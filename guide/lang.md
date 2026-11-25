# lang: remove unused voice languages

`lang` lists the installed voice languages and removes all but the ones you keep. It works on Atomic Heart, God of War (2018), God of War Ragnarök, Resident Evil 7 biohazard and Resident Evil Village.

Back to the [README](../README.md).

## Where the voices are

Per-language audio lives in these places (relative to `exec\` for the God of War games):

| Game | Voice audio | Voice languages | Saved by keeping one |
|---|---|---|---|
| Atomic Heart | Wwise files and lip-sync animations inside the `.pak` files (see below) | 10, 1.49-1.77 GiB each | about 15.1 GiB (English) |
| God of War (2018) | `sound\pc_le\<language>\`, `wad\pc_le\soundbanks\<language>\` | 12, 0.26-0.33 GiB each | about 3.2 GiB |
| God of War Ragnarök | `sound\pc_le\<language>.0.audiopack` + `.audiopack.toc`, `wad\pc_le\soundbanks\<language>\` | 13 (adds `arabic`), 1.28-1.47 GiB each | about 16.7 GiB |
| Resident Evil 7 biohazard | Wwise packs and banks inside the `.pak` files (see below) | 6, 0.35-0.39 GiB each | about 1.8 GiB |
| Resident Evil Village | Wwise packs and banks inside the `.pak` files (see below) | 9, 0.40-0.42 GiB each | about 3.3 GiB |

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

## Atomic Heart

Keeping only the English voice (`--keep english`) has been verified in game, together with `mipdrop --drop 1` and `repack`.

The voice-over is stored inside the `.pak` files (`pakchunk0`, `pakchunk12`, `pakchunk13`) in `AtomicHeart/Content/WwiseAudio/Windows/<language>/`: `.wem` media, `.bnk` banks and their `.json`/`.xml` info files. The languages are `Chinese(CN)`, `English(US)`, `French(FR)`, `German(DE)`, `Italian(IT)`, `Japanese(JP)`, `Polish(PL)`, `Portuguese(BR)`, `Ru` (Russian) and `Spanish(ES)`. `Radio/` holds radio music, not a language, and is never touched.

```bat
python -m tool lang "<GameDir>" --game ah
python -m tool lang "<GameDir>" --keep english --backup "<BackupDir>\lang" --game ah
```

- Four languages also have face animations for lip-sync in `AtomicHeart/Content/L10N/<culture>/` (`pakchunk1`): `en-US` (0.94 GiB), `ru-RU` (0.20 GiB), `pt-BR` (0.13 GiB) and `de-DE` (0.13 GiB). When a voice language is removed, its lip-sync folder is removed too, and the game then uses the default animation stored outside `L10N`. `L10N/ja-JP` holds regional textures and materials, not lip-sync, and is never touched.
- The `de-DE` animations are byte-identical copies of the `en-US` ones.
- Removed files are taken out of the pak index, and folders that become empty are removed from it too; the rest of each pak is copied as is, with the compression it already has. With `--backup`, the original paks are moved to the backup folder and `restore` puts them back.
- `lang` always starts from the pak in the game folder, so it keeps what `mipdrop` and `repack` have changed, and running it again removes nothing more. To bring a removed language back, use `restore`.
- `--keep russian` selects `Ru`. `--keep-for` is not available.
- It has not been checked whether the game picks the `L10N` folder by text language or by voice language. Keep the game's voice language set to one you keep.

## Resident Evil 7 biohazard

Keeping only the English voice (`--keep english`) has been verified in game, together with `mipdrop` and `prune --dlc`.

It works the same way as in Resident Evil Village (below). There are 6 voice languages (`english(us)`, `japanese`, `french(france)`, `german`, `italian`, `spanish(spain)`), each with 220 packs and 173 banks in `re_chunk_000.pak` and the DLC packs.

```bat
python -m tool lang "<GameDir>" --game re7
python -m tool lang "<GameDir>" --keep english --backup "<BackupDir>\lang" --game re7
```

The same backup and order rules apply: a separate backup folder per step, `mipdrop` first, `prune` last.

## Resident Evil Village

Keeping only the English voice (`--keep english`) has been verified in game.

The voice-over is stored inside the `.pak` files (`re_chunk_000.pak` and the DLC packs) as Wwise stream packs (`AKPK`, 120 per language) and sound banks (`BKHD`, 87 per language). There are no per-language folders, so `lang` rewrites the paks instead of moving files:

```bat
python -m tool lang "<GameDir>" --game re8
python -m tool lang "<GameDir>" --keep english --backup "<BackupDir>\lang" --game re8
```

- The language of each pack comes from its language map and the language ids of its files, and the language of each bank from the language id in its header (the FNV-1 hash of the language name). Sound effects and music (`sfx`) are never touched, and banks with an unknown language id are left as they are.
- Removed packs and banks are not deleted from the pak. Each is replaced by an empty one of a few dozen bytes (a pack with its language map and no files, a bank with only its header), so the game still finds a valid file if it looks for one.
- `--keep-for` is not available, because the game has no text -> voice language table in its files.
- Only the paks that contain voice data are rewritten, and the rest of each pak is copied byte for byte. With `--backup`, the original paks are moved to the backup folder and `restore` puts them back.
- `mipdrop` and `lang` change the same paks. Use a separate backup folder for each step (for example `<BackupDir>\mipdrop` and `<BackupDir>\lang`), run `mipdrop` first, and restore in reverse order (`lang` first). `prune` ([guide/prune.md](prune.md)) rewrites the same paks too; run it after both and restore it first. Running `mipdrop` again with its backup rebuilds the paks from the originals and brings the voices back; run `lang` again afterwards.

## Notes

- `--keep` accepts the folder name or any unambiguous start of it (`english` = `english(us)`; `spanish` is ambiguous). Prefer the short form: if `python` is a batch-file launcher (for example a pyenv-win shim), `cmd` breaks arguments that contain parentheses.
- `--dry-run` prints what would be removed without changing anything.
- After removing a language, do not switch the game to a text or voice language that needs it.
