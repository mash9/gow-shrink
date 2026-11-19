from __future__ import annotations

import collections
import functools
import os
import re
import struct
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable

from ..package import kpka
from ..package.kpka import Entry, Pak, PakError, Progress, path_hash, read_entry, read_head

TEX_MAGIC = b'TEX\0'
TEX_HEADER = 0x28
TEX_MIP = struct.Struct('<QII')
MDF_MAGIC = b'MDF\0'
BASE_PREFIX = 'natives/stm/'
STREAM_PREFIX = 'natives/stm/streaming/'
TEX_PATH = re.compile(rb'(?:[\x20-\x7e]\x00){4,}\.\x00[tT]\x00[eE]\x00[xX]\x00')


class TexError(Exception):
    pass


@dataclass(frozen=True)
class Tex:
    width: int
    height: int
    depth: int
    images: int
    mips: int
    fmt: int
    flags: int
    table: tuple[tuple[int, int, int], ...]

    @property
    def header_size(self) -> int:
        return TEX_HEADER + TEX_MIP.size * len(self.table)

    @property
    def data_size(self) -> int:
        return self.header_size + sum(t[2] for t in self.table)

    def max_dim(self, level: int = 0) -> int:
        return max(self.width >> level, self.height >> level, 1)

    @property
    def has_stream(self) -> bool:
        return bool(self.flags & 1)


def tex_header_size(head: bytes) -> int:
    if len(head) < TEX_HEADER or head[:4] != TEX_MAGIC:
        raise TexError('not a TEX resource')
    return TEX_HEADER + TEX_MIP.size * head[14] * (head[15] >> 4)


def tex_suffix(version: int) -> str:
    return f'.{version}'


def read_tex(f, e: Entry, version: int) -> Tex:
    head = read_head(f, e, 64)
    hs = tex_header_size(head)
    if hs > len(head):
        head = read_head(f, e, hs)
    return parse_tex(head[:hs], version, e.size)


def parse_tex(head: bytes, version: int, size: int | None = None) -> Tex:
    hs = tex_header_size(head)
    found, width, height, depth = struct.unpack_from('<IHHH', head, 4)
    if found != version:
        raise TexError(f'TEX version {found}, expected {version}')
    if len(head) < hs:
        raise TexError('header is cut short')
    images, mips = head[14], head[15] >> 4
    fmt = struct.unpack_from('<I', head, 0x10)[0]
    flags = struct.unpack_from('<I', head, 0x1c)[0]
    table = tuple(TEX_MIP.unpack_from(head, TEX_HEADER + TEX_MIP.size * i) for i in range(images * mips))
    tex = Tex(width, height, depth, images, mips, fmt, flags, table)
    if not mips or not images:
        raise TexError('no mips')
    pos = hs
    for off, pitch, sz in table:
        if off != pos:
            raise TexError('mip data is not contiguous')
        pos += sz
    if size is not None and pos != size:
        raise TexError(f'mip data ends at {pos}, resource size is {size}')
    return tex


def rebuild_tex(data: bytes, drop: int, version: int) -> bytes:
    tex = parse_tex(data, version, len(data))
    if drop <= 0:
        return data
    if drop >= tex.mips:
        raise TexError(f'cannot drop {drop} of {tex.mips} mips')
    if tex.depth != 1:
        raise TexError('volume texture')
    keep = tex.mips - drop
    kept = [tex.table[i * tex.mips + m] for i in range(tex.images) for m in range(drop, tex.mips)]
    hs = TEX_HEADER + TEX_MIP.size * len(kept)
    head = bytearray(data[:TEX_HEADER])
    struct.pack_into('<HH', head, 8, max(tex.width >> drop, 1), max(tex.height >> drop, 1))
    head[15] = (keep << 4) | (data[15] & 0xf)
    body = []
    pos = hs
    for off, pitch, sz in kept:
        head += TEX_MIP.pack(pos, pitch, sz)
        body.append(data[off:off + sz])
        pos += sz
    return bytes(head) + b''.join(body)


