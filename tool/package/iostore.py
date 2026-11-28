from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterable

MAGIC = b'-==--==--==--==-'
HEADER = struct.Struct('<16sB3xIIIIIIIII Q16s B3x I Q I I 40x')
FLAG_COMPRESSED = 0x01
FLAG_ENCRYPTED = 0x02
FLAG_SIGNED = 0x04
FLAG_INDEXED = 0x08
META_SIZE = 33
META_COMPRESSED = 0x01
BLOCK_ALIGN = 16
COPY_CHUNK = 8 << 20

TYPE_EXPORT_BUNDLE = 1
TYPE_BULK = 2
VERSIONS = (3, 6)
CHUNK_TYPES = {
    3: {'export': 2, 'bulk': 3, 'optional': 4, 'header': 10},
    6: {'export': TYPE_EXPORT_BUNDLE, 'bulk': TYPE_BULK, 'optional': 3},
}
NONE32 = 0xffffffff


class IoStoreError(Exception):
    pass


@dataclass
class Block:
    offset: int
    csize: int
    usize: int
    method: int


@dataclass
class Header:
    version: int
    header_size: int
    entry_count: int
    block_count: int
    block_entry_size: int
    method_count: int
    method_len: int
    block_size: int
    dir_index_size: int
    partition_count: int
    container_id: int
    key_guid: bytes
    flags: int
    seed_count: int
    partition_size: int
    nophash_count: int
    reserved7: int

    @classmethod
    def unpack(cls, b: bytes) -> 'Header':
        v = HEADER.unpack_from(b, 0)
        if v[0] != MAGIC:
            raise IoStoreError('not a .utoc file')
        return cls(*v[1:])

    def pack(self) -> bytes:
        return HEADER.pack(MAGIC, self.version, self.header_size, self.entry_count, self.block_count,
                           self.block_entry_size, self.method_count, self.method_len, self.block_size,
                           self.dir_index_size, self.partition_count, self.container_id, self.key_guid,
                           self.flags, self.seed_count, self.partition_size, self.nophash_count,
                           self.reserved7)


def _pack_block(b: Block) -> bytes:
    return (b.offset.to_bytes(5, 'little') + b.csize.to_bytes(3, 'little')
            + b.usize.to_bytes(3, 'little') + bytes([b.method]))


