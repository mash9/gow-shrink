# gow-shrink

A tool that reduces the install size of God of War (PC). It removes the top mip levels of streamed textures, halves the SH lighting volumes stored in `.wad` files, and deletes the voice-over languages you do not use.

Requires Python 3.11+ and NumPy (used by `shvol` only).

## Results

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.texpack`) | `shrink --drop 2` | 34.6 GiB | Verified in game |
| Voice languages | `lang --keep english` | 3.2 GiB | Files removed; game test pending |
| Lighting volumes (`.wad`) | `shvol` | 2.1 GiB | Experimental; files verified, game test pending |

## Quick start

Run the commands from the repository folder in Command Prompt. Replace `<GameDir>` with your God of War install folder (for example `C:\Games\God of War`) and `<BackupDir>` with a folder for backups. Keep the quotes, because the path may contain spaces. Apply the steps one at a time on an unmodified install, and start the game after each step.

1. Textures, about 34.6 GiB saved:

   ```bat
   python -m tool shrink "<GameDir>\exec\wad\pc_le" --drop 2 --in-place
   python -m tool verify "<GameDir>\exec\wad\pc_le"
   ```

2. Voice languages, about 3.2 GiB saved (keeps only the English voice):

   ```bat
   python -m tool lang "<GameDir>\exec" --keep english
   ```

3. SH lighting volumes, about 2.1 GiB saved (experimental):

   ```bat
   python -m tool shvol "<GameDir>\exec\wad\pc_le"
   ```

Without `--backup`, these commands replace or delete game files directly. Run each one only once: `shrink` and `shvol` shrink already reduced files again on a second run. To get the original files back, reinstall or verify the game files with your launcher, or use `--backup` (see [Backup and restore](#backup-and-restore)).

## Textures (`shrink`)

`shrink` removes the top mip levels of every texture in the 19 `.texpack` files (38.39 GiB in total) and rewrites each pack together with its `.toc`.

| Setting | Result | Saved | Typical top resolution | Notes |
|---|---|---|---|---|
| `--max-res 1024` | 17.76 GiB | 20.63 GiB | 1024 (1024 and below untouched) | Conservative |
| `--drop 1` | 10.09 GiB | 28.30 GiB | 2048 → 1024 | Verified in game. Sharpest option |
| **`--drop 2`** | **3.80 GiB** | **34.59 GiB** | 2048 → 512 | **Recommended.** Blurrier up close |
| `--drop 3` | 2.92 GiB | 35.47 GiB | 2048 → 256 | Runs in game; little gain over `--drop 2` |

Estimate savings (does not touch any file):

```bat
python -m tool scan "<GameDir>\exec\wad\pc_le" --drop 2
```

Try a single file, writing the result to another folder (originals untouched):

```bat
python -m tool shrink "<GameDir>\exec\wad\pc_le\090_caldera_010.texpack" --drop 2 --out "<OutputDir>"
```

Integrity check:

```bat
python -m tool verify "<GameDir>\exec\wad\pc_le"
```

| Option | Meaning |
|---|---|
| `--drop N` | Remove the top N mip records of every texture (default 1 when no option is given) |
| `--max-res R` | Remove records until `max(width, height)` is at most R |
| `--min-res R` | Never let the remaining top resolution fall below R |
| `--dry-run` | Print the plan without writing anything |

- When both `--drop` and `--max-res` are given, whichever removes more wins.
- At least one record always remains in each chain. The tail record that bundles the small mips is never split.
- The GNF header lives only on the top mip block, so the tool moves it to the new top block and updates its resolution, `last_level`, pitch and size fields.
- Each output is checked with `verify` right after it is written. `--in-place` writes `*.gowshrink.tmp` next to the original and swaps it in only after the check passes.

## Voice languages (`lang`)

Per-language audio lives in `sound\pc_le\<language>\` and `wad\pc_le\soundbanks\<language>\`. There are 12 voice languages of 0.26-0.33 GiB each, so keeping one saves about 3.2 GiB.

Text languages without their own voice use another voice language, as mapped in `exec\languages\LANGS_*.txt`. For example, Korean, Dutch, Turkish, Czech, Hungarian, Croatian and Traditional Chinese text all use `english(us)`.

List installed voice languages and the text -> voice mapping:

```bat
python -m tool lang "<GameDir>\exec"
```

Keep only the English voice:

```bat
python -m tool lang "<GameDir>\exec" --keep english
```

Same result, chosen by text language code (Korean text uses the English voice):

```bat
python -m tool lang "<GameDir>\exec" --keep-for ko
```

Keep several and move the others to a backup folder:

```bat
python -m tool lang "<GameDir>\exec" --keep english --keep japanese --backup "<BackupDir>\lang"
```

- `--keep` accepts the folder name or any unambiguous start of it (`english` = `english(us)`; `spanish` is ambiguous). Prefer the short form: if `python` is a batch-file launcher (for example a pyenv-win shim), `cmd` breaks arguments that contain parentheses.
- `--dry-run` prints what would be removed without changing anything.
- After removing a language, do not switch the game to a text or voice language that needs it.

## Lighting volumes (`shvol`, experimental)

`shvol` halves the resolution of the spherical-harmonics (SH) lighting volumes in `.wad` files. These are 3D textures named `<8 hex digits>SH0` to `SH3` (868 volumes in 185 wads, 2.41 GiB). Each volume is downsampled 2x on every axis with a 2x2x2 box filter, so its data shrinks to about 1/8. All `.wad` files go from 12.61 GiB to 10.52 GiB.

The files pass verification, but the change has not been tested in game yet. Indirect lighting may look softer or shifted, so check several areas.

Show what would change (does not touch any file):

```bat
python -m tool shvol "<GameDir>\exec\wad\pc_le" --dry-run
```

Apply, keeping the originals so the change can be undone:

```bat
python -m tool shvol "<GameDir>\exec\wad\pc_le" --backup "<BackupDir>\shvol"
```

- A resized volume shifts every later entry in the wad. The tool rewrites the whole wad, updates the entry sizes, the GNF header (width, height, depth, pitch, data size) and the descriptor size field, and recomputes every `autopad` entry so the next entry still starts on a 64 KiB boundary.
- Before a rewritten wad replaces the original, the tool checks that entry order and names are unchanged, all non-SH entries are byte-identical, `autopad` alignment holds, and the SH fields match the new data. Rebuilding at the original resolution reproduces all 185 wads byte for byte.

## Backup and restore

`shrink --in-place`, `shvol` and `lang` all accept `--backup <BackupDir>`. The originals are moved there and recorded in `manifest.json`, and `restore` puts them back.

Apply with a backup:

```bat
python -m tool shrink "<GameDir>\exec\wad\pc_le" --drop 2 --in-place --backup "<BackupDir>\texpack"
```

Restore everything:

```bat
python -m tool restore --backup "<BackupDir>\texpack"
```

Restore one file:

```bat
python -m tool restore --backup "<BackupDir>\texpack" 090_caldera_010.texpack
```

- Use a separate backup folder for each command, so each change can be restored on its own.
- Keep `<BackupDir>` on the same drive as the game so originals are moved, not copied.
- While a backup exists, `shrink` and `shvol` always rebuild from the backed-up original. Running `--drop 1` and later `--drop 2` therefore does not stack, and running `shvol` twice does not halve the volumes twice.
- The disk space is freed only after you delete `<BackupDir>`. Do that once the game runs correctly.

## Tests

```bat
set "GOW_TEXPACK=<GameDir>\exec\wad\pc_le\090_caldera_010.texpack"
python -m unittest tests.test_texpack -v
```

The tests read the game file named by `GOW_TEXPACK` and write their results to a temporary folder.

## Format notes

File format research is in [docs/formats.md](docs/formats.md), and open tasks are in [docs/TODO.md](docs/TODO.md).
