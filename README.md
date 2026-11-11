# gow-shrink

A tool that reduces the size of God of War (PC) `.texpack` files by removing the top mip levels of each texture. Requires Python 3.11+, standard library only.

## Recommended settings

Sizes are for all 19 `.texpack` files of the PC release (38.39 GiB in total).

| Setting | Result | Saved | Typical top resolution | Status |
|---|---|---|---|---|
| `--max-res 1024` | 17.76 GiB | 20.63 GiB | 1024 (1024 and below untouched) | Conservative; files verified |
| **`--drop 1`** | **10.09 GiB** | **28.30 GiB** | 2048 → 1024 | **Recommended.** Verified in game: loads and plays normally |
| `--drop 2` | 3.80 GiB | 34.59 GiB | 2048 → 512 | Aggressive; files verified, visibly blurrier up close |
| `--drop 3` | 2.92 GiB | 35.47 GiB | 2048 → 256 | Little gain over `--drop 2`; most chains are already down to one record |

Start with `--drop 1`. Go further only if you need the space and accept lower texture detail.

## Usage

Replace `<GameDir>` with your God of War install folder and `<BackupDir>` with a folder for the original files. Keep `<BackupDir>` on the same drive as the game so originals are moved, not copied.

```powershell
$wad = '<GameDir>\exec\wad\pc_le'

# Estimate savings (does not touch any file)
python -m tool scan $wad --drop 1

# Recommended: shrink every texpack in place; originals are moved to <BackupDir>
python -m tool shrink $wad --drop 1 --in-place --backup <BackupDir>

# Try a single file first, writing the result to another folder (originals untouched)
python -m tool shrink "$wad\090_caldera_010.texpack" --drop 1 --out <OutputDir>

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
| `--drop N` | Remove the top N mip records of every texture (default 1) |
| `--max-res R` | Remove records until `max(width, height)` is at most R |
| `--min-res R` | Never let the remaining top resolution fall below R |

- When both `--drop` and `--max-res` are given, whichever removes more wins.
- At least one record always remains in each chain. The tail record that bundles the small mips is never split.

## Behavior

- Each output file is re-checked with `verify` right after it is written. If the check fails, the original is not replaced.
- `--in-place` writes `*.gowshrink.tmp` next to the original, verifies it, and only then swaps it in.
- If `<BackupDir>` already holds a file, the tool rebuilds from that backup copy instead of the current game file. Running `--drop 1` and later `--drop 2` therefore both start from the backed-up file; they do not stack. If you delete `<BackupDir>`, the next run starts from the already shrunk game files and the drops do stack.
- The `.texpack` GNF header lives only on the top mip block, so the tool moves it to the new top block and updates its resolution, `last_level`, pitch and size fields.

## Tests

```powershell
$env:GOW_TEXPACK = '<GameDir>\exec\wad\pc_le\090_caldera_010.texpack'
python -m unittest tests.test_texpack -v
```

The tests read the given game file and write results to a temporary folder.
