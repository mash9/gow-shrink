from __future__ import annotations

import collections
import hashlib
import os
import re
from dataclasses import dataclass, field

from ..mipdrop import sb as msb
from ..package import iostore as io
from ..package import zen4

BASE = 'korean'
VOICES = {
    'en': 'english', 'en-gb': 'english', 'de': 'german', 'es': 'spanish(spain)', 'es-mx': 'spanish(mexico)',
    'fr': 'french', 'it': 'italian', 'ja': 'japanese', 'pt-br': 'portuguese(brazil)',
    'zh-hans': 'chinese(simplified)',
}
ALIASES = {'ko': BASE, 'kr': BASE, 'jp': 'japanese', 'zh': 'chinese(simplified)', 'chinese': 'chinese(simplified)',
           'cn': 'chinese(simplified)', 'pt': 'portuguese(brazil)', 'portuguese': 'portuguese(brazil)',
           'br': 'portuguese(brazil)', 'mx': 'spanish(mexico)'}
_L10N = re.compile(r'/Content/L10N/([^/]+)/', re.IGNORECASE)


def voice_of(culture: str) -> str:
    return VOICES.get(culture.lower(), culture.lower())


def resolve(want: str) -> str:
    w = want.lower()
    return ALIASES.get(w) or VOICES.get(w) or w


@dataclass
class ContainerScan:
    utoc: str
    ucas: str
    toc: io.Toc
    header_chunk: int | None
    header: zen4.ContainerHeader | None
    chunks: dict[str, set[int]] = field(default_factory=dict)
    packages: dict[str, set[int]] = field(default_factory=dict)
    cultures: dict[str, set[str]] = field(default_factory=dict)
    remove: set[int] = field(default_factory=set)
    remove_packages: set[int] = field(default_factory=set)

    def size(self, lang: str) -> int:
        a = io.BLOCK_ALIGN
        return sum((self.toc.blocks[k].csize + a - 1) // a * a
                   for i in self.chunks.get(lang, ()) for k in self.toc.block_range(i))

    def saved(self) -> int:
        a = io.BLOCK_ALIGN
        return sum((self.toc.blocks[k].csize + a - 1) // a * a for i in self.remove for k in self.toc.block_range(i))


@dataclass
class Language:
    files: int = 0
    size: int = 0
    cultures: set[str] = field(default_factory=set)


def scan(utocs: list[str]) -> list[ContainerScan]:
    out = []
    for utoc in utocs:
        toc = msb._open_toc(utoc)
        ucas = msb.ucas_of(utoc)
        with io.Reader(toc, ucas) as r:
            hc, header = msb.read_header(toc, r)
        cs = ContainerScan(utoc, ucas, toc, hc, header)
        by_pid: dict[int, list[int]] = collections.defaultdict(list)
        for i in range(toc.count):
            if i != hc:
                by_pid[msb.package_id(toc, i)].append(i)
        export = toc.type_id('export')
        for i, name in toc.names.items():
            m = _L10N.search(name)
            if not m or toc.chunk_type(i) != export:
                continue
            lang = voice_of(m.group(1))
            pid = msb.package_id(toc, i)
            cs.packages.setdefault(lang, set()).add(pid)
            cs.chunks.setdefault(lang, set()).update(by_pid[pid])
            cs.cultures.setdefault(lang, set()).add(m.group(1))
        if cs.packages and header is None:
            raise io.IoStoreError(f'{os.path.basename(utoc)}: localized packages but no container header')
        out.append(cs)
    return out


def installed(scans: list[ContainerScan]) -> dict[str, Language]:
    langs: dict[str, Language] = collections.defaultdict(Language)
    for cs in scans:
        for lang, chunks in cs.chunks.items():
            g = langs[lang]
            g.files += len(cs.packages[lang])
            g.size += cs.size(lang)
            g.cultures |= cs.cultures[lang]
    return dict(sorted(langs.items()))


def plan_removal(scans: list[ContainerScan], keep: set[str]) -> None:
    for cs in scans:
        drop = [lang for lang in cs.packages if lang not in keep]
        cs.remove = {i for lang in drop for i in cs.chunks[lang]}
        cs.remove_packages = {p for lang in drop for p in cs.packages[lang]}


def write(cs: ContainerScan, out_utoc: str, out_ucas: str, progress=None) -> int:
    h = cs.header.without(cs.remove_packages)
    data = h.pack()
    changes = {cs.header_chunk: io.Replacement(data=data, new_hash=hashlib.sha1(data).digest())}
    io.write(cs.toc, cs.ucas, out_utoc, out_ucas, changes, progress, remove=cs.remove)
    return cs.header_chunk - sum(1 for i in cs.remove if i < cs.header_chunk)
