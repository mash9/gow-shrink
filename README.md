# Game Optimization Wrench (gow-shrink)

Game Optimization Wrench reduces the install size of PC games. It lowers texture resolution, removes voice languages you do not use and recompresses game files, and each game keeps running as before.

![Game Optimization Wrench desktop app](images/app.png)

## Supported games

| Game | Executable | Install size | After all steps |
|---|---|---|---|
| Atomic Heart | `AtomicHeart.exe` | about 158 GB | about 55 GB |
| God of War (2018) | `GoW.exe` | about 65 GB | about 25 GB |
| God of War Ragnarök | `GoWR.exe` | about 176 GB | about 70 GB |
| Resident Evil 7 biohazard | `re7.exe` | about 65 GB | about 16 GB |
| Resident Evil Village | `re8.exe` | about 38 GB | about 15 GB |
| SILENT HILL f | `SHf.exe` | about 50 GB | about 27 GB |
| Stellar Blade | `SB.exe` | about 65 GB | about 36 GB |

### Unsupported games

| Game | Executable | Reason |
|---|---|---|
| Resident Evil Requiem | `re9.exe` | The game verifies a signature on its `.pak` files, so it rejects any pak the tool rewrites. (RE7 and RE8 have no such check and are supported.) |

## Using the desktop app