def check_tex(version: int, data: bytes) -> str | None:
    if data[:4] != TEX_MAGIC or len(data) < TEX_HEADER:
        return None
    if struct.unpack_from('<I', data, 4)[0] != version or not data[14] or not data[15] >> 4:
        return None
    try:
        parse_tex(data, version, len(data))
    except TexError as e:
        return f'texture: {e}'
    return None


def _names_job(job: tuple[str, list[tuple[int, int, int, int]]]) -> set[str]:
    path, items = job
    out: set[str] = set()
    with open(path, 'rb') as f:
        for off, cs, size, attr in items:
            e = Entry(0, 0, off, cs, size, attr, 0)
            if read_head(f, e, 4) != MDF_MAGIC:
                continue
            try:
                data = read_entry(f, e)
            except (PakError, zlib.error):
                continue
            for m in TEX_PATH.findall(data):
                out.add(m.decode('utf-16le'))
    return out


def clean_name(name: str) -> str:
    n = name.replace('\\', '/').lstrip('@/')
    i = n.lower().find(BASE_PREFIX)
    if i >= 0:
        n = n[i + len(BASE_PREFIX):]
    if n.lower().startswith('streaming/'):
        n = n[len('streaming/'):]
    return n


def texture_names(paks: dict[str, Pak], workers: int | None = None, progress: Progress | None = None) -> set[str]:
    jobs = []
    for src, pak in paks.items():
        items = [(e.offset, e.csize, e.size, e.attr) for e in pak.unique() if e.size < (16 << 20)]
        jobs += [(src, items[k:k + 2000]) for k in range(0, len(items), 2000)]
    names: set[str] = set()
    with ProcessPoolExecutor(max_workers=kpka.workers_for(workers)) as ex:
        for n, found in enumerate(ex.map(_names_job, jobs), 1):
            names |= found
            if progress:
                progress(n, len(jobs))
    return {clean_name(n) for n in names}


def stream_map(names: Iterable[str], version: int) -> dict[tuple[int, int], tuple[int, int]]:
    suffix = tex_suffix(version)
    out = {}
    for n in names:
        out[path_hash(STREAM_PREFIX + n + suffix)] = path_hash(BASE_PREFIX + n + suffix)
    return out


def drop_count(tex: Tex, policy, floor_dim: int) -> int:
    n = policy.drop
    if policy.max_res is not None:
        m = 0
        while m < tex.mips - 1 and tex.max_dim(m) > policy.max_res:
            m += 1
        n = max(n, m)
    n = min(n, tex.mips - 1)
    while n > 0 and tex.max_dim(n) < policy.min_res:
        n -= 1
    while n > 0 and tex.max_dim(n) < floor_dim:
        n -= 1
    return max(n, 0)


@dataclass
class Texture:
    key: tuple[int, int]
    blob: tuple[int, int]
    tex: Tex
    drop: int

    @property
    def kept_dim(self) -> int:
        return self.tex.max_dim(self.drop)


@dataclass
class PakPlan:
    path: str
    source: str
    pak: Pak
    version: int
    textures: list[Texture] = field(default_factory=list)
    drops: dict[tuple[int, int], int] = field(default_factory=dict)
    skipped: collections.Counter = field(default_factory=collections.Counter)

    @property
    def changed(self) -> list[Texture]:
        return [t for t in self.textures if t.blob in self.drops]

    def estimate(self) -> int:
        saved = 0
        for t in self.textures:
            if t.blob not in self.drops:
                continue
            removed = sum(t.tex.table[i * t.tex.mips + m][2] for i in range(t.tex.images) for m in range(t.drop))
            saved += int(t.blob[1] * removed / max(t.tex.data_size, 1))
        return self.pak.file_size - saved


@dataclass
class Plan:
    names: int
    streams: int
    paks: list[PakPlan]


