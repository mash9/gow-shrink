# mipdrop: remove top texture mip levels

`mipdrop` removes the top mip levels of every streamed texture and rewrites the `.texpack` files together with their `.toc` (God of War games), the `.pak` files (Resident Evil games and Atomic Heart) or the `.utoc`/`.ucas` containers (SILENT HILL f, Stellar Blade). It was called `shrink` before, and `shrink` still works as an alias.

Back to the [README](../README.md).

## Commands

Estimate savings (does not touch any file):

```bat
python -m tool scan "<GameDir>" --drop 2
```

Apply to the game folder:

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place
```

Try a single file, writing the result to another folder (originals untouched):

```bat
python -m tool mipdrop "<GameDir>\exec\wad\pc_le\090_caldera_010.texpack" --drop 2 --out "<OutputDir>"
```

Integrity check:

```bat
python -m tool verify "<GameDir>"
```

| Option | Meaning |
|---|---|
| `--drop N` | Remove the top N mip records of every texture (default 1 when no option is given) |
| `--max-res R` | Remove records until `max(width, height)` is at most R |
| `--min-res R` | Never let the remaining top resolution fall below R |
| `--in-place` | Replace the files in the game folder |
| `--out DIR` | Write the results to another folder instead (originals untouched) |
| `--backup DIR` | With `--in-place`, move the originals here so `restore` can put them back |
| `--game ah` / `--game gow` / `--game gowr` / `--game re7` / `--game re8` / `--game shf` / `--game sb` | Refuse files that belong to another game (default: detect from the files) |
| `--dry-run` | Print the plan without writing anything |

## Atomic Heart

All resources are in 19 Unreal Engine 4 `.pak` files in `AtomicHeart\Content\Paks\` (151.7 GiB). No extra packages are needed. `scan` and `verify` are not available for this game; use `--dry-run`.

```bat
python -m tool mipdrop "<GameDir>" --drop 1 --in-place --dry-run --game ah
python -m tool mipdrop "<GameDir>" --drop 1 --in-place --backup "<BackupDir>\mipdrop" --game ah
```

| Setting | Textures | Texture data removed (before pak compression) | Notes |
|---|---|---|---|
| **`--drop 1`** | **12,615** | **20.6 GiB** (paks 151.7 → 131.1 GiB) | **Recommended.** 4096 → 2048, 2048 → 1024, 1024 → 512 |
| `--drop 2` | 12,615 | about 25.3 GiB | 4096 → 1024, 2048 → 512, 1024 → 256. Clearly blurrier in game |

`--drop 1` has been verified in game, together with `lang` and `repack`. Most of the game's texture data is in 2048 and 4096 textures, and normal maps are shrunk too, so `--drop 2` loses a lot of detail; the second level saves only about 5 GiB more. Running `--drop 1` again without a backup would remove 5.05 GiB more from 11,370 textures.

How it works:

- Only packages that hold a single `Texture2D` are changed (14,926 of the 14,930 with streamed mips; 4 use optional `.uptnl` payloads and are skipped). Lightmaps and shadowmaps in the `*_BuiltData` packages are not touched.
- Each texture's top mip records are removed from its `.uexp`, `FirstMipToSerialize` is raised by the same number and the stored size is kept, which is how the game's own textures that were cooked with a LOD bias look. The removed mips are cut from the front of the `.ubulk`. The remaining mip offsets, the skip offset, the export size and the bulk data start in the `.uasset` are updated.
- At least one streamed mip always remains, so a texture with one streamed mip is left as it is.
- Textures used by the UI are left as they are: those under a `/UI/` folder and those that widget blueprints, Slate assets or UI materials (including material instances whose parent is a UI material) reference. The tool reads the package headers of all paks to find them (about 15 seconds). A texture that the game loads only by a path in code or in an ini file cannot be found this way.
- The paks are rewritten with the compression they already have, so `mipdrop` can run before or after `repack` ([guide/repack.md](repack.md)). Each new pak is checked (every entry decompressed and compared) before it replaces the original.
- With `--backup`, every run rebuilds from the backed-up original, so a second run does not remove more levels. Files that `lang` has removed from the game pak since then stay removed.

## God of War (2018)

The game has 19 `.texpack` files (38.39 GiB in total).

| Setting | Result | Saved | Typical top resolution | Notes |
|---|---|---|---|---|
| `--max-res 1024` | 17.76 GiB | 20.63 GiB | 1024 (1024 and below untouched) | Conservative |
| `--drop 1` | 10.09 GiB | 28.30 GiB | 2048 → 1024 | Verified in game. Sharpest option |
| **`--drop 2`** | **3.80 GiB** | **34.59 GiB** | 2048 → 512 | **Recommended.** Blurrier up close |
| `--drop 3` | 2.92 GiB | 35.47 GiB | 2048 → 256 | Verified in game; little gain over `--drop 2` |

## God of War Ragnarök

The game has 25 `.texpack` files (99.81 GiB). Use the same commands, with `<GameDir>` set to the Ragnarök install folder (for example `C:\Games\God of War - Ragnarok`). The `lz4` package is required (`python -m pip install lz4`). Savings estimated with `scan`:

| Setting | Result | Saved |
|---|---|---|
| `--max-res 1024` | 39.72 GiB | 60.09 GiB |
| `--drop 1` | 27.05 GiB | 72.77 GiB |
| **`--drop 2`** | **10.78 GiB** | **89.03 GiB** |
| `--drop 3` | 8.83 GiB | 90.98 GiB |

`--drop 2` on all 25 texpacks has been verified in game (the game starts and renders). Use `--backup` and check the game before you delete the backup:

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --backup "<BackupDir>" --game gowr
python -m tool restore --backup "<BackupDir>"
```

