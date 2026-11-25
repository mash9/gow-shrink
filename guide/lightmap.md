# lightmap: remove top lightmap mip levels

`lightmap` removes the top mip levels of the baked lightmap and shadowmap textures in the `*_BuiltData` packages and rewrites the `.pak` files. It works on Atomic Heart only. It is separate from [`mipdrop`](mipdrop.md), which handles the other textures and does not touch these.

Back to the [README](../README.md).

Verified in game: `--drop 1` was applied after `mipdrop --drop 1`, `lang --keep english` and `repack`, and the game was played without problems. The 19 paks went from 57.74 GiB to 45.44 GiB.

## What it changes

The precomputed lighting of each level is stored in a `<level>_BuiltData` package: `LightMapTexture2D` and `ShadowMapTexture2D` exports with the baked light and shadows of the static meshes, and one `MapBuildDataRegistry` export (volumetric lightmap, reflection captures and the per-mesh lightmap data). The texture mips are in the package's `.ubulk` (40.95 GiB before compression, 17.6 GiB in zlib paks).

| Setting | Packages | Textures | Texture data removed (before pak compression) |
|---|---|---|---|
| **`--drop 1`** | **852** | **13,890** | **30.74 GiB** (LightMap 28.75, ShadowMap 1.99) |

With `--drop 1`, 4096 lightmaps become 2048, 2048 become 1024 (6,009 textures) and 1024 become 512 (4,638). In paks already compressed by `repack`, it saved 12.30 GiB. Baked shadows and indirect light on static surfaces get softer; light can also leak at the edges of lightmap charts, because the padding between charts in the atlas is halved too.

This is not the same as `shvol` (God of War): `shvol` resamples a 3D grid of lighting probes, while `lightmap` drops mip levels of 2D textures mapped onto surfaces. The volumetric lightmap in the registry, which is the closest thing to `shvol` in this game, is left as it is.

## Commands

Show the plan (does not touch any file):

```bat
python -m tool lightmap "<GameDir>" --drop 1 --in-place --dry-run --game ah
```

Apply, keeping the originals so the change can be undone:

```bat
python -m tool lightmap "<GameDir>" --drop 1 --in-place --backup "<BackupDir>\lightmap" --game ah
python -m tool restore --backup "<BackupDir>\lightmap"
```

| Option | Meaning |
|---|---|
| `--drop N` | Remove the top N mip records of every lightmap and shadowmap texture (default 1 when no option is given) |
| `--max-res R` | Remove records until max(width, height) <= R |
| `--min-res R` | Never let the remaining top resolution fall below R |
| `--in-place` | Replace the paks in the game folder |
| `--out DIR` | Write the new paks to this folder instead (originals untouched) |
| `--backup DIR` | With `--in-place`, move the originals here so `restore` can put them back |
| `--dry-run` | Print the plan without writing anything |
| `--game ah` | Refuse files that belong to another game (default: detect from the files) |

## How it works

- Only packages whose exports are all `LightMapTexture2D`, `ShadowMapTexture2D` or `MapBuildDataRegistry` are changed. Each texture is read the same way as in `mipdrop` (same platform data layout), within its own export.
- The top mip records of each texture are removed from the `.uexp` and `FirstMipToSerialize` is raised by the same number. The removed mips are cut out of the `.ubulk`; the textures share one `.ubulk`, so the cuts are in the middle of the file, not only at the front.
- Every position that moves is updated: the remaining mip offsets (in the `.ubulk` and in the package), each texture's skip offset, the offset and size of every export (the registry export moves when a texture before it shrinks) and the bulk data start in the `.uasset`. The registry export itself is copied byte for byte; it holds no bulk data records or package offsets.
- At least one streamed mip always remains, so a texture with one streamed mip is left as it is.
- Before a pak is written, each changed package is parsed again: the remaining mip chain of every texture, the inline mip data and the registry bytes must match the original. Each new pak is checked (every entry decompressed and compared) before it replaces the original.
- The paks are rewritten with the compression they already have, so `lightmap` can run before or after `repack` ([guide/repack.md](repack.md)).
- With `--backup`, every run rebuilds from the backed-up original, so a second run does not remove more levels. Use a separate backup folder for this step: the backup of another step holds paks from before that step. Without a backup, running it again removes one more level.
