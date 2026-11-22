from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

TAG = 0x9E2A83C1
FILTER_EDITOR_ONLY = 0x80000000
UNVERSIONED_PROPERTIES = 0x2000
EXPORT_SIZE = 104
AT_END = 0x1
COMPRESSED = 0x2
UNUSED = 0x20
SEPARATE = 0x100
OPTIONAL = 0x800
SIZE64 = 0x2000
DUPLICATE = 0x4000
TEXTURE_CLASSES = ('Texture2D',)
_PF = re.compile(rb'PF_[A-Za-z0-9_]{1,40}\x00')

BLOCK_BYTES = {
    'PF_DXT1': (4, 8), 'PF_BC4': (4, 8), 'PF_ATI1': (4, 8),
    'PF_DXT3': (4, 16), 'PF_DXT5': (4, 16), 'PF_BC5': (4, 16), 'PF_BC6H': (4, 16), 'PF_BC7': (4, 16),
    'PF_ATI2': (4, 16),
    'PF_G8': (1, 1), 'PF_A8': (1, 1), 'PF_R8': (1, 1), 'PF_L8': (1, 1),
    'PF_G16': (1, 2), 'PF_R16F': (1, 2), 'PF_V8U8': (1, 2), 'PF_R8G8': (1, 2),
    'PF_B8G8R8A8': (1, 4), 'PF_R8G8B8A8': (1, 4), 'PF_A2B10G10R10': (1, 4), 'PF_G16R16': (1, 4),
    'PF_G16R16F': (1, 4), 'PF_R32_FLOAT': (1, 4), 'PF_FloatR11G11B10': (1, 4),
    'PF_FloatRGBA': (1, 8), 'PF_A16B16G16R16': (1, 8), 'PF_R16G16B16A16_UNORM': (1, 8),
    'PF_A32B32G32R32F': (1, 16),
}


class PackageError(Exception):
    pass


class _Reader:
    def __init__(self, b: bytes, p: int = 0):
        self.b = b
        self.p = p

    def take(self, fmt: str):
        try:
            v = struct.unpack_from(fmt, self.b, self.p)
        except struct.error:
            raise PackageError(f'truncated at {self.p}') from None
        self.p += struct.calcsize(fmt)
        return v[0] if len(v) == 1 else v

    def fstring(self) -> str:
        n = self.take('<i')
        if n == 0:
            return ''
        if n < 0:
            s = self.b[self.p:self.p - 2 * n].decode('utf-16le', 'replace')[:-1]
            self.p += -2 * n
        else:
            s = self.b[self.p:self.p + n - 1].decode('latin-1')
            self.p += n
        return s


@dataclass
class Export:
    pos: int
    cls: str
    name: str
    size: int
    offset: int


@dataclass
class Summary:
    total: int
    flags: int
    names: list[str]
    exports: list[Export]
    bulk_start: int
    bulk_start_pos: int
    imports: list[tuple[str, str]] = field(default_factory=list)
    soft_refs: list[str] = field(default_factory=list)


def read_summary(b: bytes) -> Summary:
    r = _Reader(b)
    if r.take('<I') != TAG:
        raise PackageError('not a package (bad tag)')
    legacy = r.take('<i')
    if legacy != -7:
        raise PackageError(f'legacy file version {legacy} is not supported')
    r.take('<i')
    ue4, _ = r.take('<ii')
    n = r.take('<i')
    r.p += 20 * n
    total = r.take('<i')
    r.fstring()
    flags = r.take('<I')
    if flags & UNVERSIONED_PROPERTIES:
        raise PackageError('unversioned properties are not supported')
    name_count, name_off = r.take('<ii')
    if not flags & FILTER_EDITOR_ONLY:
        r.fstring()
    r.take('<ii')
    export_count, export_off, import_count, import_off = r.take('<iiii')
    r.take('<i')
    soft_count, soft_off = r.take('<ii')
    r.p += 4 * 2 + 16
    n = r.take('<i')
    r.p += 8 * n
    for _ in range(2):
        r.p += 10
        r.fstring()
    r.take('<I')
    if r.take('<i'):
        raise PackageError('compressed chunks are not supported')
    r.take('<I')
    for _ in range(r.take('<i')):
        r.fstring()
    r.take('<i')
    bulk_start_pos = r.p
    bulk_start = r.take('<q')
    r.p = name_off
    names = []
    for _ in range(name_count):
        names.append(r.fstring())
        r.p += 4
    r.p = import_off
    imports = []
    for _ in range(import_count):
        _, _, cn, _, _, on, _ = r.take('<iiiiiii')
        imports.append((names[cn], names[on]))
    soft_refs = []
    for i in range(soft_count):
        soft_refs.append(names[struct.unpack_from('<i', b, soft_off + 8 * i)[0]])
    exports = []
    for i in range(export_count):
        pos = export_off + i * EXPORT_SIZE
        ci = struct.unpack_from('<i', b, pos)[0]
        on = struct.unpack_from('<i', b, pos + 16)[0]
        size, offset = struct.unpack_from('<qq', b, pos + 28)
        cls = imports[-ci - 1][1] if ci < 0 else ''
        exports.append(Export(pos, cls, names[on], size, offset))
    if ue4 != 0 and ue4 < 516:
        raise PackageError(f'package version {ue4} is too old')
    return Summary(total, flags, names, exports, bulk_start, bulk_start_pos, imports, soft_refs)


