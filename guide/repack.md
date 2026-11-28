# repack: compress uncompressed .pak files

`repack` compresses the uncompressed entries of Unreal Engine 4 `.pak` files. It works on Atomic Heart and Stellar Blade.

Back to the [README](../README.md).

Verified in game (zlib), after `mipdrop --drop 1` and `lang --keep english`: the 19 paks went from 131.1 GiB to 57.7 GiB. `lightmap` was run after it on the compressed paks and also works. LZ4 (`--method lz4`) has not been tried.

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
| `--game ah` / `--game sb` | Refuse other games (default: detect from `AtomicHeart.exe` or `SB.exe`) |

## How it works

- Every entry is cut into 64 KiB blocks and each block is compressed on its own, as the engine expects. An entry stays uncompressed if compression does not make it smaller.
- Wwise audio (`.wem`, `.bnk`) and video (`.mp4`, `.bk2`) are already compressed and are stored as they are.
- The pak index (directory index, path hash index, encoded entries) is rebuilt with the new offsets and sizes, and its SHA-1 hashes are recomputed. The file list, the mount point and the path hashes stay the same.
- The original paks pad entries to 2048-byte boundaries for patching. The new paks are written without this padding.
- Each new pak is written as `*.gowshrink.tmp` next to the original and checked before it replaces the original: every entry is decompressed block by block and compared with the SHA-1 stored in the original pak.
- Entries that are already compressed are copied as they are and keep their compression method (a pak can hold several, such as `Zlib` and `Oodle`). A pak with nothing left worth compressing (estimated gain under 1% or 1 MiB) is skipped, so running `repack` again changes nothing. A pak whose first supported method is not the `--method` you give is skipped too.
- It takes about 300 MB/s on a 16-core PC. The new file of the largest pak (33 GiB) needs about 14 GiB of free space.

## Stellar Blade

Verified in game (zlib), after `mipdrop --drop 1` and `lang --keep korean`: `pakchunk0-WindowsNoEditor.pak` went from 1.90 GiB to 1.37 GiB.

Almost everything in Stellar Blade is in the IoStore containers (`.utoc`/`.ucas`), which `repack` does not touch. The only large `.pak` is `SB\Content\Paks\pakchunk0-WindowsNoEditor.pak` (1.90 GiB). Its compression method slots are `Zlib` and `Oodle`, and most of its size is the shader archive `ShaderArchive-SB-PCD3D_SM5.ushaderbytecode`, stored uncompressed. `pyooz` is needed to check the copied Oodle entries.

```bat
python -m pip install pyooz
python -m tool repack "<GameDir>" --backup "<BackupDir>\repack" --game sb
```

| Entry | Before | After |
|---|---|---|
| `ShaderArchive-SB-PCD3D_SM5.ushaderbytecode` | 1,827.8 MiB | 1,298.3 MiB |
| `ShaderArchive-Global-PCD3D_SM5.ushaderbytecode` | 19.6 MiB | 12.8 MiB |
| whole `pakchunk0-WindowsNoEditor.pak` | 1.90 GiB | 1.37 GiB |

- Each shader inside the archive is already compressed with LZ4 at its highest level, so recompressing the shaders one by one gains nothing. Neighbouring shaders share a lot of data, which zlib over 64 KiB blocks finds.
- The other five `.pak` files are empty (339 bytes) and are skipped.
- The game reads the shader archive in pieces. With a compressed entry each piece decompresses its 64 KiB blocks, so loading may take a little longer.

## Order with the other steps

`repack` always starts from the pak in the game folder, so it keeps what `mipdrop`, `lightmap` and `lang` have already changed. `mipdrop`, `lightmap` and `lang` write their results with the compression the game pak already has. Running `repack` last compresses the least data; the desktop app runs the steps in the order `mipdrop`, `lightmap`, `lang`, `repack`.