class Toc:
    def __init__(self, path: str):
        self.path = path
        with open(path, 'rb') as f:
            b = f.read()
        self.raw = b
        h = self.header = Header.unpack(b)
        if h.header_size != HEADER.size or h.block_entry_size != 12:
            raise IoStoreError(f'unsupported .utoc layout (header {h.header_size}, block {h.block_entry_size})')
        if h.version not in VERSIONS:
            raise IoStoreError(f'unsupported .utoc version {h.version}')
        if h.flags & (FLAG_ENCRYPTED | FLAG_SIGNED):
            raise IoStoreError('encrypted or signed containers are not supported')
        if h.partition_count != 1:
            raise IoStoreError('multi-partition containers are not supported')
        n = h.entry_count
        p = h.header_size
        self.ids = [b[p + 12 * i:p + 12 * i + 12] for i in range(n)]
        p += 12 * n
        self.ol_off = p
        self.offsets = []
        self.lengths = []
        for i in range(n):
            x = b[p + 10 * i:p + 10 * i + 10]
            self.offsets.append(int.from_bytes(x[:5], 'big'))
            self.lengths.append(int.from_bytes(x[5:], 'big'))
        p += 10 * n
        self.hash_tables = b[p:p + 4 * (h.seed_count + h.nophash_count)]
        p += 4 * (h.seed_count + h.nophash_count)
        self.blocks = []
        for i in range(h.block_count):
            x = b[p + 12 * i:p + 12 * i + 12]
            self.blocks.append(Block(int.from_bytes(x[:5], 'little'), int.from_bytes(x[5:8], 'little'),
                                     int.from_bytes(x[8:11], 'little'), x[11]))
        p += 12 * h.block_count
        self.method_names = b[p:p + h.method_len * h.method_count]
        self.methods = ['None'] + [self.method_names[i * h.method_len:(i + 1) * h.method_len]
                                   .rstrip(b'\0').decode() for i in range(h.method_count)]
        p += h.method_len * h.method_count
        self.dir_index = b[p:p + h.dir_index_size]
        p += h.dir_index_size
        self.metas = [b[p + META_SIZE * i:p + META_SIZE * (i + 1)] for i in range(n)]
        p += META_SIZE * n
        if p != len(b):
            raise IoStoreError(f'.utoc has {len(b) - p} unexpected trailing bytes')
        self.names = _directory_names(self.dir_index)

    def type_id(self, kind: str) -> int:
        t = CHUNK_TYPES[self.header.version].get(kind)
        if t is None:
            raise IoStoreError(f'chunk type {kind} is not known for .utoc version {self.header.version}')
        return t

    def chunks_of(self, kind: str) -> list[int]:
        t = self.type_id(kind)
        return [i for i in range(self.count) if self.ids[i][11] == t]

    @property
    def count(self) -> int:
        return self.header.entry_count

    def chunk_type(self, i: int) -> int:
        return self.ids[i][11]

    def block_range(self, i: int) -> range:
        bs = self.header.block_size
        o, ln = self.offsets[i], self.lengths[i]
        if ln == 0:
            return range(o // bs, o // bs)
        return range(o // bs, (o + ln - 1) // bs + 1)

    def stored_size(self, i: int) -> int:
        return sum(self.blocks[k].csize for k in self.block_range(i))

    def chunk_hash(self, i: int) -> bytes:
        return self.metas[i][:20]


def _fstring(b: bytes, p: int) -> tuple[str, int]:
    n = struct.unpack_from('<i', b, p)[0]
    p += 4
    if n == 0:
        return '', p
    if n < 0:
        return b[p:p - 2 * n].decode('utf-16-le')[:-1], p - 2 * n
    return b[p:p + n].decode('utf-8', 'replace')[:-1], p + n


def _directory_names(b: bytes) -> dict[int, str]:
    if not b:
        return {}
    mount, p = _fstring(b, 0)
    nd = struct.unpack_from('<I', b, p)[0]
    p += 4
    dirs = [struct.unpack_from('<4I', b, p + 16 * i) for i in range(nd)]
    p += 16 * nd
    nf = struct.unpack_from('<I', b, p)[0]
    p += 4
    files = [struct.unpack_from('<3I', b, p + 12 * i) for i in range(nf)]
    p += 12 * nf
    ns = struct.unpack_from('<I', b, p)[0]
    p += 4
    strs = []
    for _ in range(ns):
        s, p = _fstring(b, p)
        strs.append(s)
    out: dict[int, str] = {}
    none = 0xffffffff
    stack = [(0, mount)] if dirs else []
    while stack:
        di, prefix = stack.pop()
        while di != none:
            name, child, sibling, first = dirs[di]
            d = prefix + (strs[name] + '/' if name != none else '')
            f = first
            while f != none:
                fname, nxt, user = files[f]
                out[user] = d + strs[fname]
                f = nxt
            if child != none:
                stack.append((child, d))
            di = sibling
    return out


def prune_directory(b: bytes, remap: dict[int, int]) -> bytes:
    if not b:
        return b
    _, p = _fstring(b, 0)
    mount = b[:p]
    nd = struct.unpack_from('<I', b, p)[0]
    p += 4
    dirs = [list(struct.unpack_from('<4I', b, p + 16 * i)) for i in range(nd)]
    p += 16 * nd
    nf = struct.unpack_from('<I', b, p)[0]
    p += 4
    files = [struct.unpack_from('<3I', b, p + 12 * i) for i in range(nf)]
    p += 12 * nf
    strings = b[p:]
    kept = [i for i, f in enumerate(files) if f[2] in remap]
    new_index = {old: new for new, old in enumerate(kept)}
    new_files = []
    for i in kept:
        name, nxt, user = files[i]
        while nxt != NONE32 and nxt not in new_index:
            nxt = files[nxt][1]
        new_files.append((name, new_index.get(nxt, NONE32), remap[user]))
    for d in dirs:
        f = d[3]
        while f != NONE32 and f not in new_index:
            f = files[f][1]
        d[3] = new_index.get(f, NONE32)
    out = bytearray(mount)
    out += struct.pack('<I', len(dirs)) + b''.join(struct.pack('<4I', *d) for d in dirs)
    out += struct.pack('<I', len(new_files)) + b''.join(struct.pack('<3I', *f) for f in new_files)
    out += strings
    return bytes(out)


def _decompress(method: str, data: bytes, usize: int) -> bytes:
    if method == 'None':
        return data
    if method == 'Oodle':
        import ooz
        out = ooz.decompress(data, usize)
        if len(out) != usize:
            raise IoStoreError('Oodle block decompressed to the wrong size')
        return out
    raise IoStoreError(f'unsupported compression method {method}')


class Reader:
    def __init__(self, toc: Toc, ucas_path: str):
        self.toc = toc
        self.f = open(ucas_path, 'rb')

    def close(self) -> None:
        self.f.close()

    def __enter__(self) -> 'Reader':
        return self

    def __exit__(self, *a) -> None:
        self.close()

    def read_block(self, k: int) -> bytes:
        bl = self.toc.blocks[k]
        self.f.seek(bl.offset)
        raw = self.f.read(bl.csize)
        if len(raw) != bl.csize:
            raise IoStoreError(f'block {k} runs past the end of the .ucas')
        return _decompress(self.toc.methods[bl.method], raw, bl.usize)

    def read(self, i: int, start: int = 0) -> bytes:
        t = self.toc
        bs = t.header.block_size
        o, ln = t.offsets[i], t.lengths[i]
        if ln == 0:
            return b''
        first = (o + start) // bs
        last = (o + ln - 1) // bs
        out = bytearray()
        for k in range(first, last + 1):
            out += self.read_block(k)
        s = o + start - first * bs
        return bytes(out[s:s + ln - start])


@dataclass
class Replacement:
    data: bytes | None = None
    skip_blocks: int = 0
    new_hash: bytes | None = None
    skip_bytes: int = 0


def _align(n: int, a: int) -> int:
    return (n + a - 1) // a * a


def write(toc: Toc, ucas_path: str, out_toc: str, out_ucas: str,
          changes: dict[int, Replacement], progress: Callable[[int, int], None] | None = None,
          remove: Iterable[int] = ()) -> None:
    h = toc.header
    bs = h.block_size
    gone = set(remove)
    if gone and (h.seed_count or h.nophash_count):
        raise IoStoreError('removing chunks from a perfect-hashed container is not supported')
    if gone & set(changes):
        raise IoStoreError('a chunk cannot be both replaced and removed')
    order = sorted((i for i in range(toc.count) if i not in gone), key=lambda i: (toc.offsets[i], i))
    total = sum(toc.stored_size(i) for i in order)
    new_offsets = [0] * toc.count
    new_lengths = list(toc.lengths)
    metas = list(toc.metas)
    blocks: list[Block] = []
    pos = 0
    upos = 0
    done = 0
    with open(ucas_path, 'rb') as src, open(out_ucas, 'wb') as dst:
        def put(raw: bytes, usize: int, method: int) -> None:
            nonlocal pos
            pad = _align(pos, BLOCK_ALIGN) - pos
            if pad:
                dst.write(b'\0' * pad)
                pos += pad
            blocks.append(Block(pos, len(raw), usize, method))
            dst.write(raw)
            pos += len(raw)

        for i in order:
            rep = changes.get(i)
            first = len(blocks)
            head = 0
            if rep is not None and rep.data is not None:
                data = rep.data
                for s in range(0, len(data), bs):
                    piece = data[s:s + bs]
                    put(piece, len(piece), 0)
                new_lengths[i] = len(data)
            else:
                skip = (rep.skip_bytes or rep.skip_blocks * bs) if rep is not None else 0
                o, ln = toc.offsets[i], toc.lengths[i]
                if skip:
                    if (rep.skip_blocks and o % bs) or skip >= ln:
                        raise IoStoreError(f'chunk {i}: cannot skip {skip} bytes')
                start = o + skip
                head = start % bs
                for k in range(start // bs, (o + ln - 1) // bs + 1 if ln else start // bs):
                    bl = toc.blocks[k]
                    src.seek(bl.offset)
                    raw = src.read(bl.csize)
                    if len(raw) != bl.csize:
                        raise IoStoreError(f'block {k} runs past the end of the .ucas')
                    put(raw, bl.usize, bl.method)
                new_lengths[i] = ln - skip
            done += toc.stored_size(i)
            if rep is not None:
                meta = bytearray(metas[i])
                if rep.new_hash is None:
                    raise IoStoreError(f'chunk {i}: replacement without a hash')
                meta[:len(rep.new_hash)] = rep.new_hash
                if rep.data is not None:
                    meta[32] &= ~META_COMPRESSED & 0xff
                metas[i] = bytes(meta)
            new_offsets[i] = upos + head
            nblk = len(blocks) - first
            ln = new_lengths[i]
            if nblk != ((head + ln - 1) // bs + 1 if ln else 0):
                raise IoStoreError(f'chunk {i}: block count mismatch')
            upos += nblk * bs
            if progress:
                progress(done, total)
        tail = _align(pos, BLOCK_ALIGN) - pos
        if tail:
            dst.write(b'\0' * tail)
    if upos >= 1 << 40:
        raise IoStoreError('container too large')

    keep = [i for i in range(toc.count) if i not in gone]
    nh = Header(**vars(h))
    nh.block_count = len(blocks)
    nh.entry_count = len(keep)
    dir_index = toc.dir_index
    if gone:
        dir_index = prune_directory(dir_index, {old: new for new, old in enumerate(keep)})
    nh.dir_index_size = len(dir_index)
    out = bytearray(nh.pack())
    out += b''.join(toc.ids[i] for i in keep)
    for i in keep:
        out += new_offsets[i].to_bytes(5, 'big') + new_lengths[i].to_bytes(5, 'big')
    out += toc.hash_tables
    out += b''.join(_pack_block(b) for b in blocks)
    out += toc.method_names
    out += dir_index
    out += b''.join(metas[i] for i in keep)
    with open(out_toc, 'wb') as f:
        f.write(out)


def find_containers(paths: Iterable[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for n in sorted(files):
                    if n.lower().endswith('.utoc'):
                        out.append(os.path.join(root, n))
        elif p.lower().endswith('.utoc'):
            out.append(p)
    return list(dict.fromkeys(os.path.abspath(p) for p in out))
