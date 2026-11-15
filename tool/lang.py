from __future__ import annotations

import glob
import os
import shutil
from dataclasses import dataclass, field


class LangError(Exception):
    pass


@dataclass(frozen=True)
class LangConfig:
    table: str
    items: tuple[str, ...]
    voices: dict[int, str] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: dict) -> 'LangConfig':
        return cls(raw['table'], tuple(raw['items']),
                   {int(k): v for k, v in raw.get('voices', {}).items()})


@dataclass
class TextLanguage:
    id: int
    code: str
    name: str
    voice_id: int


def _native(rel: str) -> str:
    return os.path.join(*rel.replace('\\', '/').split('/'))


def read_language_table(exec_dir: str, cfg: LangConfig) -> list[TextLanguage]:
    files = sorted(glob.glob(os.path.join(exec_dir, _native(cfg.table))))
    if not files:
        return []
    out = []
    with open(files[0], encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4 and parts[0].isdigit() and parts[3].isdigit():
                out.append(TextLanguage(int(parts[0]), parts[1], parts[2], int(parts[3])))
    return out


def voice_folder_for(table: list[TextLanguage], code: str, cfg: LangConfig) -> str:
    by_code = {t.code.lower(): t for t in table}
    t = by_code.get(code.lower())
    if t is None:
        raise LangError(f'unknown language code {code!r}; known: {", ".join(sorted(by_code))}')
    name = cfg.voices.get(t.voice_id)
    if name is None:
        raise LangError(f'no voice language known for {code!r} (voice id {t.voice_id})')
    return name


def path_size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total


def installed_languages(exec_dir: str, cfg: LangConfig) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {}
    for name in sorted(set(cfg.voices.values())):
        for item in cfg.items:
            path = os.path.join(exec_dir, _native(item.replace('{lang}', name)))
            if os.path.exists(path):
                out.setdefault(name, []).append((path, path_size(path)))
    return out


def remove_path(path: str) -> None:
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
