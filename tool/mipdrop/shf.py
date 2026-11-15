from __future__ import annotations

import collections
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import iostore as io
from . import zen
from .b3hash import blake3_160

Progress = Callable[[int, int], None]


@dataclass
class Texture:
    name: str
    package: int
    bulk: int
    tex: zen.TexturePackage
    drop: int = 0

    @property
    def kept_top(self) -> zen.Mip:
        return self.tex.mips[self.drop]

    @property
    def cut_bytes(self) -> int:
        return sum(m.size for m in self.tex.mips[:self.drop])


@dataclass
class Plan:
    utoc: str
    ucas: str
    toc: io.Toc
    textures: list[Texture]
    skipped: collections.Counter = field(default_factory=collections.Counter)

    @property
    def changed(self) -> list[Texture]:
        return [t for t in self.textures if t.drop]

    def skip_blocks(self, t: Texture) -> int:
        return t.cut_bytes // self.toc.header.block_size

    def new_ucas_size(self) -> int:
        toc = self.toc
        bs = toc.header.block_size
        a = io.BLOCK_ALIGN
        size = sum((b.csize + a - 1) // a * a for b in toc.blocks)
        for t in self.changed:
            rng = list(toc.block_range(t.bulk))
            size -= sum((toc.blocks[k].csize + a - 1) // a * a for k in rng[:self.skip_blocks(t)])
            size -= sum((toc.blocks[k].csize + a - 1) // a * a for k in toc.block_range(t.package))
            new_len = len(t.tex.data) - zen.BULK_ENTRY.size * t.drop - sum(m.rec_len for m in t.tex.mips[:t.drop])
            size += sum((min(bs, new_len - s) + a - 1) // a * a for s in range(0, new_len, bs))
        return size

    def saved(self) -> int:
        return self.ucas_size() - self.new_ucas_size()

    def ucas_size(self) -> int:
        return os.path.getsize(self.ucas)


def drop_count(tex: zen.TexturePackage, policy, block_size: int) -> int:
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
    while n > 0 and sum(x.size for x in streamed[:n]) % block_size:
        n -= 1
    return max(n, 0)


def ucas_of(utoc: str) -> str:
    return utoc[:-5] + '.ucas'


def texture_chunks(toc: io.Toc) -> list[tuple[str, int, int]]:
    by_name = {v: k for k, v in toc.names.items()}
    out = []
    for i in range(toc.count):
        name = toc.names.get(i, '')
        if toc.chunk_type(i) != io.TYPE_BULK or not name.endswith('.ubulk'):
            continue
        pkg = by_name.get(name[:-6] + '.uasset')
        if pkg is not None and toc.chunk_type(pkg) == io.TYPE_EXPORT_BUNDLE:
            out.append((name, pkg, i))
    return out


def make_plan(utoc: str, policy, ucas: str | None = None, progress: Progress | None = None) -> Plan:
    toc = io.Toc(utoc)
    ucas = ucas or ucas_of(utoc)
    if not os.path.isfile(ucas):
        raise io.IoStoreError(f'{ucas} is missing')
    plan = Plan(utoc, ucas, toc, [])
    bs = toc.header.block_size
    chunks = texture_chunks(toc)
    with io.Reader(toc, ucas) as r:
        for n, (name, pkg, bulk) in enumerate(chunks):
            try:
                tex = zen.parse(r.read(pkg), toc.lengths[bulk])
            except zen.ZenError as e:
                msg = str(e)
                plan.skipped['not a texture package' if msg.endswith(' exports') else msg] += 1
                continue
            if toc.offsets[bulk] % bs:
                plan.skipped['bulk chunk not block aligned'] += 1
                continue
            t = Texture(name, pkg, bulk, tex)
            t.drop = drop_count(tex, policy, bs)
            plan.textures.append(t)
            if progress:
                progress(n + 1, len(chunks))
    return plan


def _hash_tail(job: tuple[str, list[tuple[int, int, int, str]], int]) -> bytes:
    ucas, blocks, length = job
    out = bytearray()
    with open(ucas, 'rb') as f:
        for off, csize, usize, method in blocks:
            f.seek(off)
            out += io._decompress(method, f.read(csize), usize)
    return blake3_160(memoryview(out)[:length])


def _tail_jobs(plan: Plan) -> list[tuple[str, list, int]]:
    toc = plan.toc
    jobs = []
    for t in plan.changed:
        rng = list(toc.block_range(t.bulk))[plan.skip_blocks(t):]
        blocks = [(toc.blocks[k].offset, toc.blocks[k].csize, toc.blocks[k].usize,
                   toc.methods[toc.blocks[k].method]) for k in rng]
        jobs.append((plan.ucas, blocks, toc.lengths[t.bulk] - t.cut_bytes))
    return jobs


def _workers(workers: int | None) -> int:
    return workers or max(1, (os.cpu_count() or 2) - 1)


def hash_changes(plan: Plan, packages: list[bytes], workers: int | None = None,
                 progress: Progress | None = None) -> tuple[list[bytes], list[bytes]]:
    jobs = _tail_jobs(plan)
    total = sum(j[2] for j in jobs) + sum(len(d) for d in packages)
    done = 0
    tails: list[bytes] = []
    hashes: list[bytes] = []
    with ProcessPoolExecutor(max_workers=_workers(workers)) as ex:
        for job, h in zip(jobs, ex.map(_hash_tail, jobs, chunksize=4)):
            tails.append(h)
            done += job[2]
            if progress:
                progress(done, total)
        for data, h in zip(packages, ex.map(blake3_160, packages, chunksize=64)):
            hashes.append(h)
            done += len(data)
            if progress:
                progress(done, total)
    return tails, hashes


def write(plan: Plan, out_utoc: str, out_ucas: str, hash_progress: Progress | None = None,
          write_progress: Progress | None = None, workers: int | None = None) -> None:
    changed = plan.changed
    packages = [zen.rebuild(t.tex, t.drop) for t in changed]
    tails, hashes = hash_changes(plan, packages, workers, hash_progress)
    changes: dict[int, io.Replacement] = {}
    for t, data, ph, th in zip(changed, packages, hashes, tails):
        changes[t.package] = io.Replacement(data=data, new_hash=ph)
        changes[t.bulk] = io.Replacement(skip_blocks=plan.skip_blocks(t), new_hash=th)
    io.write(plan.toc, plan.ucas, out_utoc, out_ucas, changes, write_progress)


def _hash_chunk(job: tuple[str, str, int]) -> tuple[int, bytes]:
    utoc, ucas, i = job
    toc = _worker_toc(utoc)
    with io.Reader(toc, ucas) as r:
        return i, blake3_160(r.read(i))


_TOC_CACHE: dict[str, io.Toc] = {}


def _worker_toc(utoc: str) -> io.Toc:
    t = _TOC_CACHE.get(utoc)
    if t is None:
        t = _TOC_CACHE[utoc] = io.Toc(utoc)
    return t


def verify(utoc: str, ucas: str | None = None, chunks: list[int] | None = None,
           deep: bool = False, progress: Progress | None = None, workers: int | None = None) -> list[str]:
    errs: list[str] = []
    ucas = ucas or ucas_of(utoc)
    try:
        toc = io.Toc(utoc)
    except (io.IoStoreError, OSError) as e:
        return [str(e)]
    size = os.path.getsize(ucas)
    bs = toc.header.block_size
    used = [False] * len(toc.blocks)
    for i in range(toc.count):
        o, ln = toc.offsets[i], toc.lengths[i]
        if o % bs:
            errs.append(f'chunk {i}: offset {o:#x} is not block aligned')
        for k in toc.block_range(i):
            if k >= len(toc.blocks):
                errs.append(f'chunk {i}: block {k} out of range')
                break
            used[k] = True
            bl = toc.blocks[k]
            if bl.offset + bl.csize > size:
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
    strict = set(chunks or ())
    with io.Reader(toc, ucas) as r:
        for name, pkg, bulk in texture_chunks(toc):
            try:
                zen.parse(r.read(pkg), toc.lengths[bulk])
            except zen.ZenError as e:
                if pkg in strict or str(e).startswith('streamed size'):
                    errs.append(f'{name}: {e}')
            except io.IoStoreError as e:
                errs.append(f'{name}: {e}')
    targets = list(range(toc.count)) if deep else (chunks or [])
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


def find_utocs(paths: list[str]) -> list[str]:
    return [p for p in io.find_containers(paths) if os.path.isfile(ucas_of(p))]
