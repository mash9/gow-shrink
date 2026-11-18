from __future__ import annotations

import collections
import os
import struct
from dataclasses import dataclass, field

from ..package import kpka
from ..package.kpka import Entry, Pak, PakError, Progress, read_entry, read_head

PACK_MAGIC = b'AKPK'
BANK_MAGIC = b'BKHD'
SFX = 'sfx'
PACK_HEADER = struct.Struct('<4sIIIIII')
PACK_TABLES = (20, 20, 24)
PACK_LANG_AT = (16, 16, 20)


class SoundError(Exception):
    pass


def wwise_id(name: str) -> int:
    h = 2166136261
    for b in name.lower().encode():
        h = (h * 16777619) & 0xffffffff
        h ^= b
    return h


@dataclass(frozen=True)
class PackInfo:
    header_size: int
    names: dict[int, str]
    used: frozenset[str]
    files: int


def pack_header_size(head: bytes) -> int:
    if len(head) < 8 or head[:4] != PACK_MAGIC:
        raise SoundError('not an AKPK pack')
    return 8 + struct.unpack_from('<I', head, 4)[0]


def parse_pack(head: bytes) -> PackInfo:
    if len(head) < PACK_HEADER.size:
        raise SoundError('pack header is cut short')
    magic, hs, version, lms, *sizes = PACK_HEADER.unpack_from(head)
    if version != 1:
        raise SoundError(f'AKPK version {version}')
    if hs != PACK_HEADER.size - 8 + lms + sum(sizes) or len(head) < 8 + hs:
        raise SoundError('pack header sizes do not add up')
    base = PACK_HEADER.size
    count = struct.unpack_from('<I', head, base)[0]
    names = {}
    for i in range(count):
        so, lid = struct.unpack_from('<II', head, base + 4 + i * 8)
        raw = head[base + so:base + lms]
        if raw[1:2] == b'\0':
            names[lid] = raw.decode('utf-16le', errors='replace').split('\0')[0]
        else:
            names[lid] = raw.split(b'\0')[0].decode(errors='replace')
    used = set()
    files = 0
    pos = base + lms
    for size, esz, at in zip(sizes, PACK_TABLES, PACK_LANG_AT):
        if size >= 4:
            n = struct.unpack_from('<I', head, pos)[0]
            if 4 + n * esz != size:
                raise SoundError('pack table size does not match its entry count')
            for i in range(n):
                lid = struct.unpack_from('<I', head, pos + 4 + i * esz + at)[0]
                used.add(names.get(lid, f'#{lid}'))
            files += n
        pos += size
    return PackInfo(hs + 8, names, frozenset(used), files)


def pack_stub(head: bytes) -> bytes:
    lms = struct.unpack_from('<I', head, 12)[0]
    langmap = head[PACK_HEADER.size:PACK_HEADER.size + lms]
    hs = PACK_HEADER.size - 8 + lms + 12
    return PACK_HEADER.pack(PACK_MAGIC, hs, 1, lms, 4, 4, 4) + langmap + b'\0' * 12


def bank_chunks(data: bytes) -> list[tuple[bytes, int, int]]:
    out = []
    pos = 0
    while pos + 8 <= len(data):
        tag, size = struct.unpack_from('<4sI', data, pos)
        out.append((tag, pos, size))
        pos += 8 + size
    if pos != len(data):
        raise SoundError('bank chunks do not cover the file')
    return out


def bank_language(head: bytes) -> int:
    if len(head) < 20 or head[:4] != BANK_MAGIC:
        raise SoundError('not a BKHD bank')
    return struct.unpack_from('<I', head, 16)[0]


def bank_stub(data: bytes) -> bytes:
    if data[:4] != BANK_MAGIC:
        raise SoundError('not a BKHD bank')
    size = struct.unpack_from('<I', data, 4)[0]
    return data[:8 + size]


def check_sound(data: bytes) -> str | None:
    try:
        if data[:4] == PACK_MAGIC:
            info = parse_pack(data[:pack_header_size(data)])
            if info.files == 0 and len(data) != info.header_size:
                return 'empty pack has trailing data'
        elif data[:4] == BANK_MAGIC:
            bank_chunks(data)
    except (SoundError, struct.error) as e:
        return str(e)
    return None


@dataclass
class Sound:
    blob: tuple[int, int]
    keys: list[tuple[int, int]]
    kind: str
    languages: frozenset[str]
    csize: int
    stub: bool