@dataclass
class Mip:
    rec_off: int
    rec_len: int
    width: int
    height: int
    depth: int
    flags: int
    size: int
    offset: int

    @property
    def separate(self) -> bool:
        return bool(self.flags & SEPARATE)

    @property
    def max_dim(self) -> int:
        return max(self.width, self.height)


@dataclass
class Texture:
    summary: Summary
    export: Export
    pd_off: int
    skip_loc: int
    skip_target: int
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
    def streamed(self) -> list[Mip]:
        return [m for m in self.mips if m.separate]

    @property
    def streamed_size(self) -> int:
        return sum(m.size for m in self.streamed)


def _mip_size(pf: str, w: int, h: int, d: int) -> int | None:
    bb = BLOCK_BYTES.get(pf)
    if bb is None:
        return None
    blk, n = bb
    return -(-w // blk) * -(-h // blk) * n * d


def parse(uasset: bytes, uexp: bytes, ubulk_size: int | None = None) -> Texture:
    s = read_summary(uasset)
    if len(uasset) != s.total:
        raise PackageError('uasset size does not match the header size')
    if len(s.exports) != 1:
        raise PackageError(f'{len(s.exports)} exports')
    ex = s.exports[0]
    if ex.cls not in TEXTURE_CLASSES:
        raise PackageError(f'export class {ex.cls}')
    if ex.offset != s.total or ex.size + 4 != len(uexp) or struct.unpack_from('<I', uexp, len(uexp) - 4)[0] != TAG:
        raise PackageError('export does not span the uexp')
    if s.bulk_start != s.total + ex.size:
        raise PackageError('bulk data start is not the end of the export')
    names = s.names
    if 'None' not in names:
        raise PackageError('no None name')
    none_idx = names.index('None')
    b = uexp
    end = ex.size
    found = None
    for m in _PF.finditer(b, 0, end):
        st = m.start() - 4
        if st - 28 < 0 or struct.unpack_from('<i', b, st)[0] != m.end() - m.start():
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
            raise PackageError('several platform data blocks')
        found = (pf, pd, skip_loc, target)
    if found is None:
        raise PackageError('no texture platform data')
    pf, pd, skip_loc, target = found
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
    pos = 0
    for m in streamed:
        if m.offset != pos:
            raise PackageError('streamed mips are not contiguous in the ubulk')
        pos += m.size
    if ubulk_size is not None and pos != ubulk_size:
        raise PackageError(f'streamed mips cover {pos} of {ubulk_size} ubulk bytes')
    return Texture(s, ex, pd, skip_loc, target, pf, first, num_mips_off, mips)


@dataclass
class Drop:
    uasset: bytes
    uexp: bytes
    cut: int
    removed: int


def drop(tex: Texture, n: int, uasset: bytes, uexp: bytes) -> Drop:
    streamed = tex.streamed
    if not 0 < n <= len(streamed):
        raise PackageError(f'cannot drop {n} of {len(streamed)} streamed mips')
    gone = tex.mips[:n]
    cut = sum(m.size for m in gone)
    lo = gone[0].rec_off
    hi = gone[-1].rec_off + gone[-1].rec_len
    removed = hi - lo
    b = bytearray(uexp)
    struct.pack_into('<ii', b, tex.num_mips_off - 4, tex.first_mip + n, len(tex.mips) - n)
    for m in tex.mips[n:]:
        off = m.rec_off + 4 + 4 + (16 if m.flags & SIZE64 else 8)
        struct.pack_into('<q', b, off, m.offset - (cut if m.separate else removed))
    skip = struct.unpack_from('<q', b, tex.skip_loc)[0]
    struct.pack_into('<q', b, tex.skip_loc, skip - removed)
    del b[lo:hi]
    a = bytearray(uasset)
    ex = tex.export
    struct.pack_into('<q', a, ex.pos + 28, ex.size - removed)
    struct.pack_into('<q', a, tex.summary.bulk_start_pos, tex.summary.bulk_start - removed)
    return Drop(bytes(a), bytes(b), cut, removed)


def check(d: Drop, tex: Texture, n: int, ubulk_size: int) -> str | None:
    try:
        new = parse(d.uasset, d.uexp, ubulk_size - d.cut)
    except PackageError as e:
        return str(e)
    old = tex.mips[n:]
    if [(m.width, m.height, m.size) for m in new.mips] != [(m.width, m.height, m.size) for m in old]:
        return 'mip chain differs after the drop'
    if new.pixel_format != tex.pixel_format or new.first_mip != tex.first_mip + n:
        return 'pixel format or first mip changed'
    return None


@dataclass
class TexturePlan:
    path: str
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
    skipped: dict[str, int]
    ui: int

    @property
    def removed(self) -> int:
        return sum(t.cut for t in self.textures)


GAME_CONTENT = '/AtomicHeart/Content/'
UI_CLASSES = ('WidgetBlueprintGeneratedClass', 'SlateBrushAsset', 'SlateWidgetStyleAsset')
MATERIAL_CLASSES = ('Material', 'MaterialInstanceConstant')


def package_name(path: str) -> str | None:
    i = path.find(GAME_CONTENT)
    if i < 0:
        return None
    return '/Game/' + path[i + len(GAME_CONTENT):]


def references(s: Summary) -> set[str]:
    out = {on for cn, on in s.imports if cn == 'Package' and on.startswith('/Game/')}
    out.update(n.split('.')[0] for n in s.soft_refs if n.startswith('/Game/'))
    out.update(n.split('.')[0] for n in s.names if n.startswith('/Game/') and '.' in n)
    return out


def ui_textures(paks: list[str], progress=None) -> set[str]:
    from ..package import uepak
    ui_users: set[str] = set()
    materials: dict[str, set[str]] = {}
    ui_materials: set[str] = set()
    refs: dict[str, set[str]] = {}
    jobs = []
    for path in paks:
        pak = uepak.Pak.open(path)
        jobs.append((path, pak, [(p, e) for p, e in pak.files() if p.endswith(('.uasset', '.umap'))]))
    total = sum(len(j[2]) for j in jobs)
    done = 0
    for path, pak, entries in jobs:
        method = uepak.pak_method(pak)
        with open(path, 'rb') as f:
            for p, e in entries:
                done += 1
                if progress:
                    progress(done, total)
                name = package_name(p[:p.rfind('.')])
                if name is None:
                    continue
                try:
                    s = read_summary(uepak.read_entry(f, e, method))
                except (PackageError, IndexError):
                    continue
                cls = {x.cls for x in s.exports}
                r = references(s)
                refs[name] = r
                if cls & set(UI_CLASSES):
                    ui_users.add(name)
                elif cls & set(MATERIAL_CLASSES):
                    materials[name] = r
                    if 'MD_UI' in s.names:
                        ui_materials.add(name)
    grew = True
    while grew:
        grew = False
        for m, r in materials.items():
            if m not in ui_materials and r & ui_materials:
                ui_materials.add(m)
                grew = True
    out: set[str] = set()
    for user in ui_users | ui_materials:
        out |= refs[user]
    return out


def is_ui(path: str, s: Summary, ui: set[str] = frozenset()) -> bool:
    return '/UI/' in path or 'TEXTUREGROUP_UI' in s.names or package_name(path) in ui


def plan_pak(path: str, source: str, policy, progress=None, ui: set[str] = frozenset()) -> PakPlan:
    from ..package import uepak
    pak = uepak.Pak.open(source)
    method = uepak.pak_method(pak)
    files = dict(pak.files())
    changes: dict = {}
    textures = []
    skipped: dict[str, int] = {}
    ui_count = 0
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
            if [e.cls for e in s.exports] != list(TEXTURE_CLASSES):
                continue
            if is_ui(base, s, ui):
                ui_count += 1
                continue
            ux = uepak.read_entry(f, x, method)
            try:
                tex = parse(ua, ux, u.usize)
            except PackageError as ex:
                reason = str(ex)
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            n = min(policy.count(list(reversed(tex.mips))), len(tex.streamed) - 1)
            if n <= 0:
                continue
            d = drop(tex, n, ua, ux)
            err = check(d, tex, n, u.usize)
            if err:
                raise PackageError(f'{base}: {err}')
            changes[a.loc] = uepak.Change(data=d.uasset)
            changes[x.loc] = uepak.Change(data=d.uexp)
            changes[u.loc] = uepak.Change(cut=d.cut)
            textures.append(TexturePlan(base, tex.width, tex.height, n, d.cut, tex.pixel_format))
    return PakPlan(path, source, pak, changes, textures, skipped, ui_count)
