from __future__ import annotations

import os
import zlib
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
