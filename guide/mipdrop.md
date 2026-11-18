# mipdrop: remove top texture mip levels

`mipdrop` removes the top mip levels of every streamed texture and rewrites the `.texpack` files together with their `.toc` (God of War games), the `.utoc`/`.ucas` container (SILENT HILL f) or the `.pak` files (Resident Evil Village). It was called `shrink` before, and `shrink` still works as an alias.

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
| `--game gow` / `--game gowr` / `--game shf` / `--game re8` | Refuse files that belong to another game (default: detect from the files) |
| `--dry-run` | Print the plan without writing anything |

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

## Notes

- When both `--drop` and `--max-res` are given, whichever removes more wins.
- At least one record always remains in each chain. The tail record that bundles the small mips is never split.
- The GNF header lives only on the top mip block, so the tool moves it to the new top block and updates its resolution, `last_level`, pitch and size fields.
- Each output is checked with `verify` right after it is written. `--in-place` writes `*.gowshrink.tmp` next to the original and swaps it in only after the check passes.
- Run `mipdrop --in-place` only once without `--backup`: a second run removes more levels from the already reduced files. With `--backup`, every run rebuilds from the backed-up originals, so `--drop 1` followed by `--drop 2` does not stack.
