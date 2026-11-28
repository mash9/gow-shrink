from __future__ import annotations

import collections
import hashlib
import os
import re
import struct
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from ..package import iostore as io
from ..package import zen4
from . import ue4

Progress = Callable[[int, int], None]
TEXTURE_CLASS = '/Script/Engine.Texture2D'
GLOBAL = 'global.utoc'
VERSION = 3
_PF = re.compile(rb'PF_[A-Za-z0-9_]{1,40}\x00')


class TextureError(Exception):
    pass


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
        return bool(self.flags & ue4.SEPARATE)

    @property
    def max_dim(self) -> int:
        return max(self.width, self.height)


@dataclass
class TexturePackage:
    pkg: zen4.Package
    skip_loc: int
    pd_off: int
    pixel_format: str
    first_mip: int
    num_mips_off: int
    mips: list[Mip] = field(default_factory=list)

    @property
    def data(self) -> bytes:
        return self.pkg.data

    @property
    def width(self) -> int:
        return self.mips[0].width

    @property
    def height(self) -> int:
        return self.mips[0].height

    @property
    def streamed(self) -> list[Mip]:
        return [m for m in self.mips if m.separate]


def parse(pkg: zen4.Package, ubulk_size: int | None = None) -> TexturePackage:
    if [e.cls for e in pkg.exports] != [TEXTURE_CLASS]:
        raise TextureError('not a single Texture2D export')
    ex = pkg.exports[0]
    b = pkg.data
    start = pkg.header_end
    if ex.serial_offset != pkg.cooked_header_size or start + ex.serial_size != len(b):
        raise TextureError('export does not span the package')
    names = pkg.names
    if 'None' not in names:
        raise TextureError('no None name')
    none_idx = names.index('None')
    delta = pkg.delta
    found = None
    for m in _PF.finditer(b, start):
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
        target = struct.unpack_from('<q', b, skip_loc)[0] - delta
        if not pd < target <= len(b) - 8 or struct.unpack_from('<ii', b, target) != (none_idx, 0):
            continue
        if found is not None:
            raise TextureError('several platform data blocks')
        found = (pf, pd, skip_loc, target)
    if found is None:
        raise TextureError('no texture platform data')
    pf, pd, skip_loc, target = found
    sx, sy, packed = struct.unpack_from('<iiI', b, pd)
    if packed != 1:
        raise TextureError(f'unsupported packed data {packed:#x}')
    r = ue4._Reader(b, pd + 12)
    try:
        r.fstring()
        first = r.take('<i')
        if not 0 <= first < 16:
            raise TextureError(f'bad first mip {first}')
        num_mips_off = r.p
        n = r.take('<i')
        if not 0 < n <= 16:
            raise TextureError(f'bad mip count {n}')
        mips = []
        for _ in range(n):
            rec = r.p
            if r.take('<i') != 1:
                raise TextureError('mip is not cooked')
            flags = r.take('<I')
            if flags & ue4.OPTIONAL:
                raise TextureError('optional top mip (.uptnl)')
            if flags & (ue4.COMPRESSED | ue4.DUPLICATE | ue4.UNUSED):
                raise TextureError(f'unsupported bulk flags {flags:#x}')
            count, size = r.take('<qq') if flags & ue4.SIZE64 else r.take('<ii')
            offset = r.take('<q')
            if count != size:
                raise TextureError('bulk element count differs from size on disk')
            if flags & ue4.AT_END:
                if not flags & ue4.SEPARATE:
                    raise TextureError('end-of-file payload in the export')
            else:
                if flags & ue4.SEPARATE:
                    raise TextureError('inline payload marked as separate')
                if offset != r.p + delta:
                    raise TextureError('inline payload offset is not its position in the package')
                r.p += size
            w, h, d = r.take('<iii')
            mips.append(Mip(rec, r.p - rec, w, h, d, flags, size, offset))
        if r.take('<i') != 0:
            raise TextureError('virtual texture')
    except ue4.PackageError as e:
        raise TextureError(str(e)) from None
    if r.p != target:
        raise TextureError('platform data does not end at the skip offset')
    if (max(1, sx >> first), max(1, sy >> first)) != (mips[0].width, mips[0].height):
        raise TextureError('platform size does not match mip 0')
    floor = ue4.BLOCK_BYTES.get(pf, (1, 0))[0]
    for a, c in zip(mips, mips[1:]):
        want = (max(1, a.width // 2), max(1, a.height // 2))
        if (c.width, c.height) not in (want, (max(floor, want[0]), max(floor, want[1]))):
            raise TextureError('mip chain is not a halving chain')
    for m in mips:
        want = ue4._mip_size(pf, m.width, m.height, m.depth)
        if want is not None and want != m.size:
            raise TextureError(f'mip {m.width}x{m.height} size {m.size} != {want}')
    streamed = [m for m in mips if m.separate]
    if any(m.separate for m in mips[len(streamed):]):
        raise TextureError('streamed mips are not the top mips')
    pos = 0
    for m in streamed:
        if m.offset != pos:
            raise TextureError('streamed mips are not contiguous in the ubulk')
        pos += m.size
    if ubulk_size is not None and pos != ubulk_size:
        raise TextureError(f'streamed size {pos} != bulk chunk {ubulk_size}')
    return TexturePackage(pkg, skip_loc, pd, pf, first, num_mips_off, mips)


def removed_bytes(tex: TexturePackage, n: int) -> int:
    return sum(m.rec_len for m in tex.mips[:n])


def rebuild(tex: TexturePackage, n: int) -> bytes:
    if n <= 0:
        return tex.data
    if n >= len(tex.streamed):
        raise TextureError('cannot drop every streamed mip')
    gone = tex.mips[:n]
    cut = sum(m.size for m in gone)
    removed = removed_bytes(tex, n)
    b = bytearray(tex.data)
    struct.pack_into('<ii', b, tex.num_mips_off - 4, tex.first_mip + n, len(tex.mips) - n)
    for m in tex.mips[n:]:
        off = m.rec_off + 8 + (16 if m.flags & ue4.SIZE64 else 8)
        struct.pack_into('<q', b, off, m.offset - (cut if m.separate else removed))
    skip = struct.unpack_from('<q', b, tex.skip_loc)[0]
    struct.pack_into('<q', b, tex.skip_loc, skip - removed)
    ex = tex.pkg.exports[0]
    struct.pack_into('<Q', b, ex.pos + 8, ex.serial_size - removed)
    del b[gone[0].rec_off:gone[-1].rec_off + gone[-1].rec_len]
    return bytes(b)


def check(tex: TexturePackage, n: int, data: bytes, scripts: dict[int, str], ubulk_size: int) -> str | None:
    cut = sum(m.size for m in tex.mips[:n])
    try:
        new = parse(zen4.read_package(data, scripts), ubulk_size - cut)
    except (TextureError, zen4.ZenError) as e:
        return str(e)
    if [(m.width, m.height, m.size) for m in new.mips] != [(m.width, m.height, m.size) for m in tex.mips[n:]]:
        return 'mip chain differs after the drop'
    if new.pixel_format != tex.pixel_format or new.first_mip != tex.first_mip + n:
        return 'pixel format or first mip changed'
    return None


def drop_count(tex: TexturePackage, policy) -> int:
    streamed = tex.streamed
    mips = tex.mips
    n = policy.drop
    if policy.max_res is not None:
        m = 0
        while m < len(streamed) - 1 and mips[m].max_dim > policy.max_res:
            m += 1
        n = max(n, m)
    n = min(n, len(streamed) - 1)
    while n > 0 and mips[n].max_dim < policy.min_res:
        n -= 1
    return max(n, 0)


def is_ui(name: str, pkg: zen4.Package) -> bool:
    return '/UI/' in name or 'TEXTUREGROUP_UI' in pkg.names


_SCRIPTS: dict[str, dict[int, str]] = {}


def load_scripts(folder: str) -> dict[int, str]:
    path = os.path.abspath(os.path.join(folder, GLOBAL))
    got = _SCRIPTS.get(path)
    if got is not None:
        return got
    if not os.path.isfile(path):
        raise io.IoStoreError(f'{path} is missing')
    toc = io.Toc(path)
    with io.Reader(toc, ucas_of(path)) as r:
        parts = {toc.chunk_type(i): r.read(i) for i in range(toc.count)}
    try:
        got = zen4.script_objects(parts[7], parts[8], parts[9])
    except (KeyError, struct.error, zen4.ZenError) as e:
        raise io.IoStoreError(f'{path}: cannot read the script objects ({e})') from None
    _SCRIPTS[path] = got
    return got


def ucas_of(utoc: str) -> str:
    return utoc[:-5] + '.ucas'


def package_id(toc: io.Toc, i: int) -> int:
    return struct.unpack_from('<Q', toc.ids[i], 0)[0]


@dataclass
class Texture:
    name: str
    package: int
    bulk: int
    tex: TexturePackage
    drop: int = 0

    @property
    def kept_top(self) -> Mip:
        return self.tex.mips[self.drop]

    @property
    def cut_bytes(self) -> int:
        return sum(m.size for m in self.tex.mips[:self.drop])

    @property
    def new_package_size(self) -> int:
        return len(self.tex.data) - removed_bytes(self.tex, self.drop)


@dataclass
class Plan:
    utoc: str
    ucas: str
    toc: io.Toc
    header_chunk: int | None
    header: zen4.ContainerHeader | None
    scripts: dict[int, str]
    textures: list[Texture]
    skipped: collections.Counter = field(default_factory=collections.Counter)

    @property
    def changed(self) -> list[Texture]:
        return [t for t in self.textures if t.drop]

    def _aligned(self, ks) -> int:
        a = io.BLOCK_ALIGN
        return sum((self.toc.blocks[k].csize + a - 1) // a * a for k in ks)

    def _raw(self, n: int) -> int:
        bs, a = self.toc.header.block_size, io.BLOCK_ALIGN
        return sum((min(bs, n - s) + a - 1) // a * a for s in range(0, n, bs))

    def new_ucas_size(self) -> int:
        toc = self.toc
        bs = toc.header.block_size
        size = self._aligned(range(len(toc.blocks)))
        changed = self.changed
        for t in changed:
            first = toc.offsets[t.bulk] // bs
            size -= self._aligned(range(first, (toc.offsets[t.bulk] + t.cut_bytes) // bs))
            size -= self._aligned(toc.block_range(t.package))
            size += self._raw(t.new_package_size)
        if changed and self.header_chunk is not None:
            size -= self._aligned(toc.block_range(self.header_chunk))
            size += self._raw(toc.lengths[self.header_chunk])
        return size

    def ucas_size(self) -> int:
        return os.path.getsize(self.ucas)

    def saved(self) -> int:
        return self.ucas_size() - self.new_ucas_size()


def texture_chunks(toc: io.Toc) -> list[tuple[str, int, int]]:
    by_name = {v: k for k, v in toc.names.items()}
    export, bulk = toc.type_id('export'), toc.type_id('bulk')
    out = []
    for i in range(toc.count):
        name = toc.names.get(i, '')
        if toc.chunk_type(i) != bulk or not name.endswith('.ubulk'):
            continue
        pkg = by_name.get(name[:-6] + '.uasset')
        if pkg is not None and toc.chunk_type(pkg) == export:
            out.append((name, pkg, i))
    return out


def _open_toc(utoc: str) -> io.Toc:
    toc = io.Toc(utoc)
    if toc.header.version != VERSION:
        raise io.IoStoreError(f'.utoc version {toc.header.version} is not the Stellar Blade layout')
    return toc


def read_header(toc: io.Toc, reader: io.Reader) -> tuple[int | None, zen4.ContainerHeader | None]:
    hs = toc.chunks_of('header')
    if not hs:
        return None, None
    if len(hs) != 1:
        raise io.IoStoreError('several container headers')
    try:
        return hs[0], zen4.read_container_header(reader.read(hs[0]))
    except zen4.ZenError as e:
        raise io.IoStoreError(f'container header: {e}') from None


def _plan_job(job: tuple[str, str, str, list[tuple[str, int, int]], tuple]) -> list:
    utoc, ucas, home, chunks, pol = job
    from ..texpack import DropPolicy
    policy = DropPolicy(*pol)
    scripts = load_scripts(home)
    toc = _worker_toc(utoc)
    out = []
    with io.Reader(toc, ucas) as r:
        for name, pkg, bulk in chunks:
            try:
                p = zen4.read_package(r.read(pkg), scripts)
            except zen4.ZenError as e:
                out.append((name, f'package: {e}'))
                continue
            if [e.cls for e in p.exports] != [TEXTURE_CLASS]:
                out.append((name, 'not a single Texture2D export'))
                continue
            if is_ui(name, p):
                out.append((name, 'UI texture'))
                continue
            try:
                tex = parse(p, toc.lengths[bulk])
            except TextureError as e:
                out.append((name, str(e)))
                continue
            n = drop_count(tex, policy)
            if n:
                err = check(tex, n, rebuild(tex, n), scripts, toc.lengths[bulk])
                if err:
                    raise TextureError(f'{name}: {err}')
            out.append(Texture(name, pkg, bulk, tex, n))
    return out


def make_plan(utoc: str, policy, ucas: str | None = None, progress: Progress | None = None,
              home: str | None = None, workers: int | None = None) -> Plan:
    toc = _open_toc(utoc)
    ucas = ucas or ucas_of(utoc)
    if not os.path.isfile(ucas):
        raise io.IoStoreError(f'{ucas} is missing')
    home = home or os.path.dirname(os.path.abspath(utoc))
    scripts = load_scripts(home)
    with io.Reader(toc, ucas) as r:
        hc, header = read_header(toc, r)
    plan = Plan(utoc, ucas, toc, hc, header, scripts, [])
    chunks = texture_chunks(toc)
    if not chunks:
        return plan
    pol = (policy.drop, policy.max_res, policy.min_res)
    step = 256
    jobs = [(utoc, ucas, home, chunks[k:k + step], pol) for k in range(0, len(chunks), step)]
    done = 0
    with ProcessPoolExecutor(max_workers=_workers(workers)) as ex:
        for job, res in zip(jobs, ex.map(_plan_job, jobs)):
            for x in res:
                if isinstance(x, Texture):
                    plan.textures.append(x)
                else:
                    plan.skipped[x[1]] += 1
            done += len(job[3])
            if progress:
                progress(done, len(chunks))
    if plan.changed and header is None:
        raise io.IoStoreError('container has textures to change but no container header')
    return plan


def _hash_tail(job: tuple[str, list[tuple[int, int, int, str]], int, int]) -> bytes:
    ucas, blocks, head, length = job
    h = hashlib.sha1()
    left = length
    with open(ucas, 'rb') as f:
        for off, csize, usize, method in blocks:
            f.seek(off)
            data = io._decompress(method, f.read(csize), usize)
            piece = memoryview(data)[head:head + left]
            h.update(piece)
            left -= len(piece)
            head = 0
    if left:
        raise io.IoStoreError('bulk chunk is shorter than its length')
    return h.digest()


def _tail_jobs(plan: Plan) -> list[tuple[str, list, int, int]]:
    toc = plan.toc
    bs = toc.header.block_size
    jobs = []
    for t in plan.changed:
        o, ln = toc.offsets[t.bulk], toc.lengths[t.bulk]
        start = o + t.cut_bytes
        ks = range(start // bs, (o + ln - 1) // bs + 1)
        blocks = [(toc.blocks[k].offset, toc.blocks[k].csize, toc.blocks[k].usize,
                   toc.methods[toc.blocks[k].method]) for k in ks]
        jobs.append((plan.ucas, blocks, start % bs, ln - t.cut_bytes))
    return jobs


def _sha1(b: bytes) -> bytes:
    return hashlib.sha1(b).digest()


def _workers(workers: int | None) -> int:
    return workers or max(1, (os.cpu_count() or 2) - 1)


def new_header(plan: Plan, sizes: dict[int, int]) -> bytes:
    h = plan.header
    idx = h.index()
    for pid, size in sizes.items():
        k = idx.get(pid)
        if k is None:
            raise io.IoStoreError(f'package {pid:#018x} is not in the container header')
        h.entries[k].export_bundles_size = size
    return h.pack()


def write(plan: Plan, out_utoc: str, out_ucas: str, hash_progress: Progress | None = None,
          write_progress: Progress | None = None, workers: int | None = None) -> None:
    changed = plan.changed
    toc = plan.toc
    packages = [rebuild(t.tex, t.drop) for t in changed]
    jobs = _tail_jobs(plan)
    total = sum(j[3] for j in jobs) + sum(len(d) for d in packages)
    done = 0
    tails: list[bytes] = []
    with ProcessPoolExecutor(max_workers=_workers(workers)) as ex:
        for job, h in zip(jobs, ex.map(_hash_tail, jobs, chunksize=8)):
            tails.append(h)
            done += job[3]
            if hash_progress:
                hash_progress(done, total)
    changes: dict[int, io.Replacement] = {}
    sizes = {}
    for t, data, th in zip(changed, packages, tails):
        changes[t.package] = io.Replacement(data=data, new_hash=_sha1(data))
        changes[t.bulk] = io.Replacement(skip_bytes=t.cut_bytes, new_hash=th)
        sizes[package_id(toc, t.package)] = len(data)
        done += len(data)
        if hash_progress:
            hash_progress(done, total)
    if changed:
        hb = new_header(plan, sizes)
        changes[plan.header_chunk] = io.Replacement(data=hb, new_hash=_sha1(hb))
    io.write(toc, plan.ucas, out_utoc, out_ucas, changes, write_progress)


_TOC_CACHE: dict[str, io.Toc] = {}


def _worker_toc(utoc: str) -> io.Toc:
    t = _TOC_CACHE.get(utoc)
    if t is None:
        t = _TOC_CACHE[utoc] = io.Toc(utoc)
    return t


def _hash_chunk(job: tuple[str, str, int]) -> tuple[int, bytes]:
    utoc, ucas, i = job
    toc = _worker_toc(utoc)
    with io.Reader(toc, ucas) as r:
        return i, _sha1(r.read(i))


def check_layout(toc: io.Toc, ucas_size: int) -> list[str]:
    errs: list[str] = []
    bs = toc.header.block_size
    used = [False] * len(toc.blocks)
    for i in range(toc.count):
        o, ln = toc.offsets[i], toc.lengths[i]
        for k in toc.block_range(i):
            if k >= len(toc.blocks):
                errs.append(f'chunk {i}: block {k} out of range')
                break
            used[k] = True
            bl = toc.blocks[k]
            if bl.offset + bl.csize > ucas_size:
                errs.append(f'chunk {i}: block {k} runs past the end of the .ucas')
            want = min(bs, o + ln - k * bs)
            if bl.usize != want:
                errs.append(f'chunk {i}: block {k} holds {bl.usize} bytes, expected {want}')
            if bl.method == 0 and bl.csize != bl.usize:
                errs.append(f'chunk {i}: stored block {k} has csize {bl.csize} != usize {bl.usize}')
        if len(errs) > 20:
            return errs
    if not all(used):
        errs.append(f'{used.count(False)} compression blocks are not used by any chunk')
    return errs


def check_header(toc: io.Toc, header: zen4.ContainerHeader | None) -> list[str]:
    if header is None:
        return []
    errs = []
    if header.container_id != toc.header.container_id:
        errs.append('container header id does not match the .utoc')
    export = toc.type_id('export')
    lengths = {package_id(toc, i): toc.lengths[i] for i in range(toc.count) if toc.chunk_type(i) == export}
    for pid, e in zip(header.package_ids, header.entries):
        ln = lengths.get(pid)
        if ln is None:
            errs.append(f'package {pid:#018x} has no export bundle chunk')
        elif ln != e.export_bundles_size:
            errs.append(f'package {pid:#018x}: export bundle size {e.export_bundles_size} != chunk {ln}')
        if len(errs) > 20:
            break
    known = set(header.package_ids)
    for name, pairs in header.cultures:
        missing = sum(1 for _, c in pairs if c not in known)
        if missing:
            errs.append(f'culture {name}: {missing} localized packages are not in the container')
    return errs


def verify(utoc: str, ucas: str | None = None, chunks: list[int] | None = None,
           deep: bool = False, progress: Progress | None = None, workers: int | None = None,
           home: str | None = None) -> list[str]:
    ucas = ucas or ucas_of(utoc)
    try:
        toc = _open_toc(utoc)
    except (io.IoStoreError, OSError) as e:
        return [str(e)]
    errs = check_layout(toc, os.path.getsize(ucas))
    if errs:
        return errs
    strict = set(chunks or ())
    try:
        scripts = load_scripts(home or os.path.dirname(os.path.abspath(utoc)))
        with io.Reader(toc, ucas) as r:
            hc, header = read_header(toc, r)
            errs += check_header(toc, header)
            for name, pkg, bulk in texture_chunks(toc):
                if pkg not in strict and not deep:
                    continue
                try:
                    p = zen4.read_package(r.read(pkg), scripts)
                    if [e.cls for e in p.exports] == [TEXTURE_CLASS]:
                        parse(p, toc.lengths[bulk])
                except TextureError as e:
                    msg = str(e)
                    if pkg in strict or msg.startswith('streamed size'):
                        errs.append(f'{name}: {msg}')
                except zen4.ZenError as e:
                    errs.append(f'{name}: {e}')
    except io.IoStoreError as e:
        return errs + [str(e)]
    targets = list(range(toc.count)) if deep else sorted(strict | ({hc} if hc is not None and strict else set()))
    if targets:
        jobs = [(utoc, ucas, i) for i in targets]
        total = sum(toc.lengths[i] for i in targets)
        done = 0
        with ProcessPoolExecutor(max_workers=_workers(workers)) as ex:
            for i, h in ex.map(_hash_chunk, jobs, chunksize=16):
                if h != toc.chunk_hash(i):
                    errs.append(f'chunk {i} ({toc.names.get(i, "?")}): hash mismatch')
                done += toc.lengths[i]
                if progress:
                    progress(done, total)
    return errs


SETTINGS_PAK = 'pakchunk0-WindowsNoEditor.pak'
SETTINGS_INI = '../../../SB/Config/Windows/WindowsGameUserSettings.ini'
BIAS_KEYS = ('r.Streaming.EnvironmentTextureLODGroupMipBias', 'r.Streaming.CharacterTextureLODGroupMipBias')
_BIAS = re.compile(r'^(?P<key>' + '|'.join(re.escape(k) for k in BIAS_KEYS) + r')(?P<eq>\s*=\s*)(?P<val>-?\d+)',
                   re.MULTILINE)
_MARKER = re.compile(r'^; gow-shrink: texture mip bias lowered by (\d+)\r?\n', re.MULTILINE)
MARKER = '; gow-shrink: texture mip bias lowered by {}'


def bias_offset(text: str) -> int:
    m = _MARKER.search(text)
    return int(m.group(1)) if m else 0


def bias_values(text: str) -> list[tuple[str, int]]:
    return [(m.group('key'), int(m.group('val'))) for m in _BIAS.finditer(text)]


def adjust_bias(text: str, offset: int) -> str:
    old = bias_offset(text)
    if not bias_values(text):
        raise TextureError('the settings ini has no texture mip bias entries')
    body = _MARKER.sub('', text, count=1)
    delta = offset - old

    def fix(m: re.Match) -> str:
        return f'{m.group("key")}{m.group("eq")}{int(m.group("val")) - delta}'

    body = _BIAS.sub(fix, body)
    if offset == 0:
        return body
    nl = '\r\n' if '\r\n' in body else '\n'
    return MARKER.format(offset) + nl + body


def find_utocs(paths: list[str]) -> list[str]:
    return [p for p in io.find_containers(paths)
            if os.path.basename(p).lower() != GLOBAL and os.path.isfile(ucas_of(p))]
