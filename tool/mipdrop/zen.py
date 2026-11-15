from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

SUMMARY_OFFSETS = range(0x18, 0x34, 4)
NAME_BATCH_OFF = 0x34
BULK_ENTRY = struct.Struct('<qqqII')
EXPORT_ENTRY_SIZE = 72
INLINE_FLAG = 0x40
SEPARATE_FLAG = 0x100
MIP_REC_SIZE = 16
_PF = re.compile(rb'PF_[A-Za-z0-9_]{1,40}\x00')


class ZenError(Exception):
    pass


@dataclass
class BulkEntry:
    offset: int
    dup: int
    size: int
    flags: int
    pad: int

    @property
    def inline(self) -> bool:
        return bool(self.flags & INLINE_FLAG)

    def pack(self) -> bytes:
        return BULK_ENTRY.pack(self.offset, self.dup, self.size, self.flags, self.pad)


@dataclass
class Mip:
    rec_off: int
    index: int
    width: int
    height: int
    depth: int
    inline: bool
    size: int

    @property
    def rec_len(self) -> int:
        return MIP_REC_SIZE + (self.size if self.inline else 0)

    @property
    def max_dim(self) -> int:
        return max(self.width, self.height)


@dataclass
class TexturePackage:
    data: bytes
    header_size: int
    bulk_map_off: int
    bulk: list[BulkEntry]
    export_map_off: int
    export_off: int
    export_size: int
    skip_loc: int
    pd_off: int
    pixel_format: str
    num_mips_off: int
    mips: list[Mip] = field(default_factory=list)

    @property
    def bulk_map_end(self) -> int:
        return self.bulk_map_off + 8 + BULK_ENTRY.size * len(self.bulk)

    @property
    def width(self) -> int:
        return self.mips[0].width

    @property
    def height(self) -> int:
        return self.mips[0].height

    @property
    def streamed(self) -> list[Mip]:
        return [m for m in self.mips if not m.inline]

    @property
    def streamed_size(self) -> int:
        return sum(m.size for m in self.streamed)


def _u32(b: bytes, p: int) -> int:
    return struct.unpack_from('<I', b, p)[0]


def _i32(b: bytes, p: int) -> int:
    return struct.unpack_from('<i', b, p)[0]


def _i64(b: bytes, p: int) -> int:
    return struct.unpack_from('<q', b, p)[0]


def _names(b: bytes) -> tuple[list[str], int]:
    count = _u32(b, NAME_BATCH_OFF)
    if count == 0:
        return [], NAME_BATCH_OFF + 4
    nbytes = _u32(b, NAME_BATCH_OFF + 4)
    p = NAME_BATCH_OFF + 16 + 8 * count
    heads = [struct.unpack_from('>H', b, p + 2 * i)[0] for i in range(count)]
    p += 2 * count
    end = p + nbytes
    names = []
    for h in heads:
        n = h & 0x7fff
        if h & 0x8000:
            names.append(b[p:p + 2 * n].decode('utf-16-le', 'replace'))
            p += 2 * n
        else:
            names.append(b[p:p + n].decode('latin-1'))
            p += n
    if p != end:
        raise ZenError('name batch size mismatch')
    return names, end


