from __future__ import annotations

import bisect
import collections
import struct
from dataclasses import dataclass, field

from ..mipdrop.ue4 import (AT_END, BLOCK_BYTES, COMPRESSED, DUPLICATE, OPTIONAL, SEPARATE, SIZE64, TAG, UNUSED,
                           _PF, Export, Mip, PackageError, Summary, _mip_size, _Reader, read_summary)

TEXTURE_CLASSES = ('LightMapTexture2D', 'ShadowMapTexture2D')
REGISTRY_CLASS = 'MapBuildDataRegistry'
PACKAGE_CLASSES = TEXTURE_CLASSES + (REGISTRY_CLASS,)


@dataclass
class Texture:
    index: int
    cls: str
    name: str
    skip_loc: int
    target: int
    pixel_format: str
    first_mip: int
    num_mips_off: int
    mips: list[Mip] = field(default_factory=list)

    @property
    def width(self) -> int:
        return self.mips[0].width

    @property
    def height(self) -> int:
        return self.mips[0].height

    @property
    def max_dim(self) -> int:
        return max(self.width, self.height)

    @property
    def streamed(self) -> list[Mip]:
        return [m for m in self.mips if m.separate]


@dataclass
class Package:
    summary: Summary
    uexp_size: int
    ubulk_size: int
    order: list[int]
    textures: list[Texture]


def _platform(s: Summary, b: bytes, start: int, end: int) -> tuple[str, int, int, int]:
    names = s.names
    none_idx = names.index('None')
    found = None
    for m in _PF.finditer(b, start, end):
        st = m.start() - 4
        if st - 28 < start or struct.unpack_from('<i', b, st)[0] != m.end() - m.start():
            continue
        pf = m.group()[:-1].decode()
        if pf not in names:
            continue
        pd = st - 12
        skip_loc = pd - 8
        idx, num = struct.unpack_from('<ii', b, skip_loc - 8)
        if idx != names.index(pf) or num != 0:
            continue
        target = struct.unpack_from('<q', b, skip_loc)[0] - s.total
        if not pd < target <= end - 8 or struct.unpack_from('<ii', b, target) != (none_idx, 0):
            continue
        if found is not None:
            raise PackageError('several platform data blocks in one export')
        found = (pf, pd, skip_loc, target)
    if found is None:
        raise PackageError('no texture platform data')
    return found


