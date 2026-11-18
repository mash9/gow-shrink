# shvol: shrink lighting volumes

`shvol` halves the resolution of the spherical-harmonics (SH) lighting volumes in `.wad` files. It works on God of War (2018) only.

Back to the [README](../README.md).

## What it changes

The SH lighting volumes are 3D textures named `<8 hex digits>SH0` to `SH3` (868 volumes in 185 wads, 2.41 GiB). Each volume is downsampled 2x on every axis with a 2x2x2 box filter, so its data shrinks to about 1/8. All `.wad` files go from 12.61 GiB to 10.52 GiB.

The change has been tested in game by playing through with all three God of War (2018) steps applied.

## Commands

Show what would change (does not touch any file):

```bat
python -m tool shvol "<GameDir>" --dry-run
```

Apply, keeping the originals so the change can be undone:

```bat
python -m tool shvol "<GameDir>" --backup "<BackupDir>\shvol"
```

| Option | Meaning |
|---|---|
| `--backup DIR` | Move the original `.wad` files here so `restore` can put them back; without it the originals are replaced |
| `--dry-run` | Print the plan without writing anything |
| `--game gow` | Refuse files that belong to another game (default: detect from the files) |

## How it works

- A resized volume shifts every later entry in the wad. The tool rewrites the whole wad, updates the entry sizes, the GNF header (width, height, depth, pitch, data size) and the descriptor size field, and recomputes every `autopad` entry so the next entry still starts on a 64 KiB boundary.
- Before a rewritten wad replaces the original, the tool checks that entry order and names are unchanged, all non-SH entries are byte-identical, `autopad` alignment holds, and the SH fields match the new data. Rebuilding at the original resolution reproduces all 185 wads byte for byte.
- God of War Ragnarök is not supported, and the command refuses its files.
