"""Halve the resolution of the SH lighting volume textures stored in `.wad` files.

Each volume is a pair of wad entries with the same name `<8 hex>SH<n>`:
  * tag 0x0001: descriptor; a GNF header sits in the body, preceded by a u32 that equals
    the data entry size + 344
  * tag 0x001d: data; a linear [depth][height][width] array of texels (RGBA8 UNORM or
    RGBA16F), zero padded up to GNF dw7 = align8(w) * align8(h) * align4(d) * bpp,
    followed by 12 bytes
Changing a data entry size shifts every later entry, so the whole wad is rewritten and each
`autopad` entry (tag 0x0019) is resized to keep the next entry on a 64 KiB boundary.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

import numpy as np

from .lod import iter_wad_entries

HEADER = 0x60
SH_NAME = re.compile(rb'^[0-9A-F]{8}SH\d+$')
TAG_DESC = 0x0001
TAG_DATA = 0x001D
TAG_AUTOPAD = 0x0019
AUTOPAD_ALIGN = 0x10000
DATA_TRAILER = 12
DESC_SIZE_EXTRA = 344
FORMATS = {10: (4, np.uint8), 12: (8, np.float16)}   # dfmt -> (bytes per texel, component type)


class ShVolError(Exception):
    pass


def a16(n: int) -> int:
    return (n + 15) & ~15


def a8(n: int) -> int:
    return (n + 7) & ~7


def a4(n: int) -> int:
    return (n + 3) & ~3


@dataclass
class Gnf:
    offset: int          # GNF chunk offset inside the descriptor body
    dw: list[int]

    @property
    def width(self) -> int:
        return (self.dw[2] & 0x3fff) + 1

    @property
    def height(self) -> int:
        return ((self.dw[2] >> 14) & 0x3fff) + 1

    @property
    def depth(self) -> int:
        return (self.dw[4] & 0x1fff) + 1

    @property
    def pitch(self) -> int:
        return ((self.dw[4] >> 13) & 0x3fff) + 1

    @property
    def dfmt(self) -> int:
        return (self.dw[1] >> 20) & 0x3f

    @property
    def type(self) -> int:
        return self.dw[3] >> 28


def read_gnf(desc_body: bytes) -> Gnf:
    g = desc_body.find(b'GNF ')
    if g < 4:
        raise ShVolError('GNF header not found in descriptor')
    return Gnf(g, list(struct.unpack_from('<8I', desc_body, g + 16)))


def tiled_size(w: int, h: int, d: int, bpp: int) -> int:
    return a8(w) * a8(h) * a4(d) * bpp


def check_volume(desc_body: bytes, data_size: int) -> Gnf:
    g = read_gnf(desc_body)
    if g.type != 10 or g.dfmt not in FORMATS:
        raise ShVolError(f'unexpected texture type {g.type} / format {g.dfmt}')
    bpp = FORMATS[g.dfmt][0]
    if g.pitch != a8(g.width) or g.dw[7] != tiled_size(g.width, g.height, g.depth, bpp):
        raise ShVolError('pitch or size rule does not hold')
    if data_size != g.dw[7] + DATA_TRAILER:
        raise ShVolError('data entry size does not match GNF')
    if struct.unpack_from('<I', desc_body, g.offset - 4)[0] != data_size + DESC_SIZE_EXTRA:
        raise ShVolError('descriptor size field does not match')
    if struct.unpack_from('<I', desc_body, g.offset + 12)[0] != g.dw[7]:
        raise ShVolError('GNF stream size does not match')
    return g


def downsample(vol: np.ndarray) -> np.ndarray:
    """2x2x2 box filter on a (d, h, w, c) array; odd sizes repeat the last cell."""
    d, h, w, c = vol.shape
    pad = ((0, d % 2), (0, h % 2), (0, w % 2), (0, 0))
    v = np.pad(vol.astype(np.float32), pad, mode='edge')
    v = v.reshape(v.shape[0] // 2, 2, v.shape[1] // 2, 2, v.shape[2] // 2, 2, c).mean(axis=(1, 3, 5))
    return v


def shrink_volume(desc_body: bytes, data_body: bytes, factor: int) -> tuple[bytes, bytes]:
    """Return (new descriptor body, new data body). factor 1 rebuilds the input unchanged."""
    g = check_volume(desc_body, len(data_body))
    bpp, ctype = FORMATS[g.dfmt]
    w, h, d = g.width, g.height, g.depth
    texels = np.frombuffer(data_body, dtype=ctype, count=w * h * d * 4).reshape(d, h, w, 4)
    if factor == 1:
        # Keep the original padding and trailer bytes so a rebuild is byte-identical
        return desc_body, data_body
    if factor != 2:
        raise ShVolError('only factor 1 or 2 is supported')
    if min(w, h, d) < 2:
        return desc_body, data_body
    v = downsample(texels)
    if ctype == np.uint8:
        v = np.clip(np.rint(v), 0, 255).astype(np.uint8)
    else:
        v = v.astype(np.float16)
    nd, nh, nw = v.shape[:3]
    size7 = tiled_size(nw, nh, nd, bpp)
    payload = v.tobytes()
    data = payload + bytes(size7 - len(payload)) + bytes(DATA_TRAILER)

    db = bytearray(desc_body)
    dw = list(g.dw)
    dw[2] = (dw[2] & ~0x0fffffff) | (nw - 1) | ((nh - 1) << 14)
    dw[4] = (dw[4] & ~0x07ffffff) | (nd - 1) | ((a8(nw) - 1) << 13)
    dw[7] = size7
    struct.pack_into('<8I', db, g.offset + 16, *dw)
    struct.pack_into('<I', db, g.offset + 12, size7)
    struct.pack_into('<I', db, g.offset - 4, len(data) + DESC_SIZE_EXTRA)
    return bytes(db), data


@dataclass
class Result:
    data: bytes
    volumes: int
    old_bytes: int
    new_bytes: int


def rewrite_wad(buf: bytes, factor: int = 2) -> Result:
    entries = list(iter_wad_entries(buf))
    desc_idx, data_idx = {}, {}
    for i, (off, tag, size, name) in enumerate(entries):
        if SH_NAME.match(name):
            if tag == TAG_DESC:
                desc_idx[name] = i
            elif tag == TAG_DATA:
                data_idx[name] = i
    new_body: dict[int, bytes] = {}
    old_total = new_total = 0
    for name, di in data_idx.items():
        ci = desc_idx.get(name)
        if ci is None:
            continue
        coff, _, csize, _ = entries[ci]
        doff, _, dsize, _ = entries[di]
        desc_body = buf[coff + HEADER:coff + HEADER + csize]
        data_body = buf[doff + HEADER:doff + HEADER + dsize]
        nd, ndata = shrink_volume(desc_body, data_body, factor)
        new_body[ci] = nd
        new_body[di] = ndata
        old_total += dsize
        new_total += len(ndata)

    out = bytearray()
    for i, (off, tag, size, name) in enumerate(entries):
        header = bytearray(buf[off:off + HEADER])
        if tag == TAG_AUTOPAD:
            # the autopad body ends exactly on the next 64 KiB boundary
            body = bytes(-(len(out) + HEADER) % AUTOPAD_ALIGN)
        else:
            body = new_body.get(i, buf[off + HEADER:off + HEADER + size])
        struct.pack_into('<I', header, 4, len(body))
        out += header
        out += body
        end = off + HEADER + size
        pad = a16(len(out)) - len(out)
        if i not in new_body and tag != TAG_AUTOPAD and a16(end) - end == pad:
            out += buf[end:end + pad]       # keep original padding bytes
        else:
            out += bytes(pad)
    return Result(bytes(out), len(new_body) // 2, old_total, new_total)


def verify_wad(original: bytes, rewritten: bytes) -> list[str]:
    errors = []
    a = list(iter_wad_entries(original))
    b = list(iter_wad_entries(rewritten))
    if [(t, n) for _, t, _, n in a] != [(t, n) for _, t, _, n in b]:
        return ['entry sequence changed']
    if a16(b[-1][0] + HEADER + b[-1][2]) != len(rewritten):
        errors.append('file does not end after the last entry')
    for (oa, ta, sa, na), (ob, tb, sb, nb) in zip(a, b):
        if original[oa + 8:oa + HEADER] != rewritten[ob + 8:ob + HEADER]:
            errors.append(f'header changed: {na!r}')
        if ta == TAG_AUTOPAD:
            if any(rewritten[ob + HEADER:ob + HEADER + sb]):
                errors.append('autopad body is not zero')
            continue
        if SH_NAME.match(na) and ta in (TAG_DESC, TAG_DATA):
            continue
        if original[oa + HEADER:oa + HEADER + sa] != rewritten[ob + HEADER:ob + HEADER + sb]:
            errors.append(f'body changed: {na!r}')
        if len(errors) > 20:
            break
    for i, (ob, tb, sb, nb) in enumerate(b[:-1]):
        if tb == TAG_AUTOPAD and b[i + 1][0] % AUTOPAD_ALIGN:
            errors.append(f'entry after autopad not 64 KiB aligned: {b[i + 1][3]!r}')
    descs = {n: (o, s) for o, t, s, n in b if t == TAG_DESC and SH_NAME.match(n)}
    for o, t, s, n in b:
        if t == TAG_DATA and SH_NAME.match(n) and n in descs:
            co, cs = descs[n]
            try:
                check_volume(rewritten[co + HEADER:co + HEADER + cs], s)
            except ShVolError as e:
                errors.append(f'{n!r}: {e}')
    return errors