@dataclass
class PakSounds:
    path: str
    source: str
    pak: Pak
    sounds: list[Sound] = field(default_factory=list)
    skipped: collections.Counter = field(default_factory=collections.Counter)
    remove: list[Sound] = field(default_factory=list)

    def saved(self) -> int:
        return sum(s.csize for s in self.remove)


def scan_pak(path: str, source: str, known: set[str]) -> PakSounds:
    pak = Pak.open(source)
    errs = pak.check_layout()
    if errs:
        raise PakError(f'{os.path.basename(source)}: ' + '; '.join(errs[:3]))
    ps = PakSounds(path, source, pak)
    groups: dict[tuple[int, int], list[Entry]] = collections.defaultdict(list)
    for e in pak.entries:
        groups[e.blob].append(e)
    with open(source, 'rb') as f:
        for blob, group in groups.items():
            e = group[0]
            head = read_head(f, e, 32)
            keys = [g.key for g in group]
            try:
                if head[:4] == PACK_MAGIC:
                    hs = pack_header_size(head)
                    info = parse_pack(read_head(f, e, hs))
                    known.update(n for n in info.names.values())
                    langs = frozenset(n for n in info.used if n != SFX)
                    ps.sounds.append(Sound(blob, keys, 'pack', langs, e.csize, info.files == 0))
                elif head[:4] == BANK_MAGIC:
                    lid = bank_language(head)
                    stub = e.size == 8 + struct.unpack_from('<I', head, 4)[0]
                    ps.sounds.append(Sound(blob, keys, 'bank', frozenset({f'#{lid:08x}'}), e.csize, stub))
            except SoundError as ex:
                ps.skipped[str(ex)] += 1
    return ps


def name_banks(scans: list[PakSounds], known: set[str]) -> None:
    by_id = {f'#{wwise_id(n):08x}': n for n in known}
    for ps in scans:
        for s in ps.sounds:
            if s.kind != 'bank':
                continue
            (tag,) = s.languages
            name = by_id.get(tag)
            if name is None:
                ps.skipped[f'bank with unknown language id {tag[1:]}'] += 1
            s.languages = frozenset() if name in (None, SFX) else frozenset({name})


def scan(targets: list[str], sources: dict[str, str] | None = None) -> list[PakSounds]:
    sources = sources or {}
    known: set[str] = {SFX}
    scans = [scan_pak(p, sources.get(p, p), known) for p in targets]
    name_banks(scans, known)
    return scans


def installed(scans: list[PakSounds]) -> dict[str, tuple[int, int, int]]:
    out: dict[str, list[int]] = {}
    for ps in scans:
        for s in ps.sounds:
            if s.stub or len(s.languages) != 1:
                continue
            (name,) = s.languages
            row = out.setdefault(name, [0, 0, 0])
            row[0 if s.kind == 'pack' else 1] += 1
            row[2] += s.csize
    return {k: tuple(v) for k, v in sorted(out.items())}


def plan_removal(scans: list[PakSounds], keep: set[str]) -> None:
    for ps in scans:
        ps.remove = [s for s in ps.sounds if not s.stub and s.languages and not (s.languages & keep)]


def _stub_job(job: tuple[str, str, int, int, int, int]) -> tuple[bytes, int]:
    kind, src, off, cs, size, attr = job
    e = Entry(0, 0, off, cs, size, attr, 0)
    with open(src, 'rb') as f:
        if kind == 'pack':
            head = read_head(f, e, 8)
            data = pack_stub(read_head(f, e, pack_header_size(head)))
        else:
            data = bank_stub(read_entry(f, e))
    return kpka.encode(e.method, data), len(data)


def write(ps: PakSounds, out: str, progress: Progress | None = None, workers: int | None = None) -> dict:
    meta = {e.blob: (e.size, e.attr) for e in ps.pak.entries}
    changes = {s.blob: (s.kind, ps.source, s.blob[0], s.blob[1], *meta[s.blob]) for s in ps.remove}
    return kpka.write(ps.pak, ps.source, out, changes, _stub_job, progress, workers)


def verify(path: str, crcs: dict | None = None, deep: bool = True, deep_keys: set | None = None,
           progress: Progress | None = None, workers: int | None = None) -> list[str]:
    return kpka.verify(path, crcs, deep, deep_keys, check_sound, progress, workers)