Keep `<BackupDir>` on the same drive as the game, so the originals (99.81 GiB of texpacks and about 20 GiB of wads) are moved instead of copied. The new files need about 31 GiB of free space.

If the screen stays black even on unmodified files, check the display mode first. On the tested laptop, `DisplayMode=Fullscreen` in `<GameDir>\settings.ini` gave a black screen, and `DisplayMode=Windowed` fixed it. Back up `settings.ini` before you edit it.

If the game runs but its window never appears (the taskbar preview shows it, clicking it does not bring it up), check `WindowPosition` in `settings.ini`. Closing the game while it is minimized can save a position around `-32000, -32000`, which places the window off screen. Set it to a visible position such as `20, 40`.

Differences from God of War (2018), handled automatically:

- Pack version 6 instead of 5.
- Each `.texpack.toc` and `.wad` is an LZ4 frame. The tool uses the `lz4` package with the same settings as the game files, so unchanged content is written back byte for byte.
- The texture header is a PS5 (AGC) descriptor. The tool updates its width, height, last mip level, max mip and data size fields, and the stream size in the GNF chunk.
- Every `.wad` that uses a streamed texture stores that texture's resolution, mip count and stream size. `mipdrop --in-place` rewrites the affected wads so these values match the shrunk texpacks, keeping the originals in `--backup`. With `--out`, only texpacks are written, and they do not work in the game on their own.
- A texture can be stored in several texpacks. When you run `mipdrop` on only some texpacks, shared textures keep the chain they have in the other texpacks. For the same reason, `restore` refuses to restore one texpack alone while another texpack that shares textures with it is still shrunk, and names the texpacks to restore together.
- `verify` on a Ragnarök folder also checks that every wad matches the texpacks. It needs no backup.
- The game shows a black screen when the modification time of one of its files changes, even if the content is the same. Every file the tool rewrites keeps the modification time of the original. If you copy or edit game files by other means, keep their original times as well.

To check or repair the wads by hand (for example after restoring files yourself):

```bat
python -m tool wadsync "<GameDir>" --dry-run
python -m tool wadsync "<GameDir>" --backup "<BackupDir>"
```

## Resident Evil 7 biohazard

The same RE Engine `.pak` format as Resident Evil Village, with TEX version 35 (`natives/stm/<path>.tex.35`). The version is set per game in `tool/games.json` (`tex_version`). No extra packages are needed.

```bat
python -m tool scan "<GameDir>" --drop 2 --game re7
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --backup "<BackupDir>\mipdrop" --game re7
```

| Setting | `.pak` files (63.79 GiB before) | Saved |
|---|---|---|
| `--drop 1` | about 29.47 GiB | about 34.32 GiB |
| **`--drop 2`** | **about 22.09 GiB** | **about 41.70 GiB** |
| `--drop 3`, `--drop 4` | same as `--drop 2` | |

`--drop 2` has been verified in game, together with `lang` and `prune --dlc`.

