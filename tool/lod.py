"""Alias LOD0 to LOD1 in `*_smsh_data` mesh entries of `.wad` files.

Each mesh entry holds an array of LOD records (stride 0xC0..0x100). A record starts with
three FLT_MAX floats 0x58 bytes before the lodpack entry hash; the hash is followed by
`u16 lod_index, u16 0x0008`. Copying the LOD1 record over the LOD0 record (keeping the
LOD index) makes the engine draw LOD1 where it would have drawn LOD0. The lodpack itself
is not modified here.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import struct
from dataclasses import dataclass

WAD_ENTRY_HEADER = 0x60
RECORD_START_BEFORE_HASH = 0x58
TRIPLE_FLT_MAX = b'\xff\xff\x7f\x7f' * 3

_LOD_REF = re.compile(rb'([\x00-\xff]{8})([\x00-\x0f])\x00\x08\x00', re.S)


class LodError(Exception):
    pass


def load_lod_hashes(directory: str) -> dict[bytes, int]:
    """Map every lodpack entry hash in `directory` to its data size."""
    out: dict[bytes, int] = {}
    for path in glob.glob(os.path.join(directory, '*.lodpack')):
        with open(path, 'rb') as f:
            group_count, entry_count, _, _ = struct.unpack('<4I', f.read(16))
            f.seek(16 + group_count * 24)
            for _ in range(entry_count):
                _, _, h, size, _ = struct.unpack('<II8sII', f.read(24))
                out[h] = size
    return out


def iter_wad_entries(buf):
    """Yield (offset, tag, size, name) for each wad entry."""
    p, n = 0, len(buf)
    while p + WAD_ENTRY_HEADER <= n:
        tag, _, size = struct.unpack_from('<HHI', buf, p)
        name = bytes(buf[p + 0x18:p + 0x50]).split(b'\0')[0]
        yield p, tag, size, name
        p = (p + WAD_ENTRY_HEADER + size + 15) & ~15


@dataclass
class Patch:
    entry_name: str
    dst: int          # absolute file offset of the LOD0 record
    src: int          # absolute file offset of the LOD1 record
    length: int       # record stride
    lod0_size: int
    lod1_size: int


def find_patches(buf, lod: dict[bytes, int], stats: dict | None = None) -> list[Patch]:
    """Find LOD0 records that can safely be replaced by their LOD1 record."""
    def count(key):
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    patches: list[Patch] = []
    for off, _, size, name in iter_wad_entries(buf):
        if not name.endswith(b'smsh_data'):
            continue
        base = off + WAD_ENTRY_HEADER
        body = bytes(buf[base:base + size])
        refs = [(m.start(), m.group(1), m.group(2)[0]) for m in _LOD_REF.finditer(body)
                if m.group(1) in lod]
        i = 0
        while i < len(refs):
            if refs[i][2] != 0:
                i += 1
                continue
            run = [refs[i]]
            j = i + 1
            while j < len(refs) and refs[j][2] == run[-1][2] + 1:
                run.append(refs[j])
                j += 1
            i = j
            if len(run) < 2:
                count('single LOD')
                continue
            stride = run[1][0] - run[0][0]
            if any(b[0] - a[0] != stride for a, b in zip(run, run[1:])):
                count('uneven stride')
                continue
            starts = [h - RECORD_START_BEFORE_HASH for h, _, _ in run]
            if any(s < 0 or body[s:s + 12] != TRIPLE_FLT_MAX for s in starts):
                count('record start not found')
                continue
            size0, size1 = lod[run[0][1]], lod[run[1][1]]
            if size1 >= size0:
                count('LOD1 not smaller')
                continue
            count('patched')
            patches.append(Patch(name.decode(errors='replace'), base + starts[0], base + starts[1],
                                 stride, size0, size1))
    return patches


def _patched_record(buf, p: Patch) -> bytes:
    rec = bytearray(buf[p.src:p.src + p.length])
    # keep the LOD index of the destination slot (u16 right after the hash)
    idx_off = RECORD_START_BEFORE_HASH + 8
    rec[idx_off:idx_off + 2] = buf[p.dst + idx_off:p.dst + idx_off + 2]
    return bytes(rec)


def write_patched(src: str, dst: str, patches: list[Patch]) -> None:
    """Copy `src` to `dst` and apply the patches to the copy."""
    with open(src, 'rb') as f:
        buf = f.read()
    shutil.copyfile(src, dst)
    with open(dst, 'r+b') as f:
        for p in patches:
            f.seek(p.dst)
            f.write(_patched_record(buf, p))


def lodpack_entries(directory: str) -> dict[bytes, tuple]:
    """Map entry hash -> (lodpack name, absolute data offset, size, group hash)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(directory, '*.lodpack'))):
        name = os.path.basename(path)
        with open(path, 'rb') as f:
            group_count, entry_count, _, _ = struct.unpack('<4I', f.read(16))
            groups = [struct.unpack('<Q8sII', f.read(24)) for _ in range(group_count)]
            for _ in range(entry_count):
                gi, go, h, size, _ = struct.unpack('<II8sII', f.read(24))
                out[h] = (name, groups[gi][0] + go, size, groups[gi][1])
    return out


def lodpack_group_hashes(directory: str) -> set[bytes]:
    out = set()
    for path in glob.glob(os.path.join(directory, '*.lodpack')):
        with open(path, 'rb') as f:
            group_count, _, _, _ = struct.unpack('<4I', f.read(16))
            for _ in range(group_count):
                out.add(struct.unpack('<Q8sII', f.read(24))[1])
    return out


