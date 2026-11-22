from __future__ import annotations

import collections
from dataclasses import dataclass, field

from ..package import uepak

VOICE_DIR = '/WwiseAudio/Windows/'
L10N_DIR = '/AtomicHeart/Content/L10N/'
ALIASES = {'russian': 'Ru'}
LIPSYNC = {'English(US)': 'en-US', 'Ru': 'ru-RU', 'Portuguese(BR)': 'pt-BR', 'German(DE)': 'de-DE'}


@dataclass
class PakScan:
    path: str
    pak: uepak.Pak
    voice: dict[str, dict[str, int]] = field(default_factory=dict)
    lipsync: dict[str, dict[str, int]] = field(default_factory=dict)
    remove: set[str] = field(default_factory=set)

    def saved(self) -> int:
        sizes = {p: e.usize for p, e in self.pak.files()}
        return sum(sizes[p] for p in self.remove)


@dataclass
class Language:
    files: int = 0
    size: int = 0
    banks: int = 0
    lipsync_files: int = 0
    lipsync_size: int = 0


def _folder(path: str, root: str) -> str | None:
    i = path.find(root)
    if i < 0:
        return None
    rest = path[i + len(root):]
    return rest.split('/', 1)[0] if '/' in rest else None


def scan(paks: list[str]) -> list[PakScan]:
    cultures = set(LIPSYNC.values())
    out = []
    for path in paks:
        ps = PakScan(path, uepak.Pak.open(path))
        for p, e in ps.pak.files():
            lang = _folder(p, VOICE_DIR)
            if lang:
                ps.voice.setdefault(lang, {})[p] = e.usize
                continue
            culture = _folder(p, L10N_DIR)
            if culture in cultures:
                ps.lipsync.setdefault(culture, {})[p] = e.usize
        out.append(ps)
    return out


def installed(scans: list[PakScan]) -> dict[str, Language]:
    langs: dict[str, Language] = collections.defaultdict(Language)
    for ps in scans:
        for lang, files in ps.voice.items():
            g = langs[lang]
            g.files += len(files)
            g.size += sum(files.values())
            g.banks += sum(1 for p in files if p.lower().endswith('.bnk'))
    out = {lang: g for lang, g in sorted(langs.items()) if g.banks}
    for ps in scans:
        for lang, g in out.items():
            files = ps.lipsync.get(LIPSYNC.get(lang, ''), {})
            g.lipsync_files += len(files)
            g.lipsync_size += sum(files.values())
    return out


def resolve(want: str) -> str:
    return ALIASES.get(want.lower(), want)


def plan_removal(scans: list[PakScan], keep: set[str]) -> None:
    drop = set(installed(scans)) - keep
    cultures = {LIPSYNC[lang] for lang in drop if lang in LIPSYNC}
    for ps in scans:
        ps.remove = {p for lang in drop for p in ps.voice.get(lang, {})}
        ps.remove |= {p for c in cultures for p in ps.lipsync.get(c, {})}