def _texture(s: Summary, b: bytes, index: int, ex: Export) -> Texture:
    start = ex.offset - s.total
    end = start + ex.size
    pf, pd, skip_loc, target = _platform(s, b, start, end)
    sx, sy, packed = struct.unpack_from('<iiI', b, pd)
    if packed != 1:
        raise PackageError(f'unsupported packed data {packed:#x}')
    r = _Reader(b, pd + 12)
    r.fstring()
    first = r.take('<i')
    if not 0 <= first < 16:
        raise PackageError(f'bad first mip {first}')
    num_mips_off = r.p
    n = r.take('<i')
    if not 0 < n <= 16:
        raise PackageError(f'bad mip count {n}')
    mips = []
    for _ in range(n):
        rec = r.p
        if r.take('<i') != 1:
            raise PackageError('mip is not cooked')
        flags = r.take('<I')
        if flags & (COMPRESSED | OPTIONAL | DUPLICATE):
            raise PackageError(f'unsupported bulk flags {flags:#x}')
        if flags & SIZE64:
            count, size = r.take('<qq')
        else:
            count, size = r.take('<ii')
        offset = r.take('<q')
        if flags & UNUSED:
            raise PackageError('unused bulk data')
        if count != size:
            raise PackageError('bulk element count differs from size on disk')
        if not flags & AT_END:
            if offset != s.total + r.p:
                raise PackageError('inline payload offset is not its position in the package')
            r.p += size
            if flags & SEPARATE:
                raise PackageError('inline payload marked as separate')
        elif not flags & SEPARATE:
            raise PackageError('end-of-file payload in the uexp')
        w, h, d = r.take('<iii')
        mips.append(Mip(rec, r.p - rec, w, h, d, flags, size, offset))
    if r.take('<i') != 0:
        raise PackageError('virtual texture')
    if r.p != target:
        raise PackageError('platform data does not end at the skip offset')
    if (max(1, sx >> first), max(1, sy >> first)) != (mips[0].width, mips[0].height):
        raise PackageError('platform size does not match mip 0')
    floor = BLOCK_BYTES.get(pf, (1, 0))[0]
    for a, c in zip(mips, mips[1:]):
        want = (max(1, a.width // 2), max(1, a.height // 2))
        if (c.width, c.height) != want and (c.width, c.height) != (max(floor, want[0]), max(floor, want[1])):
            raise PackageError('mip chain is not a halving chain')
    for m in mips:
        want = _mip_size(pf, m.width, m.height, m.depth)
        if want is not None and want != m.size:
            raise PackageError(f'mip {m.width}x{m.height} size {m.size} != {want}')
    streamed = [m for m in mips if m.separate]
    if any(m.separate for m in mips[len(streamed):]):
        raise PackageError('streamed mips are not the top mips')
    return Texture(index, ex.cls, ex.name, skip_loc, target, pf, first, num_mips_off, mips)


def parse(uasset: bytes, uexp: bytes, ubulk_size: int) -> Package:
    s = read_summary(uasset)
    if len(uasset) != s.total:
        raise PackageError('uasset size does not match the header size')
    if len(uexp) < 4 or struct.unpack_from('<I', uexp, len(uexp) - 4)[0] != TAG:
        raise PackageError('uexp does not end with the package tag')
    classes = {e.cls for e in s.exports}
    if not classes <= set(PACKAGE_CLASSES) or not classes & set(TEXTURE_CLASSES):
        raise PackageError('not a BuiltData package with lightmap textures')
    if 'None' not in s.names:
        raise PackageError('no None name')
    order = sorted(range(len(s.exports)), key=lambda i: s.exports[i].offset)
    pos = s.total
    for i in order:
        e = s.exports[i]
        if e.offset != pos or e.size < 0:
            raise PackageError('exports do not follow each other in the uexp')
        pos += e.size
    if pos != s.total + len(uexp) - 4:
        raise PackageError('exports do not span the uexp')
    if s.bulk_start != pos:
        raise PackageError('bulk data start is not the end of the exports')
    textures = [_texture(s, uexp, i, e) for i, e in enumerate(s.exports) if e.cls in TEXTURE_CLASSES]
    spans = sorted((m.offset, m.size) for t in textures for m in t.streamed)
    at = 0
    for off, size in spans:
        if off != at:
            raise PackageError('streamed mips do not tile the ubulk')
        at += size
    if at != ubulk_size:
        raise PackageError(f'streamed mips cover {at} of {ubulk_size} ubulk bytes')
    for t in textures:
        st = t.streamed
        for a, c in zip(st, st[1:]):
            if c.offset != a.offset + a.size:
                raise PackageError('streamed mips of a texture are not contiguous')
    return Package(s, len(uexp), ubulk_size, order, textures)


class _Holes:
    def __init__(self, spans: list[tuple[int, int]]):
        self.spans = sorted(spans)
        self.starts = [a for a, _ in self.spans]
        self.before = [0]
        for a, b in self.spans:
            self.before.append(self.before[-1] + b - a)

    def shift(self, pos: int) -> int:
        i = bisect.bisect_right(self.starts, pos)
        if i and pos < self.spans[i - 1][1]:
            raise PackageError(f'position {pos} is inside a removed range')
        return pos - self.before[i]

    def keep(self, size: int) -> tuple[tuple[int, int], ...]:
        out = []
        at = 0
        for a, b in self.spans:
            if a > at:
                out.append((at, a - at))
            at = b
        if size > at:
            out.append((at, size - at))
        return tuple(out)

    @property
    def total(self) -> int:
        return self.before[-1]


@dataclass
class Drop:
    uasset: bytes
    uexp_keep: tuple[tuple[int, int], ...]
    uexp_patch: tuple[tuple[int, bytes], ...]
    ubulk_keep: tuple[tuple[int, int], ...]
    removed: int
    cut: int


def drop(pkg: Package, counts: dict[int, int], uasset: bytes) -> Drop:
    s = pkg.summary
    by_index = {t.index: t for t in pkg.textures}
    rec_holes = []
    bulk_holes = []
    for i, n in counts.items():
        t = by_index[i]
        if not 0 < n < len(t.streamed):
            raise PackageError(f'cannot drop {n} of {len(t.streamed)} streamed mips of {t.name}')
        gone = t.mips[:n]
        rec_holes.append((gone[0].rec_off, gone[-1].rec_off + gone[-1].rec_len))
        bulk_holes.append((gone[0].offset, gone[-1].offset + gone[-1].size))
    rh = _Holes(rec_holes)
    bh = _Holes(bulk_holes)
    patch: list[tuple[int, bytes]] = []
    for t in pkg.textures:
        n = counts.get(t.index, 0)
        if n:
            patch.append((rh.shift(t.num_mips_off - 4), struct.pack('<ii', t.first_mip + n, len(t.mips) - n)))
        for m in t.mips[n:]:
            field_at = m.rec_off + 8 + (16 if m.flags & SIZE64 else 8)
            value = bh.shift(m.offset) if m.separate else s.total + rh.shift(m.offset - s.total)
            patch.append((rh.shift(field_at), struct.pack('<q', value)))
        patch.append((rh.shift(t.skip_loc), struct.pack('<q', s.total + rh.shift(t.target))))
    a = bytearray(uasset)
    for e in s.exports:
        start = e.offset - s.total
        new_start = rh.shift(start)
        new_end = rh.shift(start + e.size)
        struct.pack_into('<q', a, e.pos + 28, new_end - new_start)
        struct.pack_into('<q', a, e.pos + 36, s.total + new_start)
    new_uexp = pkg.uexp_size - rh.total
    struct.pack_into('<q', a, s.bulk_start_pos, s.total + new_uexp - 4)
    return Drop(bytes(a), rh.keep(pkg.uexp_size), tuple(sorted(patch)), bh.keep(pkg.ubulk_size), rh.total, bh.total)


def check(d: Drop, pkg: Package, counts: dict[int, int], uexp: bytes, new_uexp: bytes) -> str | None:
    try:
        new = parse(d.uasset, new_uexp, pkg.ubulk_size - d.cut)
    except PackageError as e:
        return str(e)
    if len(new.textures) != len(pkg.textures):
        return 'texture count changed'
    for old, cur in zip(pkg.textures, new.textures):
        n = counts.get(old.index, 0)
        if (cur.index, cur.cls, cur.name, cur.pixel_format) != (old.index, old.cls, old.name, old.pixel_format):
            return f'{old.name}: texture identity changed'
        if cur.first_mip != old.first_mip + n:
            return f'{old.name}: first mip is {cur.first_mip}, expected {old.first_mip + n}'
        if [(m.width, m.height, m.size, m.flags) for m in cur.mips] != \
                [(m.width, m.height, m.size, m.flags) for m in old.mips[n:]]:
            return f'{old.name}: mip chain differs after the drop'
        for m, o in zip(cur.mips, old.mips[n:]):
            if not m.separate and new_uexp[m.rec_off + 24:m.rec_off + m.rec_len] != \
                    uexp[o.rec_off + 24:o.rec_off + o.rec_len]:
                return f'{old.name}: inline mip data changed'
    s, ns = pkg.summary, new.summary
    for e, ne in zip(s.exports, ns.exports):
        if e.cls == REGISTRY_CLASS:
            a = e.offset - s.total
            b = ne.offset - ns.total
            if uexp[a:a + e.size] != new_uexp[b:b + ne.size]:
                return 'registry export changed'
    return None


@dataclass
class TexturePlan:
    path: str
    name: str
    cls: str
    width: int
    height: int
    drop: int
    cut: int
    pixel_format: str

    @property
    def max_dim(self) -> int:
        return max(self.width, self.height)


@dataclass
class PakPlan:
    path: str
    source: str
    pak: object
    changes: dict
    textures: list[TexturePlan]
    packages: int
    skipped: dict[str, int]

    @property
    def removed(self) -> int:
        return sum(t.cut for t in self.textures)


def plan_pak(path: str, source: str, policy, progress=None) -> PakPlan:
    from ..package import uepak
    pak = uepak.Pak.open(source)
    method = uepak.slot_methods(pak.names)
    files = dict(pak.files())
    changes: dict = {}
    textures: list[TexturePlan] = []
    skipped: collections.Counter = collections.Counter()
    packages = 0
    bulks = [k for k in files if k.endswith('.ubulk')]
    with open(source, 'rb') as f:
        for i, k in enumerate(bulks):
            if progress:
                progress(i + 1, len(bulks))
            base = k[:-6]
            a, x, u = files.get(base + '.uasset'), files.get(base + '.uexp'), files[k]
            if a is None or x is None:
                continue
            ua = uepak.read_entry(f, a, method)
            try:
                s = read_summary(ua)
            except PackageError:
                continue
            classes = {e.cls for e in s.exports}
            if not classes & set(TEXTURE_CLASSES):
                continue
            ux = uepak.read_entry(f, x, method)
            try:
                pkg = parse(ua, ux, u.usize)
            except PackageError as ex:
                skipped[str(ex)] += 1
                continue
            counts = {}
            for t in pkg.textures:
                n = min(policy.count(list(reversed(t.mips))), len(t.streamed) - 1)
                if n > 0:
                    counts[t.index] = n
            if not counts:
                continue
            d = drop(pkg, counts, ua)
            new_uexp = uepak.splice(ux, d.uexp_keep, d.uexp_patch)
            err = check(d, pkg, counts, ux, new_uexp)
            if err:
                raise PackageError(f'{base}: {err}')
            packages += 1
            changes[a.loc] = uepak.Change(data=d.uasset)
            changes[x.loc] = uepak.Change(keep=d.uexp_keep, patch=d.uexp_patch)
            changes[u.loc] = uepak.Change(keep=d.ubulk_keep)
            for t in pkg.textures:
                n = counts.get(t.index, 0)
                if n:
                    cut = sum(m.size for m in t.mips[:n])
                    textures.append(TexturePlan(base, t.name, t.cls, t.width, t.height, n, cut, t.pixel_format))
    return PakPlan(path, source, pak, changes, textures, packages, dict(skipped))