1. Download `gow-shrink.exe` from the [Releases](https://github.com/mash9/gow-shrink/releases) page and double-click it. It needs no Python and already includes every extra package.
2. Pick the game in the list on the left.
3. Press **Change folder** and select the folder that contains the game's executable (see the table above). The dot next to the game turns green when the folder is found.
4. Decide whether to keep the original files (see below).
5. Press **Run** on the first step and confirm. The progress bar shows how far the step is; the output at the bottom shows the details.
6. Start the game and check it before you run the next step. Run the steps from top to bottom.

Each step shows about how much it saves. Some steps have options under their description: on the Stellar Blade voice step, tick the languages you want to keep, and the saved amount updates as you tick. Every step asks for confirmation before it starts; if it already ran since the app was opened, the question also says whether running it again would remove more.

### Keeping the original files

Turn on **Keep the original files** and choose a folder to move the originals there instead of replacing them. Each step gets its own subfolder, for example `<BackupDir>\sb-mipdrop`. Choose a folder on the same drive as the game: the originals are then moved instead of copied, which is much faster and needs no extra space for them.

To undo a step, close the game and press **Restore** under its **Run** button; the button shows up when that step's backup folder holds files to put back. Steps are undone from the last one, because a later step starts from the files the earlier step made: the app asks you to restore the later steps first, and **Restore all** undoes every step of the game in the right order. Running a step again while a later step still has a backup asks for confirmation, because restoring that later step afterwards would bring back old files. Once you are happy with the result, you can delete the backup folder to free the space.

With the switch off, the original files are replaced and cannot be restored except by reinstalling or verifying the game files in the store.

### Before you start

- Close the game before running a step.
- A step writes the new file next to the original and replaces the original only after the new file has been checked. Make sure the game's drive has enough free space for the largest file of the step (the output says how much it needs).
- A game update, or verifying the game files in the store, can put original files back. Run the steps again afterwards.

## What the steps do

The steps of each game, in the order the app runs them. Saved sizes are in GiB (1 GiB = 1024³ bytes).

### Atomic Heart

| Step | Saved | Status |
|---|---|---|
| Textures | 20.6 GiB (paks 151.7 → 131.1 GiB) | Verified in game |
| Lightmaps | 30.7 GiB of texture data (12.3 GiB in compressed paks) | Verified in game |
| Voice languages | about 15.1 GiB | Verified in game |
| Compress .pak files | about 66.8 GiB on unmodified paks | Verified in game |

All four steps were applied to one install and the game was played without problems; the 19 paks went from 151.7 GiB to 45.4 GiB (Textures 151.7 → 131.1 GiB, then Voice languages and Compress .pak files → 57.7 GiB, then Lightmaps → 45.4 GiB) and the whole game folder to about 55 GB. Lightmaps ran last here, on the compressed paks. The Voice languages and Compress .pak files values are estimates; after the texture and voice steps, compression saves less on its own. Textures used by the UI are left as they are. The voice step keeps English. Run the steps in the order shown (see [guide/repack.md](guide/repack.md#order-with-the-other-steps)).

### God of War (2018)

| Step | Saved | Status |
|---|---|---|
| Textures | 34.6 GiB | Verified in game |
| Lighting volumes | 2.1 GiB | Verified in game |
| Voice languages | 3.2 GiB | Verified in game |

All three steps were applied to one install and the game was played through without problems. The texture step there was at a stronger level (2.92 GiB of textures), and `exec\` went from about 65 GB to 24 GB. The voice step keeps English.

### God of War Ragnarök

| Step | Saved | Status |
|---|---|---|
| Textures | 89.0 GiB | Verified in game |
| Voice languages | 16.7 GiB | Verified in game |

If the screen stays black after these steps, quit the game completely, end any `GoWR.exe` process that is still running (for example in Task Manager), and start the game again; it then shows normally. This appears to be a problem of the game itself rather than of the changed files. The voice step keeps English.

### Resident Evil 7 biohazard

| Step | Saved | Status |
|---|---|---|
| Textures | about 41.7 GiB | Verified in game |
| Voice languages | about 1.8 GiB | Verified in game |
| Duplicate DLC data | about 6.0 GiB after Textures (26.3 GiB on its own) | Verified in game |

All three steps were applied to one install and the game ran normally. The `.pak` files went from 63.79 GiB to 14.81 GiB, and the whole game folder from about 65 GB to 16 GB. The DLC packs store byte-identical copies of many resources of `re_chunk_000.pak`; the last step removes those copies from the DLC packs and keeps the ones in `re_chunk_000.pak` (see [guide/prune.md](guide/prune.md#dlc-copies---dlc)). The voice step keeps English.

### Resident Evil Village

| Step | Saved | Status |
|---|---|---|
| Textures | 17.9 GiB | Verified in game |
| Voice languages | 3.3 GiB | Verified in game |
| Replaced patch data | 1.6 GiB | Verified in game |

The texture step took the whole game folder from about 38 GB to 20 GB. The voice step keeps English and empties the voice packs and banks of the other eight languages inside the `.pak` files (see [guide/lang.md](guide/lang.md#resident-evil-village)). The last step removes the old copies of resources that a later patch pak replaces.

### SILENT HILL f

| Step | Saved | Status |
|---|---|---|
| Textures | 21.9 GiB | Verified in game |

The voice-over is small (about 66 MB English, 90 MB Japanese), so there is no voice step for this game.

### Stellar Blade

| Step | Saved | Status |
|---|---|---|
| Textures | 23.3 GiB | Verified in game |
| Voice languages | about 0.3 GiB per language removed (2.4 GiB keeping English) | Verified in game |
| Compress .pak files | 0.5 GiB | Verified in game |

All three steps were applied to one install (voice step keeping only Korean) and the game runs normally; the whole game folder went from about 65 GB to 36 GB.

The texture step removes one mip level and lowers the game's texture quality settings by the same amount, so Low, Medium and High texture settings look as before and Very High loses one level (see [guide/mipdrop.md](guide/mipdrop.md#stellar-blade)). Without that settings change, removing levels made every setting look blurrier. The Korean voice-over is the game's original audio and is always kept. On the voice step, tick the other languages you want to keep; English is ticked by default. The last step compresses the shader archive, the only large file outside the `.ucas` containers.

## Command line

Every step can also be run from Command Prompt with Python 3.11+, and the command line has more commands and options than the app (for example a different texture level, or keeping several voice languages in other games). The commands for each game, the extra packages they need, and how to keep a backup and restore it, are in [guide/commands.md](guide/commands.md).

| Command | What it does | Games |
|---|---|---|
| [`mipdrop`](guide/mipdrop.md) | Removes the top mip levels of streamed textures | All |
| [`lang`](guide/lang.md) | Removes voice languages you do not keep | All but SILENT HILL f |
| [`lightmap`](guide/lightmap.md) | Removes the top mip levels of baked lightmaps and shadowmaps | Atomic Heart |
| [`shvol`](guide/shvol.md) | Halves the resolution of the SH lighting volumes | God of War (2018) |
| [`prune`](guide/prune.md) | Removes pak entries replaced by later patches or copied in DLC paks | Resident Evil 7, Resident Evil Village |
| [`repack`](guide/repack.md) | Compresses uncompressed `.pak` entries | Atomic Heart, Stellar Blade |
| `restore` | Puts the originals back from a backup folder | All |

Extra Python packages for the command line: numpy (God of War 2018, SILENT HILL f), lz4 (God of War Ragnarök), pyooz (SILENT HILL f, Stellar Blade). The desktop app includes them all.

## License

Copyright (C) 2026 Park Geun-young (박근영).

Game Optimization Wrench (gow-shrink) is free software: you can redistribute it
and/or modify it under the terms of the GNU General Public License version 3 as
published by the Free Software Foundation. See [LICENSE](LICENSE). It is
distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
