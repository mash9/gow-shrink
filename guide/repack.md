# repack: compress uncompressed .pak files

`repack` compresses the entries of uncompressed Unreal Engine 4 `.pak` files. It works on Atomic Heart only.

Back to the [README](../README.md).

Not yet verified in game.

## Why

All 19 `.pak` files of Atomic Heart (`AtomicHeart\Content\Paks\`, 151.7 GiB) are stored without any compression: the compression method slots in their footers are empty and every entry is stored as is. The game's executable can read zlib-compressed pak entries, so the same files can be stored much smaller.

Estimated from samples with `--dry-run`:

| Paks | Before | After (estimate) | Saved |
|---|---|---|---|
| All 19 | 151.67 GiB | about 84.84 GiB | about 66.8 GiB |

These numbers are for unmodified paks. After `mipdrop` and `lang`, less data is left to compress, so `repack` saves less on its own.

## Commands

Show the estimate (does not touch any file):

```bat
python -m tool repack "<GameDir>" --dry-run
```

Apply, keeping the originals so the change can be undone:

```bat
python -m tool repack "<GameDir>" --backup "<BackupDir>\repack"
python -m tool restore --backup "<BackupDir>\repack"
```

| Option | Meaning |
|---|---|
| `--method zlib` / `--method lz4` | Compression method (default `zlib`). Whether the game reads LZ4 paks has not been checked |
| `--level N` | Compression level (zlib 1-9, lz4 1-12; default 6) |
| `--workers N` | Worker processes (default: CPU count - 1) |
| `--backup DIR` | Move the original paks here so `restore` can put them back; without it the originals are replaced |
| `--dry-run` | Print the estimate without writing anything |
| `--game ah` | Refuse other games (default: detect from `AtomicHeart.exe`) |

## How it works

- Every entry is cut into 64 KiB blocks and each block is compressed on its own, as the engine expects. An entry stays uncompressed if compression does not make it smaller.
- Wwise audio (`.wem`, `.bnk`) and video (`.mp4`, `.bk2`) are already compressed and are stored as they are.
- The pak index (directory index, path hash index, encoded entries) is rebuilt with the new offsets and sizes, and its SHA-1 hashes are recomputed. The file list, the mount point and the path hashes stay the same.
- The original paks pad entries to 2048-byte boundaries for patching. The new paks are written without this padding.
- Each new pak is written as `*.gowshrink.tmp` next to the original and checked before it replaces the original: every entry is decompressed block by block and compared with the SHA-1 stored in the original pak.
- A pak that is already compressed is skipped, so running `repack` again changes nothing.
- It takes about 300 MB/s on a 16-core PC. The new file of the largest pak (33 GiB) needs about 14 GiB of free space.

## Order with the other steps

`repack` always starts from the pak in the game folder, so it keeps what `mipdrop` and `lang` have already changed. `mipdrop` and `lang` write their results with the compression the game pak already has. Running `repack` last compresses the least data; the desktop app runs the steps in the order `mipdrop`, `lang`, `repack`.