def base_headers(paks: dict[str, Pak], keys: set[tuple[int, int]], version: int) -> dict[tuple[int, int], Tex]:
    out: dict[tuple[int, int], Tex] = {}
    for src in sorted(paks, key=kpka.pak_order):
        pak = paks[src]
        with open(src, 'rb') as f:
            for e in pak.entries:
                if e.key not in keys:
                    continue
                try:
                    out[e.key] = read_tex(f, e, version)
                except TexError:
                    continue
    return out


def make_plan(targets: list[str], context: list[str], policy, version: int, sources: dict[str, str] | None = None,
              workers: int | None = None, progress: Progress | None = None) -> Plan:
    sources = sources or {}
    paks: dict[str, Pak] = {}
    for p in dict.fromkeys(context + targets):
        src = sources.get(p, p)
        paks[src] = Pak.open(src)
    names = texture_names(paks, workers, progress)
    smap = stream_map(names, version)
    present = {e.key for pak in paks.values() for e in pak.entries}
    smap = {s: b for s, b in smap.items() if s in present and b in present}
    bases = base_headers(paks, set(smap.values()), version)
    plan = Plan(len(names), len(smap), [])
    for path in targets:
        src = sources.get(path, path)
        pak = paks[src]
        pp = PakPlan(path, src, pak, version)
        errs = pak.check_layout()
        if errs:
            raise PakError(f'{os.path.basename(src)}: ' + '; '.join(errs[:3]))
        by_blob: dict[tuple[int, int], list[Entry]] = collections.defaultdict(list)
        for e in pak.entries:
            by_blob[e.blob].append(e)
        with open(src, 'rb') as f:
            for blob, group in by_blob.items():
                if not any(e.key in smap for e in group):
                    continue
                e = group[0]
                try:
                    tex = read_tex(f, e, version)
                except TexError as ex:
                    pp.skipped[f'streamed texture: {ex}'] += 1
                    continue
                linked = [bases.get(smap[g.key]) if g.key in smap else None for g in group]
                if any(b is None for b in linked) and len(group) > 1:
                    pp.skipped['data shared with another resource'] += 1
                    continue
                base = linked[0]
                if base is None:
                    pp.skipped['base texture not found'] += 1
                    continue
                if any(b.max_dim() != base.max_dim() for b in linked):
                    pp.skipped['data shared with another resource'] += 1
                    continue
                if tex.depth != 1:
                    pp.skipped['volume texture'] += 1
                    continue
                if base.fmt != tex.fmt or base.images != tex.images:
                    pp.skipped['base texture has another format'] += 1
                    continue
                n = drop_count(tex, policy, base.max_dim())
                pp.textures.append(Texture(e.key, blob, tex, n))
                if n:
                    pp.drops[blob] = n
        plan.paks.append(pp)
    return plan


def _rebuild_job(job: tuple[str, int, int, int, int, int, int]) -> tuple[bytes, int]:
    src, off, cs, size, attr, drop, version = job
    e = Entry(0, 0, off, cs, size, attr, 0)
    with open(src, 'rb') as f:
        data = read_entry(f, e)
    new = rebuild_tex(data, drop, version)
    return kpka.encode(e.method, new), len(new)


def write(pp: PakPlan, out: str, progress: Progress | None = None, workers: int | None = None) -> dict:
    sizes = {e.blob: (e.size, e.attr) for e in pp.pak.entries}
    changes = {b: (pp.source, b[0], b[1], sizes[b][0], sizes[b][1], n, pp.version) for b, n in pp.drops.items()}
    return kpka.write(pp.pak, pp.source, out, changes, _rebuild_job, progress, workers)


def verify(path: str, version: int, crcs: dict | None = None, deep: bool = True, deep_keys: set | None = None,
           progress: Progress | None = None, workers: int | None = None) -> list[str]:
    return kpka.verify(path, crcs, deep, deep_keys, functools.partial(check_tex, version), progress, workers)
