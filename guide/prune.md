# prune: remove replaced patch data

`prune` removes the entries that a later patch pak replaces from `re_chunk_000.pak` and its `re_chunk_000.pak.patch_*.pak` updates. With `--dlc`, it also removes the entries of the DLC paks that are byte-identical copies of entries in the main paks. It works on Resident Evil 7 biohazard and Resident Evil Village.

Back to the [README](../README.md).

Verified in game, with `mipdrop` and `lang` applied first: `--dlc` on Resident Evil 7 biohazard, the patch chain on Resident Evil Village.

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
| `--dlc` | Also remove DLC entries that are byte-identical to the main paks (see below) |
| `--dry-run` | Print the plan without writing anything |
| `--game re7` / `--game re8` | Refuse other games (default: detect from `re7.exe` / `re8.exe`) |

## How it works

- For every path, the copy in the last pak of the chain is kept and all earlier copies are removed from their paks. The last pak (`patch_009`) is never changed.
- Only the entry is removed from the pak's table. Data that a kept entry still shares is kept, and the rest of each pak is copied byte for byte in the original order, without recompression.
- A pak with no entries left is not deleted. It is written as an empty 16-byte pak, the same as the `patch_002` and `patch_003` files the game ships, so the patch numbers stay continuous.
- Before anything is written, every removed entry's replacement is decompressed and must have the same resource type. Each new pak is checked (layout and the CRC of every kept entry) before it replaces the original.
- Without `--dlc`, the DLC paks in `dlc\` are not read or changed.
- Some installs ship the patch paks as empty 0-byte files (Resident Evil 7 biohazard). These are skipped; the chain is then `re_chunk_000.pak` alone and there is nothing to remove.
- Running `prune` again changes nothing.

## DLC copies (`--dlc`)

The DLC paks of Resident Evil 7 biohazard store full copies of many resources of `re_chunk_000.pak`. `--dlc` removes the copies from the DLC paks and keeps the one in the main paks:

| Pak | Copies | Size (original) |
|---|---|---|
| `re_dlc_stm_529930.pak` | 12,117 | 9.65 GiB -> 1.18 GiB |
| `re_dlc_stm_530610.pak` | 15,574 | 12.63 GiB -> 1.91 GiB |
| `re_dlc_stm_530611.pak` | 5,957 | 11.68 GiB -> 6.55 GiB |
| `re_dlc_stm_564190.pak` | 3,105 | 5.99 GiB -> 4.01 GiB |
| 11 small DLC paks | 8-9 each | about 2 MiB each |
| **Total** | | **26.33 GiB** (about 6.0 GiB after `mipdrop`) |

```bat
python -m tool prune "<GameDir>" --dlc --dry-run --game re7
python -m tool prune "<GameDir>" --dlc --backup "<BackupDir>\prune" --game re7
```

- An entry is removed only when the same path is in the main chain and the contents are the same byte for byte: the raw data is compared when both use the same compression, otherwise both are decompressed and compared. The entry checksums are not used.
- The main paks are always read from the game folder. With `--backup`, the DLC paks are rebuilt from the originals in the backup folder.
- Entries whose contents differ from the main paks are kept. Entries that only the DLC paks share with each other (about 0.57 GiB) are kept too: unlike the patch paks, a DLC pak may be read without the others.
- It relies on the main pak copy being available whenever a DLC is played. This was verified in game on Resident Evil 7 biohazard; it has not been tried on Resident Evil Village.

## Order with the other steps

`mipdrop`, `lang` and `prune` all rewrite the same paks. Use a separate backup folder for each step, run `prune` last, and restore in reverse order (`prune`, then `lang`, then `mipdrop`). Running `mipdrop` or `lang` again with their own backup rebuilds the paks from the originals and brings the replaced entries back; run `prune` again afterwards.
