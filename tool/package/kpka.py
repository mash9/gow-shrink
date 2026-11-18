from __future__ import annotations

import glob
import os
import re
import struct
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable

Progress = Callable[[int, int], None]
Blob = tuple[int, int]

PAK_MAGIC = b'KPKA'
PAK_HEADER = struct.Struct('<4sBBHII')
PAK_ENTRY = struct.Struct('<IIQQQQQ')
METHOD_STORE = 0
METHOD_DEFLATE = 1
DEFLATE_LEVEL = 6
COPY_CHUNK = 16 << 20
GAME_EXE = 're8.exe'


class PakError(Exception):
    pass


_M = 0xffffffff


def murmur3_32(data: bytes, seed: int = 0) -> int:
    h = seed
    n = len(data) // 4
    for (k,) in struct.iter_unpack('<I', data[:n * 4]):
        k = (k * 0xcc9e2d51) & _M
        k = ((k << 15) | (k >> 17)) & _M
        k = (k * 0x1b873593) & _M
        h ^= k
        h = ((h << 13) | (h >> 19)) & _M
        h = (h * 5 + 0xe6546b64) & _M
    tail = data[n * 4:]
    if tail:
        k = int.from_bytes(tail, 'little')
        k = (k * 0xcc9e2d51) & _M
        k = ((k << 15) | (k >> 17)) & _M
        k = (k * 0x1b873593) & _M
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85ebca6b) & _M
    h ^= h >> 13
    h = (h * 0xc2b2ae35) & _M
    h ^= h >> 16
    return h


def path_hash(path: str) -> tuple[int, int]:
    return (murmur3_32(path.lower().encode('utf-16le'), _M),
            murmur3_32(path.upper().encode('utf-16le'), _M))


@dataclass(frozen=True)
class Entry:
    lo: int
    hi: int
    offset: int
    csize: int
    size: int
    attr: int
    checksum: int

    @property
    def key(self) -> tuple[int, int]:
        return self.lo, self.hi

    @property
    def method(self) -> int:
        return self.attr & 0xf

    @property
    def blob(self) -> Blob:
        return self.offset, self.csize


@dataclass
class Pak:
    path: str
    major: int
    minor: int
    flags: int
    tag: int
    entries: list[Entry]
    file_size: int

    @classmethod
    def open(cls, path: str) -> Pak:
        size = os.path.getsize(path)
        name = os.path.basename(path)
        with open(path, 'rb') as f:
            head = f.read(PAK_HEADER.size)
            if len(head) < PAK_HEADER.size:
                raise PakError(f'{name}: too short')
            magic, major, minor, flags, count, tag = PAK_HEADER.unpack(head)
            if magic != PAK_MAGIC:
                raise PakError(f'{name}: not a KPKA pak')
            if major != 4 or minor != 0:
                raise PakError(f'{name}: pak version {major}.{minor} is not supported')
            if flags:
                raise PakError(f'{name}: pak flags {flags:#x} (encrypted table?) are not supported')
            raw = f.read(count * PAK_ENTRY.size)
        if len(raw) != count * PAK_ENTRY.size:
            raise PakError(f'{name}: entry table runs past the end of the file')
        entries = [Entry(*t) for t in PAK_ENTRY.iter_unpack(raw)]
        return cls(path, major, minor, flags, tag, entries, size)

    @property
    def table_end(self) -> int:
        return PAK_HEADER.size + len(self.entries) * PAK_ENTRY.size

    def blobs(self) -> list[Blob]:
        return sorted({e.blob for e in self.entries})

    def unique(self) -> list[Entry]:
        return list({e.blob: e for e in self.entries}.values())

    def check_layout(self) -> list[str]:
        errs = []
        end = self.table_end
        for off, cs in self.blobs():
            if off < end:
                errs.append(f'data at {off:#x} overlaps the previous entry or the table')
            elif off > end:
                errs.append(f'{off - end} unused bytes before {off:#x}')
            end = max(end, off + cs)
            if len(errs) > 20:
                return errs
        if end != self.file_size:
            errs.append(f'data ends at {end:#x}, file size is {self.file_size:#x}')
        for e in self.entries:
            if e.method not in (METHOD_STORE, METHOD_DEFLATE):
                errs.append(f'entry {e.lo:08x}{e.hi:08x}: compression {e.method} is not supported')
            elif e.method == METHOD_STORE and e.csize != e.size:
                errs.append(f'entry {e.lo:08x}{e.hi:08x}: stored entry with csize {e.csize} != size {e.size}')
            if len(errs) > 20:
                break
        return errs


def decode(method: int, raw: bytes, size: int) -> bytes:
    if method == METHOD_STORE:
        return raw
    if method == METHOD_DEFLATE:
        data = zlib.decompress(raw, -15)
        if len(data) != size:
            raise PakError(f'entry inflates to {len(data)} bytes, expected {size}')
        return data
    raise PakError(f'compression {method} is not supported')


def encode(method: int, data: bytes) -> bytes:
    if method == METHOD_STORE:
        return data
    co = zlib.compressobj(DEFLATE_LEVEL, zlib.DEFLATED, -15)
    return co.compress(data) + co.flush()


def read_entry(f, e: Entry) -> bytes:
    f.seek(e.offset)
    return decode(e.method, f.read(e.csize), e.size)


def read_head(f, e: Entry, n: int) -> bytes:
    if e.method == METHOD_STORE:
        f.seek(e.offset)
        return f.read(min(n, e.csize))
    take = min(e.csize, max(1024, n * 2))
    while True:
        f.seek(e.offset)
        raw = f.read(take)
        try:
            out = zlib.decompressobj(-15).decompress(raw, n)
        except zlib.error:
            return b''
        if len(out) >= min(n, e.size) or take >= e.csize:
            return out
        take = min(e.csize, take * 4)