- It works as described for Resident Evil Village below. A streaming copy never goes below the resolution of its base copy, and in this game most base copies are two levels down, so `--drop 3` and higher change nothing more than `--drop 2`.
- The tool finds 8,134 streaming textures from 8,774 material paths and shrinks all but 12 (those share their data with another resource).
- Some installs ship the `re_chunk_000.pak.patch_*.pak` files as empty 0-byte files. These are skipped and never changed.
- `mipdrop` gives the same bytes for the same texture in every pak, so the copies in the DLC packs stay identical to the ones in `re_chunk_000.pak`, and `prune --dlc` ([guide/prune.md](prune.md#dlc-copies---dlc)) can run before or after it. Use the same `--drop` for all paks.

## Resident Evil Village

All resources are in RE Engine `.pak` files: `re_chunk_000.pak` (27.50 GiB), its `re_chunk_000.pak.patch_*.pak` updates and the DLC packs in `dlc\`. No extra packages are needed.

```bat
python -m tool scan "<GameDir>" --drop 2 --game re8
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --backup "<BackupDir>" --game re8
```

| Setting | Rewritten `.pak` files (35.35 GiB before) | Saved |
|---|---|---|
| **`--drop 2`** | **17.45 GiB** | **17.90 GiB** |

`--drop 2` has been verified in game. It takes about 1 minute (plus about 5 seconds to read the materials) on a 16-core PC.

How it works:

- The game stores each streamed texture twice: a base copy at `natives/stm/<path>.tex.30`, usually a quarter of the full resolution, and the full-resolution copy at `natives/stm/streaming/<path>.tex.30`. The base copy holds the lower mips of the streaming copy byte for byte. Only the streaming copies are shrunk; base copies and textures without a streaming copy (such as the UI) are left as they are.
- The pak index stores only a hash of each path, so the tool reads the texture paths from the material (`.mdf2`) files and hashes them to find the streaming copies (17,842 of 17,846 are shrunk).
- A streaming copy never goes below the resolution of its base copy. With `--drop 2` most textures end up at their base resolution (1024-2048 becomes 256-512), and running `mipdrop` again changes nothing.
- Each changed texture gets new width, height, mip count and mip offsets; the remaining mips are copied unchanged and recompressed with deflate. The rest of the pak is copied byte for byte in the original order. The entry checksums are kept: the game does not check them.
- Only the paks that contain streaming textures are rewritten (`re_chunk_000.pak`, `patch_006` and four DLC packs). With `--backup`, the originals are moved to the backup folder and `restore` puts them back. Keep `<BackupDir>` on the same drive as the game so the 27.5 GiB `re_chunk_000.pak` is moved instead of copied; the new files need about 17 GiB of free space.
- `verify "<GameDir>" --game re8` decompresses every entry and checks every texture layout; `--quick` checks only the pak layout.

## SILENT HILL f

The textures are in `SHf\Content\Paks\SHf-Windows.ucas` (31.41 GiB), an Unreal Engine 5 IoStore container indexed by `SHf-Windows.utoc`. Given the game folder, the commands work on `SHf\Content\Paks` only; the Bonus Content app is not touched. Install the two extra packages first:

```bat
python -m pip install pyooz numpy
python -m tool scan "<GameDir>" --drop 2 --game shf
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --backup "<BackupDir>" --game shf
```

| Setting | `.ucas` after | Saved |
|---|---|---|
| `--drop 1` | 13.23 GiB | 18.18 GiB |
| **`--drop 2`** | **9.52 GiB** | **21.89 GiB** |

`--drop 2` has been verified in game. It takes about 1.5 minutes on a 16-core PC.

How it works:

- 8,212 of the 8,829 streamed textures are shrunk. The top mips are cut from each texture's `.ubulk` chunk in whole 256 KiB compression blocks, so the remaining texture data is copied without recompression. Textures whose cut would not end on a block boundary keep more levels, and at least one streamed mip always remains. 96 textures with an unusual layout are left as they are.
- Each shrunk texture's package (`.uasset`) gets its mip list, size and bulk data map updated. These packages are stored uncompressed (about 10 MB more), because `pyooz` can only decompress Oodle.
- The original `.ucas` pads chunks so they do not cross 1 MiB boundaries (4.49 GiB of padding, used for patching). The new container is written without this padding, which is part of the savings above.
- The `.utoc` keeps a BLAKE3 hash of every chunk; the tool recomputes it for every changed chunk. If the `blake3` package is installed it is used, otherwise a NumPy implementation is used.
- With `--backup`, `SHf-Windows.utoc` and `SHf-Windows.ucas` are moved to the backup folder and `restore` puts both back. Keep `<BackupDir>` on the same drive as the game so the 31 GiB `.ucas` is moved instead of copied; the new files need about 10 GiB of free space.
- `verify "<GameDir>" --game shf` decompresses every chunk and checks its hash (about 4.5 minutes); `--quick` checks only the layout.

## Stellar Blade

The textures are in `SB\Content\Paks\pakchunk0-WindowsNoEditor.ucas` (52.38 GiB) and two smaller containers (`pakchunk1`, `pakchunk2`), Unreal Engine 4.26 IoStore containers indexed by their `.utoc` files. Given the game folder, the commands work on `SB\Content\Paks`. Install `pyooz` first:

```bat
python -m pip install pyooz
python -m tool scan "<GameDir>" --drop 1 --game sb
python -m tool mipdrop "<GameDir>" --drop 1 --in-place --backup "<BackupDir>" --game sb
```

| Setting | `pakchunk0` `.ucas` after | Saved (all containers) |
|---|---|---|
| **`--drop 1`** | **29.40 GiB** | **23.3 GiB** |
| `--drop 2` | 23.94 GiB | 28.9 GiB |

The game's own texture settings (**Environment Textures** and **Character Textures**) skip a number of top mips that is counted from the top of what is left: Low skips 3, Medium 2, High 1, Very High 0 (Environment also has a higher setting that skips −1). Removing mips from the files therefore also lowers what every setting shows, and `--drop 2` without anything else looked too blurry in game. So `mipdrop --game sb` also lowers these values by the number of removed levels, in `SB/Config/Windows/WindowsGameUserSettings.ini` inside `pakchunk0-WindowsNoEditor.pak`:

| Setting | Skipped mips, original | after `--drop 1` | after `--drop 2` |
|---|---|---|---|
| Low | 3 | 2 (as before) | 1 (as before) |
| Medium | 2 | 1 (as before) | 0 (as before) |
| High | 1 | 0 (as before) | −1 (one level below High) |
| Very High | 0 | −1 (one level below Very High) | −2 (two levels below Very High) |

The desktop app uses `--drop 1`, which keeps Low, Medium and High as they were. `--drop 1` with the adjusted settings has been verified in game; `--drop 2` with the adjusted settings has not. Use `--keep-texture-settings` to leave the settings as they are. Writing `pakchunk0` takes a few minutes on a 16-core PC.

How it works:

- 16,076 of the 16,193 single-texture packages in `pakchunk0` are shrunk. UI textures (110), textures whose top mip lives in a `pakchunk*optional` container (25) and textures with a single streamed mip are left as they are. Baked lightmaps (`*_BuiltData`) are not textures of this kind and are not touched.
- The top mips are cut from the front of each texture's `.ubulk` chunk. The containers use 1 MiB compression blocks; whole blocks inside the cut are dropped, and when the cut ends inside a block, that block is kept as it is and the chunk starts in the middle of it. Nothing is recompressed, so the part of that one block that is cut away stays on disk (about 0.25 GiB over all textures with `--drop 1`, 1.3 GiB with `--drop 2`).
- Each shrunk texture's package gets its mip list, first mip and offsets updated and is stored uncompressed, because `pyooz` can only decompress Oodle. The package size in the container header is updated too.
- The `.utoc` keeps a SHA1 hash of every chunk; the tool recomputes it for every changed chunk.
- With `--backup`, the `.utoc` and `.ucas` of each changed container are moved to the backup folder and `restore` puts them back. Keep `<BackupDir>` on the same drive as the game so the 52 GiB `.ucas` is moved instead of copied; the new files need about 30 GiB of free space with `--drop 1` (24 GiB with `--drop 2`).
- With `--backup`, `mipdrop` starts from the backed-up originals, which still hold every voice language. Run `lang` after `mipdrop`, and again if you run `mipdrop` once more.
- The settings change is computed from the original values: the `.ini` gets a first line `; gow-shrink: texture mip bias lowered by N`, and a later run with another `--drop` resets the values from it. Only that one `.pak` entry changes; the rest of `pakchunk0-WindowsNoEditor.pak` is copied as it is, and with `--backup` the original `.pak` goes to the backup folder too.
- `verify "<GameDir>" --game sb` decompresses every chunk and checks its hash; `--quick` checks only the layout and the container headers.

## Notes

- When both `--drop` and `--max-res` are given, whichever removes more wins.
- At least one record always remains in each chain. The tail record that bundles the small mips is never split.
- The GNF header lives only on the top mip block, so the tool moves it to the new top block and updates its resolution, `last_level`, pitch and size fields.
- Each output is checked with `verify` right after it is written. `--in-place` writes `*.gowshrink.tmp` next to the original and swaps it in only after the check passes.
- Run `mipdrop --in-place` only once without `--backup`: a second run removes more levels from the already reduced files. With `--backup`, every run rebuilds from the backed-up originals, so `--drop 1` followed by `--drop 2` does not stack.
