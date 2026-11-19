from __future__ import annotations

import os
import zlib
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

from ..package import kpka
from ..package.kpka import Pak, PakError, Progress


@dataclass
class PakPrune:
    path: str
    source: str
    pak: Pak
    remove: set[tuple[int, int]] = field(default_factory=set)
    winners: dict[tuple[int, int], str] = field(default_factory=dict)

    def kept_blobs(self) -> set[tuple[int, int]]:
        return {e.blob for e in self.pak.entries if e.key not in self.remove}

    def saved(self) -> int:
        kept = self.kept_blobs()
        freed = sum(b[1] for b in {e.blob for e in self.pak.entries} - kept)
        return freed + len(self.remove) * kpka.PAK_ENTRY.size

    @property
    def emptied(self) -> bool:
        return bool(self.remove) and len(self.remove) == len({e.key for e in self.pak.entries})


def is_dlc(path: str) -> bool:
    return os.path.basename(os.path.dirname(os.path.abspath(path))).lower() == 'dlc'


def main_chain(paks: list[str]) -> list[str]:
    return sorted((p for p in paks if not is_dlc(p)), key=kpka.pak_order)


def plan(chain: list[str], sources: dict[str, str] | None = None) -> list[PakPrune]:
    sources = sources or {}
    out = []
    for p in chain:
        src = sources.get(p, p)
        pak = Pak.open(src)
        errs = pak.check_layout()
        if errs:
            raise PakError(f'{os.path.basename(src)}: ' + '; '.join(errs[:3]))
        keys = [e.key for e in pak.entries]
        if len(set(keys)) != len(keys):
            raise PakError(f'{os.path.basename(src)}: the same path appears twice in one pak')
        out.append(PakPrune(p, src, pak))
    winner: dict[tuple[int, int], str] = {}
    for d in out:
        for e in d.pak.entries:
            winner[e.key] = d.path
    for d in out:
        for e in d.pak.entries:
            w = winner[e.key]
            if w != d.path:
                d.remove.add(e.key)
                d.winners[e.key] = w
    return out


CompareItem = tuple[tuple[int, int], int, int, int, int, int, int, int]


def _same_raw(fa, a_off: int, fb, b_off: int, size: int) -> bool:
    fa.seek(a_off)
    fb.seek(b_off)
    left = size
    while left:
        n = min(left, kpka.COPY_CHUNK)
        if fa.read(n) != fb.read(n):
            return False
        left -= n
    return True


def _compare_job(job: tuple[str, str, list[CompareItem]]) -> tuple[list[tuple[int, int]], int]:
    dlc_src, base_src, items = job
    same = []
    done = 0
    with open(dlc_src, 'rb') as fd, open(base_src, 'rb') as fb:
        for key, d_off, d_cs, d_attr, b_off, b_cs, b_attr, size in items:
            done += d_cs
            dm, bm = d_attr & 0xf, b_attr & 0xf
            if dm == bm and d_cs == b_cs and _same_raw(fd, d_off, fb, b_off, d_cs):
                same.append(key)
                continue
            if dm == bm == kpka.METHOD_STORE:
                continue
            try:
                fd.seek(d_off)
                a = kpka.decode(dm, fd.read(d_cs), size)
                fb.seek(b_off)
                b = kpka.decode(bm, fb.read(b_cs), size)
            except (PakError, zlib.error):
                continue
            if a == b:
                same.append(key)
    return same, done


def dlc_plan(chain: list[str], dlc: list[str], sources: dict[str, str] | None = None,
             progress: Progress | None = None, workers: int | None = None) -> list[PakPrune]:
    sources = sources or {}
    base: dict[tuple[int, int], tuple[str, kpka.Entry]] = {}
    for p in sorted(chain, key=kpka.pak_order):
        for e in Pak.open(p).entries:
            base[e.key] = (p, e)
    out = []
    jobs = []
    for p in sorted(dlc, key=kpka.pak_order):
        src = sources.get(p, p)
        pak = Pak.open(src)
        errs = pak.check_layout()
        if errs:
            raise PakError(f'{os.path.basename(src)}: ' + '; '.join(errs[:3]))
        keys = [e.key for e in pak.entries]
        if len(set(keys)) != len(keys):
            raise PakError(f'{os.path.basename(src)}: the same path appears twice in one pak')
        d = PakPrune(p, src, pak)
        out.append(d)
        by_base: dict[str, list[CompareItem]] = {}
        for e in pak.entries:
            hit = base.get(e.key)
            if hit is None or hit[1].size != e.size:
                continue
            bp, be = hit
            d.winners[e.key] = bp
            by_base.setdefault(bp, []).append(
                (e.key, e.offset, e.csize, e.attr, be.offset, be.csize, be.attr, e.size))
        for bp, items in by_base.items():
            items.sort(key=lambda t: t[1])
            chunk: list[CompareItem] = []
            weight = 0
            for it in items:
                chunk.append(it)
                weight += it[2]
                if len(chunk) >= 256 or weight >= (256 << 20):
                    jobs.append((len(out) - 1, (src, bp, chunk)))
                    chunk, weight = [], 0
            if chunk:
                jobs.append((len(out) - 1, (src, bp, chunk)))
    total = sum(it[2] for _, j in jobs for it in j[2])
    done = 0
    with ProcessPoolExecutor(max_workers=kpka.workers_for(workers)) as ex:
        for (i, _), (same, n) in zip(jobs, ex.map(_compare_job, [j for _, j in jobs])):
            out[i].remove.update(same)
            done += n
            if progress:
                progress(done, total)
    for d in out:
        d.winners = {k: v for k, v in d.winners.items() if k in d.remove}
    return out


def check_winners(plans: list[PakPrune], progress: Progress | None = None) -> list[str]:
    by_path = {d.path: d for d in plans}
    order = {d.path: i for i, d in enumerate(plans)}
    index = {d.path: {e.key: e for e in d.pak.entries} for d in plans}
    errs = []
    todo = [(d, e) for d in plans for e in d.pak.entries if e.key in d.remove]
    handles = {d.path: open(d.source, 'rb') for d in plans}
    try:
        for n, (d, e) in enumerate(todo, 1):
            w = by_path[d.winners[e.key]]
            if order[w.path] <= order[d.path]:
                errs.append(f'{os.path.basename(d.path)}: {e.lo:08x}{e.hi:08x} is not overridden by a later pak')
                continue
            we = index[w.path][e.key]
            try:
                head_old = kpka.read_head(handles[d.path], e, 4)
                data = kpka.read_entry(handles[w.path], we)
            except (PakError, zlib.error) as ex:
                errs.append(f'{os.path.basename(w.path)}: {e.lo:08x}{e.hi:08x} does not decode: {ex}')
                continue
            if head_old[:4] != data[:4]:
                errs.append(f'{e.lo:08x}{e.hi:08x}: resource type differs between {os.path.basename(d.path)} '
                            f'and {os.path.basename(w.path)}')
            if progress:
                progress(n, len(todo))
            if len(errs) > 20:
                break
    finally:
        for f in handles.values():
            f.close()
    return errs


def write(d: PakPrune, out: str, progress: Progress | None = None, workers: int | None = None) -> dict:
    return kpka.write(d.pak, d.source, out, {}, None, progress, workers, remove=d.remove)


def verify(path: str, crcs: dict | None = None, progress: Progress | None = None,
           workers: int | None = None) -> list[str]:
    return kpka.verify(path, crcs, deep=False, progress=progress, workers=workers)
