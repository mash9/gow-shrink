# prune: remove replaced patch data

`prune` removes the entries that a later patch pak replaces from `re_chunk_000.pak` and its `re_chunk_000.pak.patch_*.pak` updates. It works on Resident Evil Village only.

Back to the [README](../README.md).

Verified in game, with `mipdrop` and `lang` applied first.

## What it removes

The game reads `re_chunk_000.pak` and then `patch_001`, `patch_002`, ... in number order. When several of them contain the same path, the game uses the one in the highest-numbered pak. The older copies stay in the earlier paks but are never read:

| Pak | Replaced entries | Size |
|---|---|---|
| `re_chunk_000.pak` | 2,376 | 10.75 GiB -> 9.94 GiB |
| `re_chunk_000.pak.patch_001.pak` | 328 | 0.76 GiB -> 1.8 MiB |
| `re_chunk_000.pak.patch_004.pak` | 5 (all) | 12.0 MiB -> empty |
| `re_chunk_000.pak.patch_005.pak` | 16 | 23.1 MiB -> 1.8 MiB |
| `patch_006`, `patch_007`, `patch_008` | 432 | less than 1 MiB |
| **Total** | | **1.60 GiB** |

Most of the replaced data is scene, prefab and material files and the SDF volumes, which `re_chunk_000.pak`, `patch_001` and `patch_006` each store in full. The sizes above were measured after `mipdrop` and `lang`.

## Commands

Show what would be removed (does not touch any file):

```bat
python -m tool prune "<GameDir>" --dry-run
```

Apply, keeping the originals so the change can be undone:

```bat
python -m tool prune "<GameDir>" --backup "<BackupDir>\prune"
python -m tool restore --backup "<BackupDir>\prune"
```

| Option | Meaning |
|---|---|
| `--backup DIR` | Move the original paks here so `restore` can put them back; without it the originals are replaced |
| `--dry-run` | Print the plan without writing anything |
| `--game re8` | Refuse other games (default: detect from `re8.exe`) |

## How it works

- For every path, the copy in the last pak of the chain is kept and all earlier copies are removed from their paks. The last pak (`patch_009`) is never changed.
- Only the entry is removed from the pak's table. Data that a kept entry still shares is kept, and the rest of each pak is copied byte for byte in the original order, without recompression.
- A pak with no entries left is not deleted. It is written as an empty 16-byte pak, the same as the `patch_002` and `patch_003` files the game ships, so the patch numbers stay continuous.
- Before anything is written, every removed entry's replacement is decompressed and must have the same resource type. Each new pak is checked (layout and the CRC of every kept entry) before it replaces the original.
- The DLC paks in `dlc\` are not read or changed. About 120 MiB is also stored twice between the DLC paks and the main paks, but the order in which the game reads them is not known.
- Running `prune` again changes nothing.

## Order with the other steps

`mipdrop`, `lang` and `prune` all rewrite the same paks. Use a separate backup folder for each step, run `prune` last, and restore in reverse order (`prune`, then `lang`, then `mipdrop`). Running `mipdrop` or `lang` again with their own backup rebuilds the paks from the originals and brings the replaced entries back; run `prune` again afterwards.
