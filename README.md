# gow-shrink

A tool that reduces the install size of PC games by rewriting or removing their resource files. Each supported game gets its own set of steps, such as lowering texture resolution, shrinking lighting data and removing voice-over languages you do not use, and each step is tested in game before it is listed. The games and their steps are defined in `tool/games.json`, and more games will be added.

Requires Python 3.11+. Some steps need extra packages (for example NumPy, `lz4` or `pyooz`); they are listed with each game below, and the desktop app offers to install them.

## Supported games

| Game | Executable | Install size | After the quick start | Extra packages |
|---|---|---|---|---|
| God of War (2018) | `GoW.exe` | about 65 GB | about 25 GB | NumPy (`shvol` only) |
| God of War Ragnarök | `GoWR.exe` | about 176 GB | about 70 GB | `lz4` |
| SILENT HILL f | `SHf.exe` | about 50 GB | about 27 GB | `pyooz`, NumPy |
| Resident Evil Village | `re8.exe` | about 38 GB | about 15 GB | none |

- Install sizes are for `exec\` (God of War games) or the whole game folder (SILENT HILL f, Resident Evil Village).
- "After the quick start" is the install size minus the savings of every quick start step (see [Results](#results)).
- Games that are not in this table are not supported yet. Each game needs its own analysis of its file formats.
- The texture commands detect the game from the `.texpack` version, or from the executable in a parent folder when there are no `.texpack` files, so the same commands work for every game.
- Other builds of the supported games may work if `verify` reports `[OK]` on the unmodified files. `mipdrop` skips any file whose structure fails verification.
- Every command except `restore` accepts `--game gow` (God of War 2018), `--game gowr` (Ragnarök), `--game shf` (SILENT HILL f) or `--game re8` (Resident Evil Village). Without it, the game is detected from the `.texpack` version or from `GoW.exe` / `GoWR.exe` / `SHf.exe` / `re8.exe` in a parent folder. With it, the command refuses files that belong to the other game. A command that the game does not support (for example `shvol` on Ragnarök) is refused either way.

## Desktop app

![gow-shrink desktop app](images/app.png)

`gow_shrink.pyw` in the repository folder runs the quick start steps below from a window. Double-click it, or run `python gow_shrink.pyw`. Pick the game in the list on the left, choose its folder, and press **Run** on each step. The games and their steps come from the `app` section of each game in `tool/games.json`. Turn on **Keep the original files** to move the originals to a backup folder (one subfolder per game and step, for example `<BackupDir>\gow-mipdrop`); undo a step with `python -m tool restore --backup "<BackupDir>\gow-mipdrop"`. Other commands and options are only available from the command line.

## Quick start

Run the commands from the repository folder in Command Prompt. Replace `<GameDir>` with the folder that contains the game's `.exe`, and keep the quotes. Given the game folder, each command finds the subfolder it works on (for example `exec\wad\pc_le`, `exec` or `SHf\Content\Paks`) by itself; you can still pass that subfolder, or single files, directly. The per-game executable names and subfolders are listed in `tool/games.json`. These commands replace game files directly, so run each one once and start the game after each step. To keep the originals so a step can be undone with `restore`, add `--backup "<BackupDir>"` (see each tool's section below).

### God of War (2018)

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game gow
python -m tool shvol "<GameDir>" --game gow
python -m tool lang "<GameDir>" --keep english --game gow
```

### God of War Ragnarök

```bat
python -m pip install lz4
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game gowr
python -m tool lang "<GameDir>" --keep english --game gowr
```

### SILENT HILL f

```bat
python -m pip install pyooz numpy
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game shf
```

### Resident Evil Village

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game re8
python -m tool lang "<GameDir>" --keep english --game re8
python -m tool prune "<GameDir>" --game re8
```

## Results

### God of War (2018)

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.texpack`) | `mipdrop --drop 2` | 34.6 GiB | Verified in game |
| Lighting volumes (`.wad`) | `shvol` | 2.1 GiB | Verified in game |
| Voice languages | `lang --keep english` | 3.2 GiB | Verified in game |

All three steps were applied to one install and the game was played through without problems. The texture step there was at the `--drop 3` level (2.92 GiB of textures), and `exec\` went from about 65 GB to 24 GB.

### God of War Ragnarök

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.texpack`) | `mipdrop --drop 2` | 89.0 GiB | Verified in game |
| Voice languages | `lang --keep english` | 16.7 GiB | Verified in game |

### SILENT HILL f

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.ucas`) | `mipdrop --drop 2` | 21.9 GiB | Verified in game |

The voice-over is stored inside `SHf-Windows.pak` and is small (about 66 MB English, 90 MB Japanese), so there is no `lang` step for this game.

### Resident Evil Village

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.pak`) | `mipdrop --drop 2` | 17.9 GiB | Verified in game |
| Voice languages (`.pak`) | `lang --keep english` | 3.3 GiB | Verified in game |
| Replaced patch data (`.pak`) | `prune` | 1.6 GiB | Verified in game |

The texture step took the whole game folder from about 38 GB to 20 GB. The voice step empties the voice packs and banks of the other eight languages inside the `.pak` files; run it after `mipdrop` and keep its backup in a separate folder (see [guide/lang.md](guide/lang.md#resident-evil-village)). `prune` removes the old copies of resources that a later patch pak replaces; run it last.

## Textures (`mipdrop`)

`mipdrop` removes the top mip levels of every streamed texture in the `.texpack` files (God of War games), the `.utoc`/`.ucas` container (SILENT HILL f) or the `.pak` files (Resident Evil Village). Settings, savings per game, options and the game specifics are in [guide/mipdrop.md](guide/mipdrop.md).

## Lighting volumes (`shvol`)

`shvol` halves the resolution of the spherical-harmonics (SH) lighting volumes in the `.wad` files of God of War (2018). What it changes, commands, options and checks are in [guide/shvol.md](guide/shvol.md).

## Replaced patch data (`prune`)

`prune` removes the entries of `re_chunk_000.pak` and its patch paks that a later patch pak replaces (Resident Evil Village only). What it removes, commands and the order with the other steps are in [guide/prune.md](guide/prune.md).

## Voice languages (`lang`)

`lang` lists the installed voice languages and removes all but the ones you keep. In Resident Evil Village the voices are inside the `.pak` files, so `lang` rewrites the paks instead of moving folders. Where each game stores them, the text -> voice mapping, commands and options are in [guide/lang.md](guide/lang.md).
