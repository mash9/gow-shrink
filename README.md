# gow-shrink

Reduces the install size of PC games by lowering texture resolution, removing unused voice languages and recompressing game files.

Requires Python 3.11+. Extra packages needed by some games are listed below.

## Supported games

| Game | Executable | Install size | After all steps | Extra packages |
|---|---|---|---|---|
| Atomic Heart | `AtomicHeart.exe` | about 158 GB | about 55 GB | - |
| God of War (2018) | `GoW.exe` | about 65 GB | about 25 GB | numpy |
| God of War Ragnarök | `GoWR.exe` | about 176 GB | about 70 GB | lz4 |
| Resident Evil 7 biohazard | `re7.exe` | about 65 GB | about 16 GB | - |
| Resident Evil Village | `re8.exe` | about 38 GB | about 15 GB | - |
| SILENT HILL f | `SHf.exe` | about 50 GB | about 27 GB | pyooz, numpy |

## Unsupported games

| Game | Executable | Reason |
|---|---|---|
| Resident Evil Requiem | `re9.exe` | The game verifies a signature on its `.pak` files, so it rejects any pak the tool rewrites. (RE7 and RE8 have no such check and are supported.) |

## Desktop app

![gow-shrink desktop app](images/app.png)

The desktop app runs the recommended steps of each game from a window. Download `gow-shrink.exe` from the [Releases](https://github.com/mash9/gow-shrink/releases) page and double-click it. It needs no Python and already includes every extra package.

Pick the game in the list on the left, choose its folder, and press **Run** on each step. The games and their steps come from the `app` section of each game in `tool/games.json`. Turn on **Keep the original files** to move the originals to a backup folder (one subfolder per game and step, for example `<BackupDir>\gow-mipdrop`). Other commands and options are only available from the command line.

## Command line

The recommended steps of each game can also be run from Command Prompt, with Python 3.11+ and the extra packages listed above. The commands for each game, and how to pass the game folder and keep a backup, are in [guide/commands.md](guide/commands.md).

## Results

### Atomic Heart

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.pak`) | `mipdrop --drop 1` | 20.6 GiB (paks 151.7 → 131.1 GiB) | Verified in game |
| Lightmaps (`.pak`) | `lightmap --drop 1` | 30.7 GiB of texture data (12.3 GiB in compressed paks) | Verified in game |
| Voice languages and lip-sync (`.pak`) | `lang --keep english` | about 15.1 GiB | Verified in game |
| Compression (`.pak`) | `repack` | about 66.8 GiB on unmodified paks | Verified in game |

All four steps were applied to one install and the game was played without problems; the 19 paks went from 151.7 GiB to 45.4 GiB (`mipdrop` 151.7 → 131.1 GiB, then `lang` and `repack` → 57.7 GiB, then `lightmap` → 45.4 GiB) and the whole game folder to about 55 GB. `lightmap` ran last here, on the compressed paks. The `lang` and `repack` values are estimates from `--dry-run`; after `mipdrop` and `lang`, `repack` saves less on its own. All resources are in 19 uncompressed Unreal Engine 4 `.pak` files (151.7 GiB). Textures used by the UI are left as they are. Run the steps in the order of the table (see [guide/repack.md](guide/repack.md#order-with-the-other-steps)).

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

If the screen stays black after these steps, quit the game completely, end any `GoWR.exe` process that is still running (for example in Task Manager), and start the game again; it then shows normally. This appears to be a problem of the game itself rather than of the changed files.

### Resident Evil 7 biohazard

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.pak`) | `mipdrop --drop 2` | about 41.7 GiB | Verified in game |
| Voice languages (`.pak`) | `lang --keep english` | about 1.8 GiB | Verified in game |
| Duplicate DLC data (`.pak`) | `prune --dlc` | about 6.0 GiB after `mipdrop` (26.3 GiB on its own) | Verified in game |

All three steps were applied to one install and the game ran normally. The `.pak` files went from 63.79 GiB to 14.81 GiB, and the whole game folder from about 65 GB to 16 GB. The per-step values are the estimates from `scan` and `--dry-run`. The DLC packs store byte-identical copies of many resources of `re_chunk_000.pak`; `prune --dlc` removes those copies from the DLC packs and keeps the ones in `re_chunk_000.pak` (see [guide/prune.md](guide/prune.md#dlc-copies---dlc)).

### Resident Evil Village

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.pak`) | `mipdrop --drop 2` | 17.9 GiB | Verified in game |
| Voice languages (`.pak`) | `lang --keep english` | 3.3 GiB | Verified in game |
| Replaced patch data (`.pak`) | `prune` | 1.6 GiB | Verified in game |

The texture step took the whole game folder from about 38 GB to 20 GB. The voice step empties the voice packs and banks of the other eight languages inside the `.pak` files; run it after `mipdrop` and keep its backup in a separate folder (see [guide/lang.md](guide/lang.md#resident-evil-village)). `prune` removes the old copies of resources that a later patch pak replaces; run it last.

### SILENT HILL f

| Step | Command | Saved | Status |
|---|---|---|---|
| Textures (`.ucas`) | `mipdrop --drop 2` | 21.9 GiB | Verified in game |

The voice-over is stored inside `SHf-Windows.pak` and is small (about 66 MB English, 90 MB Japanese), so there is no `lang` step for this game.

## Tools

| Tool | What it does | Games |
|---|---|---|
| [`mipdrop`](guide/mipdrop.md) | Removes the top mip levels of streamed textures | All |
| [`lang`](guide/lang.md) | Removes voice languages you do not keep | All but SILENT HILL f |
| [`lightmap`](guide/lightmap.md) | Removes the top mip levels of baked lightmaps and shadowmaps | Atomic Heart |
| [`shvol`](guide/shvol.md) | Halves the resolution of the SH lighting volumes | God of War (2018) |
| [`prune`](guide/prune.md) | Removes pak entries replaced by later patches or copied in DLC paks | Resident Evil 7, Resident Evil Village |
| [`repack`](guide/repack.md) | Compresses uncompressed `.pak` files | Atomic Heart |

## License

Copyright (C) 2026 Park Geun-young (박근영).

gow-shrink is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 3 as published by the Free
Software Foundation. See [LICENSE](LICENSE). It is distributed in the hope that
it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty
of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
