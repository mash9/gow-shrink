"""Remove the voice-over languages you do not use.

Per-language audio lives in exactly two places under `exec`:
  * `sound\\pc_le\\<language>\\`            (.wem files, 0.22-0.29 GiB each)
  * `wad\\pc_le\\soundbanks\\<language>\\`   (.sbp files, about 0.045 GiB each)

`exec\\languages\\LANGS_*.txt` maps each text language to the voice language it uses
(`<id> <code> <NAME> <voice id>`); e.g. Korean text uses the English voice.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

LANG_DIRS = (os.path.join('sound', 'pc_le'), os.path.join('wad', 'pc_le', 'soundbanks'))

# voice folder names used by the game, keyed by language code
CODE_TO_FOLDER = {
    'en': 'english(us)', 'fr': 'french(france)', 'es': 'spanish(spain)', 'de': 'german',
    'it': 'italian', 'pt': 'portuguese(portugal)', 'ru': 'russian', 'pl': 'polish',
    'pt-BR': 'portuguese(brazil)', 'es-LA': 'spanish(mexico)', 'el': 'greek', 'ja': 'japanese',
}


class LangError(Exception):
    pass


@dataclass
class TextLanguage:
    id: int
    code: str
    name: str
    voice_id: int


def read_language_table(exec_dir: str) -> list[TextLanguage]:
    files = glob.glob(os.path.join(exec_dir, 'languages', 'LANGS_*.txt'))
    if not files:
        return []
    out = []
    with open(files[0], encoding='utf-8', errors='replace') as f:
        for line in f:
            parts = line.split()
            if len(parts) == 4 and parts[0].isdigit() and parts[3].isdigit():
                out.append(TextLanguage(int(parts[0]), parts[1], parts[2], int(parts[3])))
    return out


def voice_folder_for(table: list[TextLanguage], code: str) -> str:
    """Voice folder used by text language `code` (e.g. 'ko' -> 'english(us)')."""
    by_code = {t.code.lower(): t for t in table}
    t = by_code.get(code.lower())
    if t is None:
        raise LangError(f'unknown language code {code!r}; known: {", ".join(sorted(by_code))}')
    voice = next((v for v in table if v.id == t.voice_id), None)
    if voice is None or voice.code not in CODE_TO_FOLDER:
        raise LangError(f'no voice folder known for {code!r}')
    return CODE_TO_FOLDER[voice.code]


def dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(root, name))
    return total


def installed_languages(exec_dir: str) -> dict[str, list[tuple[str, int]]]:
    """Language folder name -> [(absolute path, size)] across both language locations."""
    out: dict[str, list[tuple[str, int]]] = {}
    for rel in LANG_DIRS:
        base = os.path.join(exec_dir, rel)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if os.path.isdir(path):
                out.setdefault(name, []).append((path, dir_size(path)))
    return out
