from __future__ import annotations

import bisect
import collections
import concurrent.futures as cf
import dataclasses
import hashlib
import io
import itertools
import os
import random
import struct
import zlib
from dataclasses import dataclass, field
from typing import Callable, Iterable

MAGIC = 0x5A6F12E1
VERSION = 11
FOOTER = 221
NAME_SLOTS = 5
BLOCK = 0x10000
TASK_BYTES = 8 << 20
HEAD_PLAIN = 53
SKIP_EXTS = ('.wem', '.bnk', '.mp4', '.bk2', '.bik', '.ogg', '.opus')
METHODS = {'zlib': 'Zlib', 'lz4': 'LZ4'}
CODECS = {'zlib': 'zlib', 'lz4': 'lz4', 'oodle': 'oodle'}


class PakError(Exception):
    pass


def _fstring(r: io.BytesIO) -> str:
    n, = struct.unpack('<i', r.read(4))
    if n == 0:
        return ''
    if n < 0:
        return r.read(-2 * n).decode('utf-16le')[:-1]
    return r.read(n).decode('latin1')[:-1]


def _put_fstring(s: str) -> bytes:
    if not s:
        return struct.pack('<i', 0)
    try:
        b = s.encode('ascii') + b'\0'
        return struct.pack('<i', len(b)) + b
    except UnicodeEncodeError:
        b = s.encode('utf-16le') + b'\0\0'
        return struct.pack('<i', -(len(b) // 2)) + b


@dataclass
class Entry:
    loc: int
    offset: int
    size: int
    usize: int
    method: int
    block_size: int
    blocks: list[int]
    encrypted: bool

    def head_size(self) -> int:
        if self.method:
            return HEAD_PLAIN + 4 + 16 * len(self.blocks)
        return HEAD_PLAIN

    def data_start(self) -> int:
        return self.offset + self.head_size()


def decode_entry(enc: bytes, pos: int) -> Entry:
    flags, = struct.unpack_from('<I', enc, pos)
    p = pos + 4
    bs = flags & 0x3f
    if bs == 0x3f:
        bs, = struct.unpack_from('<I', enc, p)
        p += 4
    else:
        bs <<= 11
    nblocks = (flags >> 6) & 0xffff
    encrypted = bool(flags & (1 << 22))
    method = (flags >> 23) & 0x3f

    def num(bit: int) -> int:
        nonlocal p
        if flags & (1 << bit):
            v, = struct.unpack_from('<I', enc, p)
            p += 4
        else:
            v, = struct.unpack_from('<Q', enc, p)
            p += 8
        return v

    offset = num(31)
    usize = num(30)
    size = num(29) if method else usize
    blocks: list[int] = []
    if nblocks == 1 and not encrypted:
        blocks = [size]
    elif nblocks:
        blocks = list(struct.unpack_from(f'<{nblocks}I', enc, p))
    return Entry(pos, offset, size, usize, method, bs, blocks, encrypted)


def encode_entry(e: Entry) -> bytes:
    if e.encrypted:
        raise PakError('encrypted entries are not supported')
    if len(e.blocks) > 0xffff:
        raise PakError(f'entry at {e.offset} has too many blocks to encode')
    bs = e.block_size >> 11
    explicit = bs > 0x3f or (bs << 11) != e.block_size
    flags = (0x3f if explicit else bs) | (len(e.blocks) << 6) | (e.method << 23)
    for bit, v in ((31, e.offset), (30, e.usize), (29, e.size)):
        if v < 1 << 32:
            flags |= 1 << bit
    out = [struct.pack('<I', flags)]
    if explicit:
        out.append(struct.pack('<I', e.block_size))
    for bit, v in ((31, e.offset), (30, e.usize)):
        out.append(struct.pack('<I' if flags & (1 << bit) else '<Q', v))
    if e.method:
        out.append(struct.pack('<I' if flags & (1 << 29) else '<Q', e.size))
        if len(e.blocks) > 1:
            out.append(struct.pack(f'<{len(e.blocks)}I', *e.blocks))
    return b''.join(out)


def entry_header(e: Entry, sha1: bytes) -> bytes:
    out = [struct.pack('<QQQI', 0, e.size, e.usize, e.method), sha1]
    if e.method:
        out.append(struct.pack('<i', len(e.blocks)))
        start = e.head_size()
        for n in e.blocks:
            out.append(struct.pack('<qq', start, start + n))
            start += n
    out.append(struct.pack('<BI', 0, e.block_size if e.method else 0))
    return b''.join(out)


@dataclass
class Pak:
    path: str
    file_size: int
    guid: bytes
    index_encrypted: int
    version: int
    names: list[str]
    mount: str
    seed: int
    entry_count: int
    encoded: bytes
    files_count: int
    path_hashes: list[tuple[int, int]] | None
    pruned: list[tuple[str, list[tuple[str, int]]]]
    tree: list[tuple[str, list[tuple[str, int]]]]
    entries: dict[int, Entry] = field(default_factory=dict)

    @classmethod
    def open(cls, path: str) -> Pak:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size < FOOTER:
                raise PakError('file too small')
            f.seek(size - FOOTER)
            foot = f.read(FOOTER)
            guid, enc = foot[:16], foot[16]
            magic, version, ioff, isize = struct.unpack_from('<IIQQ', foot, 17)
            if magic != MAGIC:
                raise PakError('not a UE4 pak (bad footer magic)')
            if version != VERSION:
                raise PakError(f'pak version {version} is not supported (expected {VERSION})')
            if enc:
                raise PakError('the pak index is encrypted')
            names = [foot[61 + 32 * i:93 + 32 * i].split(b'\0')[0].decode('latin1') for i in range(NAME_SLOTS)]
            f.seek(ioff)
            prim = f.read(isize)
            if hashlib.sha1(prim).digest() != foot[41:61]:
                raise PakError('index hash mismatch')
            r = io.BytesIO(prim)
            mount = _fstring(r)
            count, seed = struct.unpack('<iQ', r.read(12))
            hashes = None
            pruned: list = []
            has_ph, = struct.unpack('<I', r.read(4))
            if has_ph:
                po, ps = struct.unpack('<qq', r.read(16))
                phh = r.read(20)
                f.seek(po)
                ph = f.read(ps)
                if hashlib.sha1(ph).digest() != phh:
                    raise PakError('path hash index hash mismatch')
                q = io.BytesIO(ph)
                n, = struct.unpack('<i', q.read(4))
                hashes = [struct.unpack('<Qi', q.read(12)) for _ in range(n)]
                pruned = _read_tree(q)
            has_dir, = struct.unpack('<I', r.read(4))
            if not has_dir:
                raise PakError('the pak has no full directory index')
            do, ds = struct.unpack('<qq', r.read(16))
            dh = r.read(20)
            f.seek(do)
            dd = f.read(ds)
            if hashlib.sha1(dd).digest() != dh:
                raise PakError('directory index hash mismatch')
            tree = _read_tree(io.BytesIO(dd))
            n, = struct.unpack('<i', r.read(4))
            encoded = r.read(n)
            files, = struct.unpack('<i', r.read(4))
        pak = cls(path, size, guid, enc, version, names, mount, seed, count, encoded, files,
                  hashes, pruned, tree)
        if files:
            raise PakError(f'{files} entries are stored outside the encoded table; not supported')
        for _, fl in tree:
            for _, loc in fl:
                if loc < 0:
                    raise PakError('directory entry points outside the encoded table')
                if loc not in pak.entries:
                    pak.entries[loc] = decode_entry(encoded, loc)
        for _, loc in hashes or ():
            if loc not in pak.entries:
                raise PakError('path hash index points at an entry missing from the directory index')
        return pak

    def files(self) -> Iterable[tuple[str, Entry]]:
        for d, fl in self.tree:
            for name, loc in fl:
                yield self.mount + d + name, self.entries[loc]

    def ordered(self) -> list[Entry]:
        return sorted(self.entries.values(), key=lambda e: e.offset)

    def data_end(self) -> int:
        es = self.ordered()
        return max((e.data_start() + e.size for e in es), default=0)


def _read_tree(r: io.BytesIO) -> list[tuple[str, list[tuple[str, int]]]]:
    n, = struct.unpack('<i', r.read(4))
    out = []
    for _ in range(n):
        d = _fstring(r)
        c, = struct.unpack('<i', r.read(4))
        fl = []
        for _ in range(c):
            name = _fstring(r)
            loc, = struct.unpack('<i', r.read(4))
            fl.append((name, loc))
        out.append((d, fl))
    return out


def _write_tree(tree, remap: dict[int, int]) -> bytes:
    out = [struct.pack('<i', len(tree))]
    for d, fl in tree:
        out.append(_put_fstring(d))
        out.append(struct.pack('<i', len(fl)))
        for name, loc in fl:
            out.append(_put_fstring(name))
            out.append(struct.pack('<i', remap[loc]))
    return b''.join(out)


def compressed_count(pak: Pak) -> int:
    return sum(1 for e in pak.entries.values() if e.method)


def skipped_locs(pak: Pak, skip_exts: tuple[str, ...]) -> set[int]:
    return {e.loc for p, e in pak.files() if p.lower().endswith(skip_exts) or e.usize == 0}


def uncompressed_targets(pak: Pak, skip_exts: tuple[str, ...] = SKIP_EXTS) -> list[Entry]:
    skip = skipped_locs(pak, skip_exts)
    return [e for e in pak.ordered() if not e.method and e.loc not in skip]


def slot_methods(names) -> tuple[str | None, ...]:
    return tuple(CODECS.get(n.lower()) if n else None for n in names)


def _codec(slots, e: Entry) -> str:
    if isinstance(slots, str):
        return slots
    if slots is None or not 0 < e.method <= len(slots) or slots[e.method - 1] is None:
        raise PakError(f'entry at {e.offset} uses an unsupported compression method index {e.method}')
    return slots[e.method - 1]


def _compress(method: str, level: int, data: bytes) -> bytes:
    if method == 'zlib':
        return zlib.compress(data, level)
    import lz4.block
    return lz4.block.compress(data, mode='high_compression', compression=level, store_size=False)


def _decompress(method: str, data: bytes, size: int) -> bytes:
    if method == 'zlib':
        return zlib.decompress(data)
    if method == 'oodle':
        import ooz
        out = ooz.decompress(data, size)
        if len(out) != size:
            raise PakError('Oodle block decompressed to the wrong size')
        return out
    import lz4.block
    return lz4.block.decompress(data, uncompressed_size=size)


_handles: dict[str, object] = {}


def _handle(path: str):
    f = _handles.get(path)
    if f is None:
        f = _handles[path] = open(path, 'rb')
    return f


def read_entry(f, e: Entry, method) -> bytes:
    f.seek(e.data_start())
    if not e.method:
        data = f.read(e.usize)
        if len(data) != e.usize:
            raise PakError(f'short read at {e.offset}')
        return data
    codec = _codec(method, e)
    out = []
    left = e.usize
    for cs in e.blocks:
        out.append(_decompress(codec, f.read(cs), min(e.block_size, left)))
        left -= len(out[-1])
    if left:
        raise PakError(f'entry at {e.offset} does not decompress to its size')
    return b''.join(out)


def _blocks(method: str, level: int, data) -> list[bytes]:
    return [_compress(method, level, data[i:i + BLOCK]) for i in range(0, len(data), BLOCK)]


def _job(job: tuple) -> list:
    path, src_method, method, level, items = job
    f = _handle(path)
    out = []
    for item in items:
        if item[0] == 'range':
            _, pos, length = item
            f.seek(pos)
            data = f.read(length)
            if len(data) != length:
                raise PakError(f'short read at {pos}')
            out.append(_blocks(method, level, data))
        else:
            _, e, cut, data, keep, patch = item
            if data is None:
                data = splice(read_entry(f, e, src_method)[cut:], keep, patch)
            sha = hashlib.sha1(data).digest()
            out.append((_blocks(method, level, data) if method and data else None, data, sha))
    return out


@dataclass(frozen=True)
class Change:
    data: bytes | None = None
    cut: int = 0
    keep: tuple[tuple[int, int], ...] | None = None
    patch: tuple[tuple[int, bytes], ...] = ()


def splice(data: bytes, keep: tuple[tuple[int, int], ...] | None = None,
           patch: tuple[tuple[int, bytes], ...] = ()) -> bytes:
    if keep is not None:
        data = b''.join(data[s:s + n] for s, n in keep)
    if patch:
        b = bytearray(data)
        for pos, v in patch:
            if pos < 0 or pos + len(v) > len(b):
                raise PakError(f'patch at {pos} is outside the entry')
            b[pos:pos + len(v)] = v
        data = bytes(b)
    return data


def _plan(pak: Pak, method: str | None, skip: set[int], changes: dict[int, Change], compress_unchanged: bool = True):
    for e in pak.ordered():
        c = changes.get(e.loc)
        if c is not None:
            yield e, 'change', [('entry', e, c.cut, c.data, c.keep, c.patch)]
        elif e.method or not method or e.loc in skip or not compress_unchanged:
            yield e, 'copy', []
        else:
            start = e.data_start()
            yield e, 'range', [('range', start + off, min(TASK_BYTES, e.usize - off))
                               for off in range(0, e.usize, TASK_BYTES)]


def _jobs(plan, source: str, src_method: str | None, method: str | None, level: int):
    items: list = []
    owners: list[tuple[int, bool]] = []
    size = 0
    for e, kind, its in plan:
        for i, it in enumerate(its):
            items.append(it)
            owners.append((e.loc, i == len(its) - 1))
            size += it[2] if it[0] == 'range' else e.usize
            if size >= TASK_BYTES:
                yield (source, src_method, method, level, items), owners
                items, owners, size = [], [], 0
    if items:
        yield (source, src_method, method, level, items), owners


def _copy(src, dst, pos: int, n: int) -> None:
    src.seek(pos)
    while n:
        b = src.read(min(n, 16 << 20))
        if not b:
            raise PakError(f'short read at {pos}')
        dst.write(b)
        n -= len(b)


def _read_hash(f, e: Entry) -> bytes:
    f.seek(e.offset)
    head = f.read(HEAD_PLAIN)
    off, size, usize, method = struct.unpack_from('<QQQI', head)
    if size != e.size or usize != e.usize or method != e.method:
        raise PakError(f'entry header at {e.offset} does not match the index')
    return head[28:48]


def pak_method(pak: Pak) -> str | None:
    if not compressed_count(pak):
        return None
    for n in pak.names:
        for k, v in METHODS.items():
            if n and v.lower() == n.lower():
                return k
    if not any(pak.names):
        raise PakError('compressed entries but no compression method names')
    return None


def output_names(pak: Pak, method: str | None) -> list[str]:
    names = [n for n in pak.names if n]
    if method and not any(n.lower() == METHODS[method].lower() for n in names):
        names.append(METHODS[method])
    if len(names) > NAME_SLOTS:
        raise PakError('no free compression method slot')
    return names


def _method_index(names: list[str], codec: str) -> int:
    for i, n in enumerate(names):
        if CODECS.get(n.lower()) == codec:
            return i + 1
    raise PakError(f'{codec} is not among the compression methods')


@dataclass
class Result:
    size: int
    stored: int
    compressed: int
    expect: dict[int, tuple[str, bytes]]
    method: str | None


def write(pak: Pak, source: str, out: str, method: str | None = 'zlib', level: int = 6,
          changes: dict[int, Change] | None = None, skip_exts: tuple[str, ...] = SKIP_EXTS,
          min_gain: float = 0.0, workers: int | None = None,
          progress: Callable[[int, int], None] | None = None, compress_unchanged: bool = True) -> Result:
    if method is not None and method not in METHODS:
        raise PakError(f'unknown method {method}')
    src_method = pak_method(pak)
    if src_method and method and method != src_method:
        raise PakError(f'the pak is compressed with {src_method}, not {method}')
    method = method or src_method
    changes = changes or {}
    src_slots = slot_methods(pak.names)
    names = output_names(pak, method)
    new_index = _method_index(names, method) if method else 0
    skip = skipped_locs(pak, skip_exts)
    total = sum(e.usize for e in pak.entries.values())
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    plan = list(_plan(pak, method, skip, changes, compress_unchanged))
    new: dict[int, Entry] = {}
    expect: dict[int, tuple[str, bytes]] = {}
    pending: dict[int, list] = collections.defaultdict(list)
    done = 0
    stored = compressed = 0
    with open(source, 'rb') as src, open(out, 'wb') as dst, \
            cf.ProcessPoolExecutor(workers) as pool:
        for e, _, _ in plan:
            expect[e.loc] = ('c' if e.method else 'u', _read_hash(src, e))
        jobs = _jobs(plan, source, src_slots, method, level)
        window: collections.deque = collections.deque()

        def fill() -> None:
            while len(window) < workers * 3:
                j = next(jobs, None)
                if j is None:
                    return
                window.append((pool.submit(_job, j[0]), j[1]))

        fill()
        ready: dict[int, list] = {}
        for e, kind, _ in plan:
            if kind != 'copy':
                while e.loc not in ready:
                    fut, owners = window.popleft()
                    for res, (loc, last) in zip(fut.result(), owners):
                        pending[loc].append(res)
                        if last:
                            ready[loc] = pending.pop(loc)
                    fill()
            here = dst.tell()
            res = ready.pop(e.loc, None)
            if kind == 'copy' and e.method:
                ne = Entry(0, here, e.size, e.usize, _method_index(names, _codec(src_slots, e)), e.block_size,
                           list(e.blocks), False)
                dst.write(entry_header(ne, expect[e.loc][1]))
                _copy(src, dst, e.data_start(), e.size)
                compressed += 1
            elif kind == 'copy':
                ne = Entry(0, here, e.usize, e.usize, 0, 0, [], False)
                dst.write(entry_header(ne, expect[e.loc][1]))
                _copy(src, dst, e.data_start(), e.usize)
                stored += 1
            else:
                if kind == 'range':
                    blocks = [b for part in res for b in part]
                    usize, raw, sha = e.usize, None, expect[e.loc][1]
                else:
                    blocks, raw, sha = res[0]
                    usize = len(raw)
                    expect[e.loc] = ('u', sha)
                csize = sum(map(len, blocks)) if blocks is not None else None
                if blocks is not None and csize < usize * (1 - min_gain):
                    ne = Entry(0, here, csize, usize, new_index, min(BLOCK, usize), [len(b) for b in blocks], False)
                    c = hashlib.sha1()
                    for b in blocks:
                        c.update(b)
                    dst.write(entry_header(ne, c.digest()))
                    for b in blocks:
                        dst.write(b)
                    compressed += 1
                else:
                    ne = Entry(0, here, usize, usize, 0, 0, [], False)
                    dst.write(entry_header(ne, sha))
                    if raw is None:
                        _copy(src, dst, e.data_start(), usize)
                    else:
                        dst.write(raw)
                    stored += 1
            new[e.loc] = ne
            done += e.usize
            if progress:
                progress(done, total)
        used = {ne.method for ne in new.values() if ne.method}
        out_names = names if used else [n for n in pak.names if n]
        if used and max(used) > len(out_names):
            raise PakError('internal error: compression method index')
        _write_index(pak, dst, new, out_names)
        size = dst.tell()
    return Result(size, stored, compressed, expect, method)


def _write_index(pak: Pak, dst, new: dict[int, Entry], names: list[str] | None) -> None:
    remap: dict[int, int] = {}
    enc = []
    pos = 0
    for loc in sorted(new):
        b = encode_entry(new[loc])
        remap[loc] = pos
        enc.append(b)
        pos += len(b)
    encoded = b''.join(enc)
    ph = b''
    if pak.path_hashes is not None:
        ph = (struct.pack('<i', len(pak.path_hashes))
              + b''.join(struct.pack('<Qi', h, remap[loc]) for h, loc in pak.path_hashes)
              + _write_tree(pak.pruned, remap))
    dd = _write_tree(pak.tree, remap)
    ioff = dst.tell()
    head = _put_fstring(pak.mount) + struct.pack('<iQ', pak.entry_count, pak.seed)
    tail = struct.pack('<i', len(encoded)) + encoded + struct.pack('<i', 0)
    ph_part = 4 + (36 if pak.path_hashes is not None else 0)
    isize = len(head) + ph_part + 40 + len(tail)
    po = ioff + isize
    do = po + len(ph)
    prim = [head]
    if pak.path_hashes is not None:
        prim.append(struct.pack('<Iqq', 1, po, len(ph)) + hashlib.sha1(ph).digest())
    else:
        prim.append(struct.pack('<I', 0))
    prim.append(struct.pack('<Iqq', 1, do, len(dd)) + hashlib.sha1(dd).digest())
    prim.append(tail)
    prim_b = b''.join(prim)
    if len(prim_b) != isize:
        raise PakError('internal error: index size')
    dst.write(prim_b)
    dst.write(ph)
    dst.write(dd)
    names = (list(pak.names if names is None else names) + [''] * NAME_SLOTS)[:NAME_SLOTS]
    foot = (pak.guid + bytes([pak.index_encrypted])
            + struct.pack('<IIQQ', MAGIC, pak.version, ioff, len(prim_b)) + hashlib.sha1(prim_b).digest()
            + b''.join(n.encode('latin1').ljust(32, b'\0') for n in names))
    if len(foot) != FOOTER:
        raise PakError('internal error: footer size')
    dst.write(foot)


def _verify_job(job: tuple) -> list[str]:
    path, method, items = job
    f = _handle(path)
    errs = []
    for name, e, (kind, want) in items:
        try:
            f.seek(e.offset)
            head = f.read(e.head_size())
            off, size, usize, m = struct.unpack_from('<QQQI', head)
            if (size, usize, m) != (e.size, e.usize, e.method):
                errs.append(f'{name}: entry header does not match the index')
                continue
            sha = hashlib.sha1()
            if e.method:
                n, = struct.unpack_from('<i', head, 48)
                if n != len(e.blocks):
                    errs.append(f'{name}: block count mismatch')
                    continue
                csha = hashlib.sha1()
                left = e.usize
                codec = _codec(method, e)
                for i, cs in enumerate(e.blocks):
                    raw = f.read(cs)
                    csha.update(raw)
                    data = _decompress(codec, raw, min(e.block_size, left))
                    if len(data) != min(e.block_size, left):
                        raise PakError(f'block {i} has the wrong size')
                    sha.update(data)
                    left -= len(data)
                if left:
                    raise PakError('blocks do not cover the entry')
                if head[28:48] != csha.digest():
                    errs.append(f'{name}: header hash does not match the compressed data')
                if kind == 'c':
                    if csha.digest() != want:
                        errs.append(f'{name}: compressed data differs from the original')
                    continue
            else:
                left = e.usize
                while left:
                    b = f.read(min(left, 16 << 20))
                    if not b:
                        raise PakError('short read')
                    sha.update(b)
                    left -= len(b)
                if kind != 'u' or head[28:48] != want:
                    errs.append(f'{name}: header hash differs from the original')
            if sha.digest() != want:
                errs.append(f'{name}: data differs from the original')
        except Exception as ex:
            errs.append(f'{name}: {type(ex).__name__}: {ex}')
    return errs


def verify(path: str, original: Pak, expect: dict[int, tuple[str, bytes]], workers: int | None = None,
           progress: Callable[[int, int], None] | None = None) -> list[str]:
    try:
        pak = Pak.open(path)
    except PakError as ex:
        return [f'cannot read the output: {ex}']
    errs = []
    if (pak.mount, pak.entry_count, pak.seed) != (original.mount, original.entry_count, original.seed):
        errs.append('mount point or entry count differs')
    old = list(original.files())
    cur = list(pak.files())
    if [p for p, _ in old] != [p for p, _ in cur]:
        return errs + ['file list differs from the original']
    if (pak.path_hashes is None) != (original.path_hashes is None):
        errs.append('path hash index presence differs')
    elif pak.path_hashes is not None:
        if [h for h, _ in pak.path_hashes] != [h for h, _ in original.path_hashes]:
            errs.append('path hashes differ')
        old_path = {e.loc: p for p, e in old}
        new_path = {e.loc: p for p, e in cur}
        for (_, nl), (_, ol) in zip(pak.path_hashes, original.path_hashes):
            if new_path.get(nl) != old_path.get(ol):
                errs.append('path hash index points at a different file than the original')
                break
    method = slot_methods(pak.names)
    if any(e.method and not (e.method <= len(method) and method[e.method - 1]) for e in pak.entries.values()):
        errs.append('unexpected compression method index')
    spans = sorted((e.offset, e.data_start() + e.size) for e in pak.entries.values())
    for (a0, a1), (b0, _) in zip(spans, spans[1:]):
        if b0 < a1:
            errs.append(f'entries overlap at {b0}')
            break
    if errs:
        return errs
    old_loc = {p: e.loc for p, e in old}
    items = [(p, e, expect[old_loc[p]]) for p, e in cur]
    seen: set[int] = set()
    uniq = []
    for it in items:
        if it[1].loc not in seen:
            seen.add(it[1].loc)
            uniq.append(it)
    uniq.sort(key=lambda it: it[1].offset)
    total = sum(it[1].usize for it in uniq)
    jobs, cur_job, size = [], [], 0
    for it in uniq:
        cur_job.append(it)
        size += it[1].usize
        if size >= TASK_BYTES * 4:
            jobs.append((path, method, cur_job))
            cur_job, size = [], 0
    if cur_job:
        jobs.append((path, method, cur_job))
    done = 0
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with cf.ProcessPoolExecutor(workers) as pool:
        for job, res in zip(jobs, pool.map(_verify_job, jobs)):
            errs.extend(res)
            done += sum(it[1].usize for it in job[2])
            if progress:
                progress(done, total)
    return errs


def find_paks(paths: Iterable[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(os.path.join(p, n) for n in os.listdir(p) if n.lower().endswith('.pak'))
        elif p.lower().endswith('.pak'):
            out.append(p)
    return out


def estimate(pak: Pak, source: str, method: str = 'zlib', level: int = 6,
             skip_exts: tuple[str, ...] = SKIP_EXTS, samples: int = 400, seed: int = 1) -> int:
    todo = uncompressed_targets(pak, skip_exts)
    targets = {e.loc for e in todo}
    fixed = sum(e.head_size() + e.size for e in pak.entries.values() if e.loc not in targets)
    total = sum(e.usize for e in todo)
    if not total:
        return fixed
    rng = random.Random(seed)
    starts = [e.data_start() for e in todo]
    sizes = [e.usize for e in todo]
    cum = list(itertools.accumulate(sizes))
    raw = comp = 0
    with open(source, 'rb') as f:
        for _ in range(samples):
            x = rng.randrange(total)
            i = bisect.bisect_right(cum, x)
            within = (x - (cum[i] - sizes[i])) // BLOCK * BLOCK
            f.seek(starts[i] + within)
            b = f.read(min(BLOCK, sizes[i] - within))
            raw += len(b)
            comp += len(_compress(method, level, b))
    heads = sum(HEAD_PLAIN + 4 + 16 * -(-e.usize // BLOCK) for e in todo)
    return fixed + heads + int(total * comp / raw)


def without(pak: Pak, paths) -> Pak:
    paths = set(paths)
    if not paths:
        return pak
    dropped: set[int] = set()
    kept: set[int] = set()
    tree = []
    found = 0
    for d, fl in pak.tree:
        nfl = []
        for name, loc in fl:
            if pak.mount + d + name in paths:
                dropped.add(loc)
                found += 1
            else:
                nfl.append((name, loc))
                kept.add(loc)
        if nfl or not fl:
            tree.append((d, nfl))
    if dropped & kept:
        raise PakError('an entry to remove is shared with a file that stays')
    pruned = [(d, [(n, l) for n, l in fl if l not in dropped]) for d, fl in pak.pruned]
    pruned = [(d, fl) for (d, fl), (_, ofl) in zip(pruned, pak.pruned) if fl or not ofl]
    hashes = None if pak.path_hashes is None else [(h, l) for h, l in pak.path_hashes if l not in dropped]
    entries = {loc: e for loc, e in pak.entries.items() if loc not in dropped}
    return dataclasses.replace(pak, entry_count=pak.entry_count - found, path_hashes=hashes, pruned=pruned,
                               tree=tree, entries=entries)