def find_root(path: str, exe: str = GAME_EXE) -> str | None:
    d = os.path.abspath(path)
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    for _ in range(4):
        if os.path.isfile(os.path.join(d, exe)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def pak_order(path: str) -> tuple:
    name = os.path.basename(path).lower()
    in_dlc = os.path.basename(os.path.dirname(os.path.abspath(path))).lower() == 'dlc'
    m = re.match(r'(.*\.pak)\.patch_(\d+)\.pak$', name)
    if m:
        return (in_dlc, m.group(1), int(m.group(2)))
    return (in_dlc, name, -1)


def root_paks(root: str) -> list[str]:
    found = glob.glob(os.path.join(root, '*.pak')) + glob.glob(os.path.join(root, 'dlc', '*.pak'))
    return sorted((os.path.abspath(p) for p in found), key=pak_order)


def find_paks(paths: Iterable[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += root_paks(p)
        elif p.lower().endswith('.pak') and os.path.isfile(p):
            out.append(p)
    return sorted(dict.fromkeys(os.path.abspath(p) for p in out), key=pak_order)


def workers_for(workers: int | None) -> int:
    return workers or max(1, (os.cpu_count() or 2) - 1)


def write(pak: Pak, source: str, out: str, changes: dict[Blob, tuple],
          worker: Callable[[tuple], tuple[bytes, int]], progress: Progress | None = None,
          workers: int | None = None) -> dict[tuple[int, int], int]:
    blobs = pak.blobs()
    total = sum(cs for _, cs in blobs)
    placed: dict[Blob, tuple[int, int, int]] = {}
    crcs: dict[Blob, int] = {}
    sizes = {e.blob: e.size for e in pak.entries}
    done = 0
    window = max(8, workers_for(workers) * 4)
    it = iter([b for b in blobs if b in changes])
    pending: dict[Blob, object] = {}
    with open(source, 'rb') as f, open(out, 'wb') as o, \
            ProcessPoolExecutor(max_workers=workers_for(workers)) as ex:

        def fill() -> None:
            while len(pending) < window:
                b = next(it, None)
                if b is None:
                    return
                pending[b] = ex.submit(worker, changes[b])

        fill()
        o.write(b'\0' * pak.table_end)
        for b in blobs:
            pos = o.tell()
            if b in changes:
                data, usize = pending.pop(b).result()
                fill()
                o.write(data)
                placed[b] = (pos, len(data), usize)
                crcs[b] = zlib.crc32(data)
            else:
                f.seek(b[0])
                left = b[1]
                crc = 0
                while left:
                    chunk = f.read(min(left, COPY_CHUNK))
                    if not chunk:
                        raise PakError(f'{os.path.basename(source)}: unexpected end of file')
                    crc = zlib.crc32(chunk, crc)
                    o.write(chunk)
                    left -= len(chunk)
                placed[b] = (pos, b[1], sizes[b])
                crcs[b] = crc
            done += b[1]
            if progress:
                progress(done, total)
        o.seek(0)
        o.write(PAK_HEADER.pack(PAK_MAGIC, pak.major, pak.minor, pak.flags, len(pak.entries), pak.tag))
        for e in pak.entries:
            pos, cs, size = placed[e.blob]
            o.write(PAK_ENTRY.pack(e.lo, e.hi, pos, cs, size, e.attr, e.checksum))
    st = os.stat(source)
    os.utime(out, (st.st_atime, st.st_mtime))
    return {e.key: crcs[e.blob] for e in pak.entries}


Check = Callable[[bytes], 'str | None']


def _check_job(job: tuple[str, list[tuple], Check | None]) -> list[str]:
    path, items, check = job
    errs = []
    with open(path, 'rb') as f:
        for off, cs, size, attr, want, deep in items:
            f.seek(off)
            raw = f.read(cs)
            if len(raw) != cs:
                errs.append(f'data at {off:#x}: runs past the end of the file')
                continue
            if want is not None and zlib.crc32(raw) != want:
                errs.append(f'data at {off:#x}: CRC differs from what was written')
                continue
            if not deep:
                continue
            try:
                data = decode(attr & 0xf, raw, size)
            except (PakError, zlib.error) as e:
                errs.append(f'data at {off:#x}: {e}')
                continue
            msg = check(data) if check else None
            if msg:
                errs.append(f'data at {off:#x}: {msg}')
    return errs


def verify(path: str, crcs: dict | None = None, deep: bool = True, deep_keys: set | None = None,
           check: Check | None = None, progress: Progress | None = None,
           workers: int | None = None) -> list[str]:
    try:
        pak = Pak.open(path)
    except (PakError, OSError) as e:
        return [str(e)]
    errs = pak.check_layout()
    if errs:
        return errs
    if crcs is not None and {e.key for e in pak.entries} != set(crcs):
        return ['entry set differs from the source']
    seen: dict[Blob, list] = {}
    for e in pak.entries:
        d = deep or (deep_keys is not None and e.key in deep_keys)
        item = seen.setdefault(e.blob, [e.offset, e.csize, e.size, e.attr, crcs.get(e.key) if crcs else None, d])
        item[5] = item[5] or d
    items = sorted(tuple(v) for v in seen.values() if v[4] is not None or v[5])
    jobs = [(path, items[k:k + 256], check) for k in range(0, len(items), 256)]
    with ProcessPoolExecutor(max_workers=workers_for(workers)) as ex:
        for n, found in enumerate(ex.map(_check_job, jobs), 1):
            errs += found
            if progress:
                progress(n, len(jobs))
            if len(errs) > 20:
                break
    return errs
