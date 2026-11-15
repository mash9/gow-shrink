# lang: remove unused voice languages

`lang` lists the installed voice languages and removes all but the ones you keep. It works on both God of War (2018) and God of War Ragnarök.

Back to the [README](../README.md).

## Where the voices are

Per-language audio lives in these places (relative to `exec\`):

| Game | Voice audio | Voice languages | Saved by keeping one |
|---|---|---|---|
| God of War (2018) | `sound\pc_le\<language>\`, `wad\pc_le\soundbanks\<language>\` | 12, 0.26-0.33 GiB each | about 3.2 GiB |
| God of War Ragnarök | `sound\pc_le\<language>.0.audiopack` + `.audiopack.toc`, `wad\pc_le\soundbanks\<language>\` | 13 (adds `arabic`), 1.28-1.47 GiB each | about 16.7 GiB |

The locations and the voice id -> language name mapping for each game are in `tool/games.json`. Other `.audiopack` files (`root`, `valhalla`, and the per-area ones such as `010_midgard1a`) are never touched.

## Text and voice languages

Text languages without their own voice use another voice language, as mapped in `exec\languages\LANGS_*.txt`. For example, Korean, Dutch, Turkish, Czech, Hungarian, Croatian and Chinese text all use `english(us)`. The text language codes differ between the games (for example Latin American Spanish is `es-LA` in God of War and `mx` in Ragnarök); run `lang` without `--keep` to see them.

## Commands

List installed voice languages and the text -> voice mapping:

```bat
python -m tool lang "<GameDir>"
```

Keep only the English voice:

```bat
python -m tool lang "<GameDir>" --keep english
```

Same result, chosen by text language code (Korean text uses the English voice):

```bat
python -m tool lang "<GameDir>" --keep-for ko
```

Keep several and move the others to a backup folder:

```bat
python -m tool lang "<GameDir>" --keep english --keep japanese --backup "<BackupDir>\lang"
```

## Notes

- `--keep` accepts the folder name or any unambiguous start of it (`english` = `english(us)`; `spanish` is ambiguous). Prefer the short form: if `python` is a batch-file launcher (for example a pyenv-win shim), `cmd` breaks arguments that contain parentheses.
- `--dry-run` prints what would be removed without changing anything.
- After removing a language, do not switch the game to a text or voice language that needs it.
