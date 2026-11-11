# gow-shrink

A tool that reduces the size of God of War (PC) `.texpack` files by removing the top mip levels of each texture. Requires Python 3.11+, standard library only.

## Recommended settings

Sizes are for all 19 `.texpack` files of the PC release (38.39 GiB in total).

| Setting | Result | Saved | Typical top resolution | Status |
|---|---|---|---|---|
| `--max-res 1024` | 17.76 GiB | 20.63 GiB | 1024 (1024 and below untouched) | Conservative; files verified |
| `--drop 1` | 10.09 GiB | 28.30 GiB | 2048 → 1024 | Verified in game: loads and plays normally. Choose this if texture detail matters most |
| **`--drop 2`** | **3.80 GiB** | **34.59 GiB** | 2048 → 512 | **Recommended.** Best size/quality trade-off; blurrier up close. The game also runs normally with `--drop 3`, which removes more |
| `--drop 3` | 2.92 GiB | 35.47 GiB | 2048 → 256 | Little gain over `--drop 2`; most chains are already down to one record |

Start with `--drop 2`. Use `--drop 1` instead if you want sharper textures and can spare about 6 GiB more.

## Recommended commands

Apply the two changes one after the other on an unmodified install, and check the game after each step.

```powershell
$wad = '<GameDir>\exec\wad\pc_le'

# Step 1: textures (.texpack), about 34.6 GiB saved
python -m tool shrink $wad --drop 2 --in-place
python -m tool verify $wad
# -> start the game and check it loads and plays

# Step 2: SH lighting volumes (.wad), about 2.1 GiB saved (experimental)
python -m tool shvol $wad
# -> start the game again and check lighting in several areas
```

These commands replace the game files directly and keep no backup. Run each command only once: running it again shrinks the already reduced files a second time. To get the original files back, reinstall or verify the game files with your launcher. Add `--backup <BackupDir>` to either command if you want to be able to undo it with `restore` (see below).

## Usage

Replace `<GameDir>` with your God of War install folder and `<BackupDir>` with a folder for the original files. Keep `<BackupDir>` on the same drive as the game so originals are moved, not copied.

```powershell
$wad = '<GameDir>\exec\wad\pc_le'

# Estimate savings (does not touch any file)
python -m tool scan $wad --drop 2

# Recommended: shrink every texpack in place; originals are moved to <BackupDir>
python -m tool shrink $wad --drop 2 --in-place --backup <BackupDir>

# Try a single file first, writing the result to another folder (originals untouched)
python -m tool shrink "$wad\090_caldera_010.texpack" --drop 2 --out <OutputDir>

# Integrity check
python -m tool verify $wad

# Restore originals
python -m tool restore --backup <BackupDir>                            # everything
python -m tool restore --backup <BackupDir> 090_caldera_010.texpack    # one file
```

After confirming the game runs, you can delete `<BackupDir>` to actually free the disk space. Without it, getting the original textures back requires reinstalling the game.

## Removal options

| Option | Meaning |
|---|---|
| `--drop N` | Remove the top N mip records of every texture (default 1 when no option is given; 2 is recommended) |
| `--max-res R` | Remove records until `max(width, height)` is at most R |
| `--min-res R` | Never let the remaining top resolution fall below R |

- When both `--drop` and `--max-res` are given, whichever removes more wins.
- At least one record always remains in each chain. The tail record that bundles the small mips is never split.

## Behavior

- Each output file is re-checked with `verify` right after it is written. If the check fails, the original is not replaced.
- `--in-place` writes `*.gowshrink.tmp` next to the original, verifies it, and only then swaps it in.
- Without `--backup`, `--in-place` replaces the original once the output passes verification; nothing is kept.
- If `<BackupDir>` already holds a file, the tool rebuilds from that backup copy instead of the current game file. Running `--drop 1` and later `--drop 2` therefore both start from the backed-up file; they do not stack. If you delete `<BackupDir>`, the next run starts from the already shrunk game files and the drops do stack.
- The `.texpack` GNF header lives only on the top mip block, so the tool moves it to the new top block and updates its resolution, `last_level`, pitch and size fields.

## Lighting volumes in `.wad` files (`shvol`, experimental)

`shvol` halves the resolution of the spherical-harmonics (SH) lighting volumes stored in `.wad` files. These are 3D textures named `<8 hex digits>SH0` to `SH3` (868 volumes in 185 wads, 2.41 GiB). Each volume is downsampled 2x on every axis with a 2x2x2 box filter, so its data shrinks to about 1/8.

| | Before | After |
|---|---|---|
| The 185 wads that hold SH volumes | 9.78 GiB | 7.69 GiB |
| All `.wad` files | 12.61 GiB | 10.52 GiB (2.09 GiB saved) |

**Status:** the files pass verification, but the change has not been tested in game yet. Indirect lighting may look softer or shifted. Keep `<BackupDir>` until you have checked several areas.

```powershell
$wad = '<GameDir>\exec\wad\pc_le'

# Show what would change (does not touch any file)
python -m tool shvol $wad --backup <BackupDir> --dry-run

# Apply to every wad that holds SH volumes; originals are moved to <BackupDir>
python -m tool shvol $wad --backup <BackupDir>

# Undo
python -m tool restore --backup <BackupDir>
```

- `--backup` is optional. Without it the wads are replaced directly and running the command again halves the volumes again.
- With `--backup`, use a separate folder from the one used for `shrink`, so each change can be restored on its own. The tool then always rebuilds from the backed-up original, so running it twice does not halve the volumes twice.
- A changed volume shifts every later entry in the wad. The tool rewrites the whole wad, updates the entry sizes, the GNF header (width, height, depth, pitch, data size) and the descriptor size field, and recomputes every `autopad` entry so the next entry still starts on a 64 KiB boundary.
- Every rewritten wad is checked before it replaces the original. Entry order and names must be unchanged, all non-SH entries must be byte-identical, `autopad` alignment must hold, and the SH fields must match the new data. Rebuilding at the original resolution reproduces all 185 wads byte for byte.

## Tests

```powershell
$env:GOW_TEXPACK = '<GameDir>\exec\wad\pc_le\090_caldera_010.texpack'
python -m unittest tests.test_texpack -v
```

The tests read the given game file and write results to a temporary folder.