def parse(data: bytes, ubulk_size: int | None = None) -> TexturePackage:
    b = data
    if len(b) < 0x40 or _u32(b, 0) != 0:
        raise ZenError('unsupported package summary (versioning info present)')
    header_size = _u32(b, 4)
    names, p = _names(b)
    pad = _i64(b, p)
    if not 0 <= pad < 8:
        raise ZenError('bad name batch padding')
    p += 8 + pad
    bulk_map_off = p
    map_size = _i64(b, p)
    if map_size < 0 or map_size % BULK_ENTRY.size:
        raise ZenError('bad bulk data map size')
    bulk = [BulkEntry(*BULK_ENTRY.unpack_from(b, p + 8 + i * BULK_ENTRY.size))
            for i in range(map_size // BULK_ENTRY.size)]
    map_end = p + 8 + map_size
    if _i32(b, 0x18) != map_end:
        raise ZenError('bulk data map is not followed by the import hashes')
    export_map_off = _i32(b, 0x20)
    n_exports = (_i32(b, 0x24) - export_map_off) // EXPORT_ENTRY_SIZE
    if n_exports != 1:
        raise ZenError(f'{n_exports} exports')
    serial_off, serial_size = struct.unpack_from('<QQ', b, export_map_off)
    export_off = header_size + serial_off
    export_end = export_off + serial_size
    if export_end > len(b):
        raise ZenError('export runs past the chunk')
    if 'None' not in names:
        raise ZenError('no None name')
    none_idx = names.index('None')

    found = None
    for m in _PF.finditer(b, export_off, export_end):
        s = m.start() - 4
        if s - 12 < export_off or _i32(b, s) != m.end() - m.start():
            continue
        pf = m.group()[:-1].decode()
        if pf not in names:
            continue
        pf_idx = names.index(pf)
        loc = None
        for q in range(s - 12 - 8, max(export_off, s - 12 - 64) - 1, -1):
            if _u32(b, q - 8) == pf_idx and _u32(b, q - 4) == 0:
                skip = _i64(b, q)
                t = q + skip
                if export_off < t <= export_end - 8 and _u32(b, t) == none_idx and _u32(b, t + 4) == 0:
                    loc = q
                    break
        if loc is None:
            continue
        if found is not None:
            raise ZenError('several platform data blocks')
        found = (s, pf, loc)
    if found is None:
        raise ZenError('no texture platform data')
    s, pf, skip_loc = found
    pd_off = s - 12
    sx, sy, packed = struct.unpack_from('<iiI', b, pd_off)
    if packed & ~0x3fffffff or packed & 0x3fffffff != 1:
        raise ZenError(f'unsupported packed data {packed:#x}')
    p = s + 4 + _i32(b, s)
    if _i32(b, p) != 0:
        raise ZenError('first mip to serialize is not 0')
    num_mips_off = p + 4
    n = _i32(b, num_mips_off)
    if not 0 < n <= 16:
        raise ZenError(f'bad mip count {n}')
    p = num_mips_off + 4
    mips = []
    for i in range(n):
        idx = _i32(b, p)
        if not 0 <= idx < len(bulk):
            raise ZenError('mip bulk index out of range')
        e = bulk[idx]
        inline = e.inline
        if inline == bool(e.flags & SEPARATE_FLAG):
            raise ZenError(f'unexpected bulk flags {e.flags:#x}')
        q = p + 4 + (e.size if inline else 0)
        w, h, d = struct.unpack_from('<iii', b, q)
        mips.append(Mip(p, idx, w, h, d, inline, e.size))
        p = q + 12
    tex = TexturePackage(b, header_size, bulk_map_off, bulk, export_map_off, export_off, serial_size,
                         skip_loc, pd_off, pf, num_mips_off, mips)
    _check(tex, sx, sy, ubulk_size)
    return tex


def _check(tex: TexturePackage, sx: int, sy: int, ubulk_size: int | None) -> None:
    mips = tex.mips
    if (sx, sy) != (mips[0].width, mips[0].height):
        raise ZenError('platform size does not match mip 0')
    if sorted(m.index for m in mips) != list(range(len(tex.bulk))):
        raise ZenError('bulk data map is not used exactly once by the mips')
    for a, c in zip(mips, mips[1:]):
        if (c.width, c.height) != (max(1, a.width >> 1), max(1, a.height >> 1)) or c.depth != 1:
            raise ZenError('mip sizes do not halve')
    seen_inline = False
    off = 0
    for m in mips:
        e = tex.bulk[m.index]
        if m.inline:
            seen_inline = True
            if e.offset != m.rec_off + 4 - tex.export_off:
                raise ZenError('inline payload offset mismatch')
        else:
            if seen_inline:
                raise ZenError('streamed mip after inline mip')
            if e.offset != off:
                raise ZenError('streamed mips are not contiguous')
            off += e.size
    if ubulk_size is not None and off != ubulk_size:
        raise ZenError(f'streamed size {off} != bulk chunk {ubulk_size}')


def rebuild(tex: TexturePackage, drop: int) -> bytes:
    if drop <= 0:
        return tex.data
    if drop >= len(tex.streamed):
        raise ZenError('cannot drop every streamed mip')
    b = tex.data
    gone = tex.mips[:drop]
    kept = tex.mips[drop:]
    cut = sum(m.size for m in gone)
    rec_cut = sum(m.rec_len for m in gone)
    remap = {m.index: i for i, m in enumerate(kept)}
    entries = [None] * len(kept)
    for m in kept:
        e = tex.bulk[m.index]
        if m.inline:
            off = e.offset - rec_cut
        else:
            off = e.offset - cut
        entries[remap[m.index]] = BulkEntry(off, e.dup, e.size, e.flags, e.pad)
    hdr_cut = BULK_ENTRY.size * drop

    head = bytearray(b[:tex.bulk_map_off])
    head += struct.pack('<q', BULK_ENTRY.size * len(entries))
    head += b''.join(e.pack() for e in entries)
    head += b[tex.bulk_map_end:tex.header_size]
    struct.pack_into('<I', head, 4, tex.header_size - hdr_cut)
    for o in SUMMARY_OFFSETS:
        v = _i32(b, o)
        if v >= tex.bulk_map_end:
            struct.pack_into('<i', head, o, v - hdr_cut)
    em = tex.export_map_off - hdr_cut
    struct.pack_into('<Q', head, em + 8, tex.export_size - rec_cut)

    ex = bytearray(b[tex.header_size:tex.export_off + tex.export_size])
    base = tex.header_size
    skip = _i64(b, tex.skip_loc)
    struct.pack_into('<q', ex, tex.skip_loc - base, skip - rec_cut)
    struct.pack_into('<ii', ex, tex.pd_off - base, kept[0].width, kept[0].height)
    struct.pack_into('<i', ex, tex.num_mips_off - base, len(kept))
    for m in kept:
        struct.pack_into('<i', ex, m.rec_off - base, remap[m.index])
    start = gone[0].rec_off - base
    del ex[start:start + rec_cut]
    return bytes(head) + bytes(ex) + b[tex.export_off + tex.export_size:]
