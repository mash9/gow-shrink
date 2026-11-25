# Command line: recommended steps by game

The commands below run the recommended steps of each game, the same ones the desktop app runs. The results of each step are in the [README](../README.md#results).

Back to the [README](../README.md).

## Before you start

- Run the commands from the repository folder in Command Prompt.
- Replace `<GameDir>` with the folder that contains the game's `.exe`, and keep the quotes. Given the game folder, each command finds the subfolder it works on (for example `exec\wad\pc_le`, `exec` or `SHf\Content\Paks`) by itself; you can still pass that subfolder, or single files, directly. The per-game executable names and subfolders are listed in `tool/games.json`.
- Without `--backup`, these commands replace game files directly, so run each one once and start the game after each step.
- To keep the originals so a step can be undone with `restore`, add `--backup "<BackupDir>"` and use a separate folder for each step. Options and details are in the guide of each command: [mipdrop](mipdrop.md), [shvol](shvol.md), [lang](lang.md), [prune](prune.md), [lightmap](lightmap.md), [repack](repack.md).

## Atomic Heart

Verified in game with all four steps.

```bat
python -m tool mipdrop "<GameDir>" --drop 1 --in-place --backup "<BackupDir>\mipdrop" --game ah
python -m tool lightmap "<GameDir>" --drop 1 --in-place --backup "<BackupDir>\lightmap" --game ah
python -m tool lang "<GameDir>" --keep english --backup "<BackupDir>\lang" --game ah
python -m tool repack "<GameDir>" --backup "<BackupDir>\repack" --game ah
```

## God of War (2018)

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game gow
python -m tool shvol "<GameDir>" --game gow
python -m tool lang "<GameDir>" --keep english --game gow
```

## God of War Ragnarök

```bat
python -m pip install lz4
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game gowr
python -m tool lang "<GameDir>" --keep english --game gowr
```

## Resident Evil 7 biohazard

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game re7
python -m tool lang "<GameDir>" --keep english --game re7
python -m tool prune "<GameDir>" --dlc --game re7
```

## Resident Evil Village

```bat
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game re8
python -m tool lang "<GameDir>" --keep english --game re8
python -m tool prune "<GameDir>" --game re8
```

## SILENT HILL f

```bat
python -m pip install pyooz numpy
python -m tool mipdrop "<GameDir>" --drop 2 --in-place --game shf
```
