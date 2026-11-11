"""Parse, verify and rewrite `.texpack` / `.texpack.toc`, removing top mip levels.

The format follows section 3 of docs/formats.md. All integers are little-endian.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import BinaryIO, Callable, Iterable

HEADER_OFF = 0x20
TEXTURE_TABLE_OFF = 0x38
TEXTURE_ENTRY_SIZE = 24
RECORD_SIZE = 32

PLAIN_HDR_SIZE = 0x20
GNF_HDR_SIZE = 0x124
GNF_CHUNK_OFF = 0x10
GNF_CHUNK_SIZE = 0x100
SUB_SIZE = 0x14  # {u32 3, u32 0, u16 top+1, u16 count, u32 size, u32 2}
KIND_PLAIN = 3
KIND_GNF = 5

COPY_CHUNK = 8 << 20

_REC = struct.Struct('<IIIIBBHHHq')
_TEX = struct.Struct('<16sQ')
_HDR = struct.Struct('<6I')
_BLK = struct.Struct('<4I')
_SUB = struct.Struct('<IIHHII')


class FormatError(Exception):
    pass


def align16(n: int) -> int:
    return (n + 15) & ~15


def pow2_ceil(n: int) -> int:
    return 1 << (n - 1).bit_length() if n > 1 else 1


@dataclass
class Record:
    table_off: int      # absolute position in the record table (source file)
    offset16: int
    payload_size: int
    stored_size: int
    unk0c: int
    top_mip: int
    bottom_mip: int
    unk12: int
    width: int
    height: int
    next: int

    @property
    def data_off(self) -> int:
        return self.offset16 * 16

    @property
    def max_dim(self) -> int:
        return max(self.width, self.height)

    def pack(self) -> bytes:
        return _REC.pack(self.offset16, self.payload_size, self.stored_size, self.unk0c,
                         self.top_mip, self.bottom_mip, self.unk12, self.width, self.height,
                         self.next)


@dataclass
class Texture:
    hash: bytes
    first: int                      # table_off of the first record
    chain: list[Record] = field(default_factory=list)  # low resolution -> high resolution


@dataclass
class Texpack:
    path: str
    file_size: int
    toc_size: int
    record_count: int
    record_table_off: int
    texture_count: int
    version: int
    unk34: int
    textures: list[Texture]
    records: list[Record]           # record table order
    toc: bytes

    @classmethod
    def open(cls, path: str) -> 'Texpack':
        file_size = os.path.getsize(path)
        with open(path, 'rb') as f:
            head = f.read(TEXTURE_TABLE_OFF)
            if len(head) < TEXTURE_TABLE_OFF:
                raise FormatError(f'{path}: file is too small')
            toc_size, rc, rto, tc, ver, unk34 = _HDR.unpack_from(head, HEADER_OFF)
            if toc_size > file_size:
                raise FormatError(f'{path}: toc_size ({toc_size}) exceeds file size')
            f.seek(0)
            toc = f.read(toc_size)
        if rto != TEXTURE_TABLE_OFF + tc * TEXTURE_ENTRY_SIZE:
            raise FormatError(f'{path}: record_table_off mismatch')
        if rto + rc * RECORD_SIZE > toc_size:
            raise FormatError(f'{path}: record table extends past toc')

        records = []
        by_off = {}
        for i in range(rc):
            o = rto + i * RECORD_SIZE
            r = Record(o, *_REC.unpack_from(toc, o))
            records.append(r)
            by_off[o] = r

        textures = []
        seen = set()
        for i in range(tc):
            h, first = _TEX.unpack_from(toc, TEXTURE_TABLE_OFF + i * TEXTURE_ENTRY_SIZE)
            t = Texture(h, first)
            o = first
            while o != -1:
                r = by_off.get(o)
                if r is None:
                    raise FormatError(f'{path}: chain of texture {i} points to invalid offset {o:#x}')
                if o in seen:
                    raise FormatError(f'{path}: record {o:#x} is referenced twice')
                seen.add(o)
                t.chain.append(r)
                o = r.next
            if not t.chain:
                raise FormatError(f'{path}: texture {i} has no records')
            textures.append(t)
        if len(seen) != rc:
            raise FormatError(f'{path}: {rc - len(seen)} records do not belong to any chain')

        return cls(path, file_size, toc_size, rc, rto, tc, ver, unk34, textures, records, toc)

    @property
    def data_size(self) -> int:
        return self.file_size - self.toc_size


# --------------------------------------------------------------------------
# Block header / GNF


@dataclass
class Gnf:
    raw: bytearray      # 0x100-byte GNF chunk

    @property
    def stream_size(self) -> int:
        return struct.unpack_from('<I', self.raw, 12)[0]

    def dw(self, i: int) -> int:
        return struct.unpack_from('<I', self.raw, 16 + i * 4)[0]

    def set_dw(self, i: int, v: int) -> None:
        struct.pack_into('<I', self.raw, 16 + i * 4, v)

    @property
    def width(self) -> int:
        return (self.dw(2) & 0x3fff) + 1

    @property
    def height(self) -> int:
        return ((self.dw(2) >> 14) & 0x3fff) + 1

    @property
    def base_level(self) -> int:
        return (self.dw(3) >> 12) & 0xf

    @property
    def last_level(self) -> int:
        return (self.dw(3) >> 16) & 0xf

    @property
    def pitch(self) -> int:
        return ((self.dw(4) >> 13) & 0x3fff) + 1

    @property
    def data_size(self) -> int:
        return self.dw(7)

    def user_hash(self) -> bytes:
        return bytes(self.raw[0x38:0x40])

    def resized(self, width: int, height: int, last_level: int, pitch: int,
                total: int) -> 'Gnf':
        g = Gnf(bytearray(self.raw))
        struct.pack_into('<I', g.raw, 12, total + GNF_CHUNK_SIZE)
        dw2 = g.dw(2) & ~0x0fffffff
        g.set_dw(2, dw2 | (width - 1) | ((height - 1) << 14))
        g.set_dw(3, (g.dw(3) & ~(0xf << 16)) | (last_level << 16))
        g.set_dw(4, (g.dw(4) & ~(0x3fff << 13)) | ((pitch - 1) << 13))
        g.set_dw(7, total)
        return g


@dataclass
class BlockHeader:
    one: int
    header_size: int
    stored_size: int
    kind: int
    gnf: Gnf | None
    sub: tuple          # (3, 0, top+1, count, size, 2)

    @classmethod
    def parse(cls, buf: bytes) -> 'BlockHeader':
        one, hsz, stored, kind = _BLK.unpack_from(buf)
        if hsz == PLAIN_HDR_SIZE:
            return cls(one, hsz, stored, kind, None, _SUB.unpack_from(buf, 0x0C))
        if hsz == GNF_HDR_SIZE:
            gnf = Gnf(bytearray(buf[GNF_CHUNK_OFF:GNF_CHUNK_OFF + GNF_CHUNK_SIZE]))
            return cls(one, hsz, stored, kind, gnf,
                       _SUB.unpack_from(buf, GNF_CHUNK_OFF + GNF_CHUNK_SIZE))
        raise FormatError(f'unknown block header size {hsz:#x}')


def read_block_header(f: BinaryIO, rec: Record) -> BlockHeader:
    f.seek(rec.data_off)
    return BlockHeader.parse(f.read(GNF_HDR_SIZE))


def build_gnf_block_header(gnf: Gnf, sub: tuple, payload_size: int) -> bytes:
    stored = align16(payload_size + GNF_HDR_SIZE)
    return (_BLK.pack(1, GNF_HDR_SIZE, stored, KIND_GNF) + bytes(gnf.raw) + _SUB.pack(*sub))


# --------------------------------------------------------------------------
# Verification


def verify(path: str, toc_path: str | None = None, deep: bool = True) -> list[str]:
    """Return a list of problems. An empty list means the file is valid."""
    errors: list[str] = []
    try:
        tp = Texpack.open(path)
    except FormatError as e:
        return [str(e)]

    if toc_path is None:
        toc_path = path + '.toc'
    if os.path.exists(toc_path):
        with open(toc_path, 'rb') as f:
            if f.read() != tp.toc:
                errors.append(f'{toc_path}: does not match the head of the pack')
    else:
        errors.append(f'{toc_path}: file not found')

    if tp.version != 5:
        errors.append(f'version {tp.version} (expected 5)')
    if tp.toc_size != align16(tp.record_table_off + tp.record_count * RECORD_SIZE):
        errors.append('toc_size differs from the 16-aligned end of the record table')

    # Data blocks must run contiguously from toc_size to the end of the file
    pos = tp.toc_size
    for r in sorted(tp.records, key=lambda r: r.offset16):
        if r.data_off != pos:
            errors.append(f'record {r.table_off:#x}: block at {r.data_off:#x}, expected {pos:#x}')
            break
        pos += r.stored_size
    else:
        if pos != tp.file_size:
            errors.append(f'data end {pos:#x} != file size {tp.file_size:#x}')

    if not deep:
        return errors

    with open(path, 'rb') as f:
        for ti, t in enumerate(tp.textures):
            total = sum(r.payload_size for r in t.chain)
            for k, r in enumerate(t.chain):
                where = f'texture {ti} record {k}'
                if k > 0:
                    p = t.chain[k - 1]
                    if r.top_mip != p.top_mip + 1 or r.bottom_mip != r.top_mip:
                        errors.append(f'{where}: mip levels are not contiguous')
                    if r.width != p.width * 2 and p.width != 1:
                        errors.append(f'{where}: width does not double')
                    if r.height != p.height * 2 and p.height != 1:
                        errors.append(f'{where}: height does not double')
                try:
                    bh = read_block_header(f, r)
                except FormatError as e:
                    errors.append(f'{where}: {e}')
                    continue
                last = k == len(t.chain) - 1
                if bh.stored_size != r.stored_size:
                    errors.append(f'{where}: block stored_size mismatch')
                if r.stored_size != align16(r.payload_size + bh.header_size):
                    errors.append(f'{where}: stored_size != align16(payload + header)')
                if bh.sub != (3, 0, r.top_mip + 1, r.top_mip - r.bottom_mip + 1,
                              r.payload_size, 2):
                    errors.append(f'{where}: block sub-header mismatch {bh.sub}')
                if last != (bh.gnf is not None):
                    errors.append(f'{where}: GNF header must be on the top record only')
                if bh.gnf is not None:
                    g = bh.gnf
                    if bh.kind != KIND_GNF:
                        errors.append(f'{where}: GNF block kind {bh.kind}')
                    if (g.width, g.height) != (r.width, r.height):
                        errors.append(f'{where}: GNF resolution {g.width}x{g.height} != '
                                      f'{r.width}x{r.height}')
                    if g.base_level != 0 or g.last_level != r.top_mip:
                        errors.append(f'{where}: GNF mip range {g.base_level}..{g.last_level}')
                    if g.pitch != pow2_ceil(g.width):
                        errors.append(f'{where}: GNF pitch {g.pitch}')
                    if g.data_size != total or g.stream_size != total + GNF_CHUNK_SIZE:
                        errors.append(f'{where}: GNF size fields mismatch')
                    if g.user_hash() != t.hash[8:]:
                        errors.append(f'{where}: USER hash mismatch')
                elif bh.kind != KIND_PLAIN:
                    errors.append(f'{where}: plain block kind {bh.kind}')
            if len(errors) > 50:
                errors.append('... (truncated)')
                break
    return errors


# --------------------------------------------------------------------------
# Removal plan


@dataclass
class DropPolicy:
    """Decide how many top records to remove from each texture.

    drop:     number of records to remove, starting from the top
    max_res:  remove until max(width, height) is at most this value
    min_res:  never let the remaining top resolution (max side) fall below this value
    When drop and max_res are both given, whichever removes more wins.
    At least one record always remains in each chain.
    """
    drop: int = 0
    max_res: int | None = None
    min_res: int = 0

    def count(self, chain: list[Record]) -> int:
        n = self.drop
        if self.max_res is not None:
            m = 0
            while m < len(chain) - 1 and chain[len(chain) - 1 - m].max_dim > self.max_res:
                m += 1
            n = max(n, m)
        n = min(n, len(chain) - 1)
        while n > 0 and chain[len(chain) - 1 - n].max_dim < self.min_res:
            n -= 1
        return n


@dataclass
class Plan:
    tp: Texpack
    drops: list[int]                # records removed per texture

    @property
    def removed(self) -> list[Record]:
        out = []
        for t, n in zip(self.tp.textures, self.drops):
            if n:
                out.extend(t.chain[-n:])
        return out

    @property
    def new_record_count(self) -> int:
        return self.tp.record_count - sum(self.drops)

    @property
    def new_toc_size(self) -> int:
        return align16(self.tp.record_table_off + self.new_record_count * RECORD_SIZE)

    def new_file_size(self) -> int:
        size = self.new_toc_size
        for t, n in zip(self.tp.textures, self.drops):
            kept = t.chain[:len(t.chain) - n]
            for r in kept[:-1]:
                size += r.stored_size
            top = kept[-1]
            size += align16(top.payload_size + GNF_HDR_SIZE) if n else top.stored_size
        return size


def make_plan(tp: Texpack, policy: DropPolicy) -> Plan:
    return Plan(tp, [policy.count(t.chain) for t in tp.textures])


# --------------------------------------------------------------------------
# Rewrite


def _copy(src: BinaryIO, dst: BinaryIO, off: int, size: int) -> None:
    src.seek(off)
    while size:
        chunk = src.read(min(size, COPY_CHUNK))
        if not chunk:
            raise FormatError('source file is shorter than expected')
        dst.write(chunk)
        size -= len(chunk)


def write(plan: Plan, out_pack: str, out_toc: str,
          progress: Callable[[int, int], None] | None = None) -> None:
    tp = plan.tp

    # Records to keep, new top records, and the old top records whose GNF moves
    removed = {r.table_off for r in plan.removed}
    new_top: dict[int, tuple[Texture, Record]] = {}   # new top table_off -> (texture, old top)
    for t, n in zip(tp.textures, plan.drops):
        if n:
            new_top[t.chain[-1 - n].table_off] = (t, t.chain[-1])

    kept = [r for r in tp.records if r.table_off not in removed]
    new_off = {r.table_off: tp.record_table_off + i * RECORD_SIZE for i, r in enumerate(kept)}
    toc_size = plan.new_toc_size

    with open(tp.path, 'rb') as src:
        # Build the new top block headers up front
        gnf_headers: dict[int, bytes] = {}
        for off, (t, old_top) in new_top.items():
            r = next(x for x in t.chain if x.table_off == off)
            old = read_block_header(src, old_top)
            cur = read_block_header(src, r)
            if old.gnf is None or cur.gnf is not None:
                raise FormatError(f'{tp.path}: GNF header not where expected ({off:#x})')
            if old.gnf.pitch != pow2_ceil(old.gnf.width):
                raise FormatError(f'{tp.path}: texture does not follow the pitch rule ({off:#x})')
            idx = t.chain.index(r)
            total = sum(x.payload_size for x in t.chain[:idx + 1])
            gnf = old.gnf.resized(r.width, r.height, r.top_mip, pow2_ceil(r.width), total)
            gnf_headers[off] = build_gnf_block_header(gnf, cur.sub, r.payload_size)

        # Lay out data blocks (original order preserved)
        layout = []      # (record, new_offset, new_stored)
        pos = toc_size
        for r in sorted(kept, key=lambda r: r.offset16):
            stored = (align16(r.payload_size + GNF_HDR_SIZE) if r.table_off in gnf_headers
                      else r.stored_size)
            layout.append((r, pos, stored))
            pos += stored
        total_size = pos

        new_recs: dict[int, Record] = {}
        for r, o, stored in layout:
            nr = Record(**vars(r))
            nr.offset16 = o // 16
            nr.stored_size = stored
            nr.next = -1 if (r.table_off in new_top or r.next == -1) else new_off[r.next]
            new_recs[r.table_off] = nr

        toc = bytearray(toc_size)
        toc[:HEADER_OFF] = tp.toc[:HEADER_OFF]
        _HDR.pack_into(toc, HEADER_OFF, toc_size, len(kept), tp.record_table_off,
                       tp.texture_count, tp.version, tp.unk34)
        for i, t in enumerate(tp.textures):
            _TEX.pack_into(toc, TEXTURE_TABLE_OFF + i * TEXTURE_ENTRY_SIZE,
                           t.hash, new_off[t.first])
        for r in kept:
            toc[new_off[r.table_off]:new_off[r.table_off] + RECORD_SIZE] = \
                new_recs[r.table_off].pack()
        toc = bytes(toc)

        with open(out_pack, 'wb') as dst:
            dst.write(toc)
            done = 0
            for r, o, stored in layout:
                hdr = gnf_headers.get(r.table_off)
                if hdr is None:
                    _copy(src, dst, r.data_off, r.stored_size)
                else:
                    dst.write(hdr)
                    _copy(src, dst, r.data_off + PLAIN_HDR_SIZE, r.payload_size)
                    dst.write(b'\0' * (stored - GNF_HDR_SIZE - r.payload_size))
                done += stored
                if progress:
                    progress(done, total_size - toc_size)
            if dst.tell() != total_size:
                raise FormatError('output size differs from the computed size')

    with open(out_toc, 'wb') as f:
        f.write(toc)


def iter_texpacks(paths: Iterable[str]) -> list[str]:
    out = []
    for p in paths:
        if os.path.isdir(p):
            out.extend(sorted(os.path.join(p, n) for n in os.listdir(p)
                              if n.endswith('.texpack')))
        else:
            out.append(p)
    return out