def replaced_lod0(original_wads: list[str], sizes: dict[bytes, int]) -> dict[bytes, set[bytes]]:
    """LOD0 hash -> LOD1 hashes that replaced it, computed from the unpatched wads."""
    pairs: dict[bytes, set[bytes]] = {}
    for w in original_wads:
        with open(w, 'rb') as f:
            buf = f.read()
        for p in find_patches(buf, sizes):
            h0 = buf[p.dst + RECORD_START_BEFORE_HASH:p.dst + RECORD_START_BEFORE_HASH + 8]
            h1 = buf[p.src + RECORD_START_BEFORE_HASH:p.src + RECORD_START_BEFORE_HASH + 8]
            pairs.setdefault(h0, set()).add(h1)
    return pairs


def referenced_hashes(wads: list[str], watch: set[bytes], progress=None) -> dict[bytes, int]:
    """Count raw occurrences of `watch` hashes in wads, ignoring WAD_* index entries."""
    import numpy as np

    arr = np.array(sorted(int.from_bytes(h, 'little') for h in watch), dtype='<u8')
    counts: dict[bytes, int] = {}
    for k, w in enumerate(wads):
        with open(w, 'rb') as f:
            buf = f.read()
        keep = np.ones(len(buf), dtype=bool)
        for off, _, size, name in iter_wad_entries(buf):
            if name.startswith(b'WAD_'):
                keep[off:off + WAD_ENTRY_HEADER + size] = False
        for shift in range(8):
            n8 = (len(buf) - shift) // 8
            if n8 <= 0:
                continue
            v = np.frombuffer(buf, dtype='<u8', count=n8, offset=shift)
            idx = np.searchsorted(arr, v)
            idx[idx >= len(arr)] = 0
            for j in np.nonzero(arr[idx] == v)[0]:
                o = shift + int(j) * 8
                if keep[o]:
                    h = bytes(buf[o:o + 8])
                    counts[h] = counts.get(h, 0) + 1
        if progress:
            progress(k + 1, len(wads))
    return counts


# --------------------------------------------------------------------------
# NTFS sparse ranges

FSCTL_SET_SPARSE = 0x000900C4
FSCTL_SET_ZERO_DATA = 0x000980C8


def _win_handle(path: str):
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                    wintypes.LPVOID]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k32.CreateFileW(path, 0xC0000000, 0, None, 3, 0x80, None)  # RW, open existing
    if h == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), f'cannot open {path}')
    return k32, h


def zero_ranges_sparse(path: str, ranges: list[tuple[int, int]]) -> None:
    """Mark `path` sparse and zero (deallocate) each (offset, length) range."""
    import ctypes
    from ctypes import wintypes

    k32, h = _win_handle(path)
    try:
        ret = wintypes.DWORD()
        if not k32.DeviceIoControl(h, FSCTL_SET_SPARSE, None, 0, None, 0, ctypes.byref(ret), None):
            raise OSError(ctypes.get_last_error(), 'FSCTL_SET_SPARSE failed')
        for off, length in ranges:
            info = (ctypes.c_longlong * 2)(off, off + length)
            if not k32.DeviceIoControl(h, FSCTL_SET_ZERO_DATA, info, ctypes.sizeof(info), None, 0,
                                       ctypes.byref(ret), None):
                raise OSError(ctypes.get_last_error(), f'FSCTL_SET_ZERO_DATA failed at {off:#x}')
    finally:
        k32.CloseHandle(h)


def size_on_disk(path: str) -> int:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    high = wintypes.DWORD()
    k32.GetCompressedFileSizeW.restype = wintypes.DWORD
    low = k32.GetCompressedFileSizeW(path, ctypes.byref(high))
    return (high.value << 32) | low


def verify_zeroed(original: str, modified: str, ranges: list[tuple[int, int]]) -> list[str]:
    """Bytes inside `ranges` must be zero, all other bytes must equal the original."""
    if os.path.getsize(original) != os.path.getsize(modified):
        return ['file size changed']
    marks = sorted(ranges)
    chunk = 8 << 20
    with open(original, 'rb') as a, open(modified, 'rb') as b:
        pos = 0
        i = 0
        size = os.path.getsize(original)
        while pos < size:
            n = min(chunk, size - pos)
            x = bytearray(a.read(n))
            y = b.read(n)
            while i < len(marks) and marks[i][0] + marks[i][1] <= pos:
                i += 1
            j = i
            while j < len(marks) and marks[j][0] < pos + n:
                s = max(marks[j][0], pos) - pos
                e = min(marks[j][0] + marks[j][1], pos + n) - pos
                x[s:e] = bytes(e - s)
                j += 1
            if bytes(x) != y:
                return [f'unexpected bytes in chunk at {pos:#x}']
            pos += n
    return []


def verify_patched(original: str, patched: str, patches: list[Patch]) -> list[str]:
    """Check that only the expected records changed and that they match LOD1."""
    errors = []
    if os.path.getsize(original) != os.path.getsize(patched):
        return ['file size changed']
    with open(original, 'rb') as f:
        a = f.read()
    with open(patched, 'rb') as f:
        b = f.read()
    expected = bytearray(a)
    for p in patches:
        expected[p.dst:p.dst + p.length] = _patched_record(a, p)
    if bytes(expected) != b:
        errors.append('patched file differs from the expected result')
    # The patched file must still parse as a wad with the same entries
    if [e[:3] for e in iter_wad_entries(a)] != [e[:3] for e in iter_wad_entries(b)]:
        errors.append('wad entry layout changed')
    return errors
