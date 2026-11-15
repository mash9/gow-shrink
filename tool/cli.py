from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass

from . import gowr_wad as gw
from . import lang as lang_mod
from . import texpack as tx

GiB = 1 << 30
MANIFEST = 'manifest.json'


def fmt_size(n: int) -> str:
    return f'{n / GiB:7.2f} GiB' if abs(n) >= GiB / 10 else f'{n / (1 << 20):7.1f} MiB'


def policy_from(args) -> tx.DropPolicy:
    if not args.drop and args.max_res is None:
        args.drop = 1
    return tx.DropPolicy(args.drop, args.max_res, args.min_res)


def add_policy_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group('removal options (default: --drop 1)')
    g.add_argument('--drop', type=int, default=0, metavar='N',
                   help='remove the top N mip records of every texture')
    g.add_argument('--max-res', type=int, metavar='R',
                   help='remove records until max(width, height) <= R')
    g.add_argument('--min-res', type=int, default=0, metavar='R',
                   help='never let the remaining top resolution fall below R')


@dataclass(frozen=True)
class GameInfo:
    title: str
    exe: str
    texpack_version: int | None
    dirs: dict[str, tuple[str, ...]]
    lang: lang_mod.LangConfig | None = None


GAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.json')


def load_games(path: str = GAMES_FILE) -> dict[str, GameInfo]:
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    return {g: GameInfo(v['title'], v['exe'], v.get('texpack_version'),
                        {role: tuple(rel.replace('\\', '/').split('/')) for role, rel in v.get('dirs', {}).items()},
                        lang_mod.LangConfig.from_json(v['lang']) if 'lang' in v else None)
            for g, v in raw.items()}


def games_with(attr: str) -> tuple[str, ...]:
    return tuple(g for g, i in GAME_INFO.items() if getattr(i, attr) is not None)


GAME_INFO = load_games()
GAMES = {g: i.title for g, i in GAME_INFO.items()}
GAME_OF_VERSION = {i.texpack_version: g for g, i in GAME_INFO.items() if i.texpack_version is not None}
GAME_EXE = {g: i.exe for g, i in GAME_INFO.items()}
ARG_DIR_ROLES = (('paths', 'wad'), ('dir', 'wad'), ('exec_dir', 'exec'))


def add_game_arg(p: argparse.ArgumentParser, games: tuple[str, ...] = tuple(GAME_INFO)) -> None:
    names = ', '.join(f'{g} = {GAMES[g]}' for g in games)
    p.add_argument('--game', choices=list(games), default=None,
                   help=f'target game ({names}); default: detect from the files')
    p.set_defaults(games=games)


def _game_of_dir(path: str) -> str | None:
    d = os.path.abspath(path)
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    for _ in range(5):
        found = [g for g, exe in GAME_EXE.items() if os.path.isfile(os.path.join(d, exe))]
        if len(found) == 1:
            return found[0]
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def resolve_game(args) -> str:
    texture_cmd = hasattr(args, 'paths')
    detected: dict[str, list[str]] = collections.defaultdict(list)
    if texture_cmd:
        for path in tx.iter_texpacks(args.paths):
            g = GAME_OF_VERSION.get(_pack_version(path))
            if g:
                detected[g].append(os.path.basename(path))
    else:
        target = getattr(args, 'dir', None) or getattr(args, 'exec_dir', None)
        g = _game_of_dir(target) if target else None
        if g:
            detected[g].append(GAME_EXE[g])

    if len(detected) > 1:
        raise SystemExit('files of several games were given: '
                         + '; '.join(f'{g}: {", ".join(v[:3])}' for g, v in detected.items())
                         + '. Run each game separately.')
    found = next(iter(detected), None)
    if args.game and found and found != args.game:
        raise SystemExit(f'--game {args.game} ({GAMES[args.game]}) was given, but the files belong to '
                         f'{found} ({GAMES[found]}): {", ".join(detected[found][:3])}')
    game = args.game or found
    if game is None:
        if len(args.games) == 1:
            game = args.games[0]
        else:
            raise SystemExit('could not tell which game the files belong to; pass '
                             + ' or '.join(f'--game {g}' for g in args.games))
    if game not in args.games:
        raise SystemExit(f'{args.cmd} is not supported for {GAMES[game]}')
    return game


def game_of_root(path: str) -> str | None:
    if not os.path.isdir(path):
        return None
    found = [g for g, exe in GAME_EXE.items() if os.path.isfile(os.path.join(path, exe))]
    return found[0] if len(found) == 1 else None


def game_subdir(path: str, role: str) -> str:
    game = game_of_root(path)
    rel = GAME_INFO[game].dirs.get(role) if game else None
    if rel is None:
        return path
    d = os.path.join(path, *rel)
    if not os.path.isdir(d):
        raise SystemExit(f'{path} looks like the {GAMES[game]} folder, but {os.path.join(*rel)} is missing')
    return d


def resolve_dirs(args) -> None:
    changed = []
    for attr, role in ARG_DIR_ROLES:
        old = getattr(args, attr, None)
        if not old:
            continue
        olds = old if isinstance(old, list) else [old]
        news = [game_subdir(p, role) for p in olds]
        changed += [n for o, n in zip(olds, news) if n != o]
        setattr(args, attr, news if isinstance(old, list) else news[0])
    for d in dict.fromkeys(changed):
        print(f'using {d}')


class Progress:
    def __init__(self, label: str):
        self.label = label
        self.t0 = time.time()
        self.last = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.time()
        if now - self.last < 0.5 and done < total:
            return
        self.last = now
        pct = 100 * done / total if total else 100
        rate = done / max(now - self.t0, 1e-6) / (1 << 20)
        sys.stderr.write(f'\r  {self.label}: {pct:5.1f}%  {rate:6.0f} MiB/s')
        if done >= total:
            sys.stderr.write('\n')
        sys.stderr.flush()




def cmd_scan(args) -> int:
    policy = policy_from(args)
    tot_before = tot_after = 0
    res_before = collections.Counter()
    res_after = collections.Counter()
    print(f'{"file":32} {"textures":>8} {"removed":>8} {"current":>12} {"estimate":>12} {"saved":>12}')
    for path in tx.iter_texpacks(args.paths):
        tp = tx.Texpack.open(path)
        plan = tx.make_plan(tp, policy)
        after = plan.new_file_size()
        for t, n in zip(tp.textures, plan.drops):
            res_before[t.chain[-1].max_dim] += 1
            res_after[t.chain[-1 - n].max_dim] += 1
        tot_before += tp.file_size
        tot_after += after
        print(f'{os.path.basename(path):32} {tp.texture_count:8} {sum(plan.drops):8} '
              f'{fmt_size(tp.file_size)} {fmt_size(after)} {fmt_size(tp.file_size - after)}')
    print(f'{"total":32} {"":8} {"":8} {fmt_size(tot_before)} {fmt_size(tot_after)} '
          f'{fmt_size(tot_before - tot_after)}')
    print()
    print('textures by top resolution (max(width, height))')
    for r in sorted(set(res_before) | set(res_after)):
        print(f'  {r:5}: {res_before[r]:7} -> {res_after[r]:7}')
    return 0


def cmd_verify(args) -> int:
    bad = 0
    for path in tx.iter_texpacks(args.paths):
        errs = tx.verify(path, deep=not args.quick)
        name = os.path.basename(path)
        if errs:
            bad += 1
            print(f'[FAIL] {name}')
            for e in errs:
                print(f'    {e}')
        else:
            print(f'[OK]   {name}')

    if not args.quick:
        for d in sorted({os.path.abspath(p) for p in args.paths if os.path.isdir(p)}):
            if not any(_pack_version(p) == tx.VERSION_GOWR for p in tx.iter_texpacks([d])):
                continue
            try:
                results = gw.sync(d, dry_run=True, progress=Progress('checking wads'))
            except (gw.WadError, tx.FormatError) as e:
                print(f'[FAIL] wads: {e}')
                bad += 1
                continue
            errors = [e for r in results for e in r.errors]
            stale = [r for r in results if r.action == 'rewrite']
            refs = sum(r.refs for r in results)
            if errors or stale:
                bad += 1
                print(f'[FAIL] wads: {len(stale)} of {len(results)} do not match the texpacks '
                      f'({sum(r.patched for r in stale)} texture refs); run wadsync')
                for e in errors[:10]:
                    print(f'    {e}')
            else:
                print(f'[OK]   wads: {len(results)} wads, {refs} texture refs match the texpacks')
    return 1 if bad else 0


def _load_manifest(backup: str) -> dict:
    p = os.path.join(backup, MANIFEST)
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {'files': {}}


def _save_manifest(backup: str, m: dict) -> None:
    p = os.path.join(backup, MANIFEST)
    with open(p + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(p + '.tmp', p)


def _move(src: str, dst: str) -> None:
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst + '.tmp')
        os.replace(dst + '.tmp', dst)
        os.remove(src)


def _check_free(directory: str, need: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < need + (1 << 30):
        raise SystemExit(f'not enough free disk space in {directory}: {fmt_size(free)} free, '
                         f'need {fmt_size(need)} + 1 GiB margin')


def cmd_mipdrop(args) -> int:
    policy = policy_from(args)
    if bool(args.out) == bool(args.in_place):
        raise SystemExit('specify exactly one of --out DIR or --in-place')

    manifest = None
    if args.in_place and args.backup:
        os.makedirs(args.backup, exist_ok=True)
        manifest = _load_manifest(args.backup)
    elif args.in_place:
        print('no --backup given: originals will be replaced without a backup')
    else:
        os.makedirs(args.out, exist_ok=True)

    paths = tx.iter_texpacks(args.paths)

    gowr_dirs: dict[str, dict] = {}
    for path in paths:
        if _pack_version(path) == tx.VERSION_GOWR:
            d = os.path.dirname(os.path.abspath(path))
            if d not in gowr_dirs:
                run = {os.path.basename(p) for p in paths
                       if os.path.dirname(os.path.abspath(p)) == d}
                gowr_dirs[d] = gw.outside_chains(d, run)
    if gowr_dirs and not args.in_place:
        print('note: Ragnarok texpacks only work together with matching .wad files; '
              '--out writes texpacks only. Use --in-place to update the wads as well.')
    if gowr_dirs and args.in_place and not args.dry_run:
        for d in gowr_dirs:
            if not _wadsync(d, args.backup, manifest, dry_run=True, quiet=True):
                return 1

    for path in paths:
        name = os.path.basename(path)
        game_dir = os.path.dirname(os.path.abspath(path))
        print(f'{name}')

        src = path
        if args.in_place and args.backup:
            bpack = os.path.join(args.backup, name)
            if os.path.exists(bpack):
                src = bpack
                print(f'  source: using backup ({bpack})')

        errs = tx.verify(src, deep=False)
        if errs:
            print('  [SKIP] source failed verification: ' + '; '.join(errs[:3]))
            continue
        tp = tx.Texpack.open(src)
        plan = tx.make_plan(tp, policy)
        if game_dir in gowr_dirs:
            try:
                n = gw.align_drops(plan, gowr_dirs[game_dir])
            except gw.WadError as e:
                print(f'  [SKIP] {e}')
                continue
            if n:
                print(f'  {n} textures shared with other texpacks follow their current chain')
        new_size = plan.new_file_size()
        print(f'  records {tp.record_count} -> {plan.new_record_count}, '
              f'{fmt_size(tp.file_size)} -> {fmt_size(new_size)}')
        if args.dry_run:
            continue
        if sum(plan.drops) == 0 and src == path:
            print('  nothing to change')
            continue

        out_dir = args.out if args.out else game_dir
        out_pack = os.path.join(out_dir, name)
        out_toc = out_pack + '.toc'
        if args.in_place:
            out_pack += '.gowshrink.tmp'
            out_toc += '.gowshrink.tmp'
        elif os.path.abspath(out_pack) == os.path.abspath(src):
            raise SystemExit('--out points at the source folder; refusing to overwrite the original')
        _check_free(out_dir, new_size)

        tx.write(plan, out_pack, out_toc, progress=Progress('writing'))
        errs = tx.verify(out_pack, out_toc)
        if errs:
            print('  [FAIL] output failed verification; original left untouched:')
            for e in errs[:10]:
                print(f'    {e}')
            if args.in_place:
                os.remove(out_pack)
                os.remove(out_toc)
            return 1
        print('  output verified')

        if args.in_place and not args.backup:
            os.replace(out_pack, path)
            os.replace(out_toc, path + '.toc')
            print('  replaced (no backup)')
        elif args.in_place:
            bpack = os.path.join(args.backup, name)
            btoc = bpack + '.toc'
            if not os.path.exists(bpack):
                _move(path + '.toc', btoc)
                _move(path, bpack)
                manifest['files'][name] = {
                    'target_dir': game_dir,
                    'size': tp.file_size,
                    'toc_size': tp.toc_size,
                }
                _save_manifest(args.backup, manifest)
                print(f'  original moved to backup: {bpack}')
            os.replace(out_pack, path)
            os.replace(out_toc, path + '.toc')
            manifest['files'].setdefault(name, {
                'target_dir': game_dir, 'size': tp.file_size, 'toc_size': tp.toc_size,
            })['policy'] = vars(policy)
            _save_manifest(args.backup, manifest)
            print('  replaced')

    if args.in_place and not args.dry_run:
        for d in gowr_dirs:
            if not _wadsync(d, args.backup, manifest):
                return 1
    return 0


def _pack_version(path: str) -> int | None:
    try:
        return tx.Texpack.open(path).version
    except (OSError, tx.FormatError):
        return None


def _wadsync(wad_dir: str, backup: str | None, manifest: dict | None,
             dry_run: bool = False, quiet: bool = False) -> bool:
    label = 'checking wads' if dry_run else 'updating wads'
    if not quiet or not dry_run:
        print(f'{label} in {wad_dir}')

    def on_backup(name: str, target_dir: str, size: int) -> None:
        manifest['files'][name] = {'target_dir': target_dir, 'size': size, 'kind': 'wad'}
        _save_manifest(backup, manifest)

    def on_restore(name: str) -> None:
        manifest['files'].pop(name, None)
        _save_manifest(backup, manifest)

    if backup and manifest is None:
        os.makedirs(backup, exist_ok=True)
        manifest = _load_manifest(backup)
    try:
        results = gw.sync(wad_dir, backup=backup, dry_run=dry_run,
                          on_backup=on_backup if backup else None,
                          on_restore=on_restore if backup else None,
                          progress=Progress(label), move=_move)
    except (gw.WadError, tx.FormatError, OSError) as e:
        print(f'  [FAIL] {e}')
        return False
    errors = [e for r in results for e in r.errors]
    if errors:
        print(f'  [FAIL] {len(errors)} streaming info blocks could not be matched; nothing written:')
        for e in errors[:10]:
            print(f'    {e}')
        return False
    acts = collections.Counter(r.action for r in results)
    refs = sum(r.refs for r in results)
    patched = sum(r.patched for r in results)
    verb = 'would be' if dry_run else 'were'
    print(f'  {len(results)} wads, {refs} texture refs: {acts["rewrite"]} wads {verb} rewritten '
          f'({patched} refs differ from the original), {acts["restore"]} {verb} restored from backup')
    return True


def _restore_partners(backup: str, m: dict, names: list[str]) -> list[str]:
    def hashes(name):
        try:
            tp = tx.Texpack.open(os.path.join(backup, name))
        except (OSError, tx.FormatError):
            return None
        return {t.hash[8:] for t in tp.textures} if tp.version == tx.VERSION_GOWR else None

    packs = [n for n in m['files'] if n.endswith('.texpack')]
    chosen = {n: hashes(n) for n in packs if n in names}
    chosen = {n: h for n, h in chosen.items() if h}
    if not chosen:
        return []
    out = []
    for n in packs:
        if n in names:
            continue
        h = hashes(n)
        if h and any(h & c for c in chosen.values()):
            out.append(n)
    return sorted(out)


def cmd_wadsync(args) -> int:
    return 0 if _wadsync(args.dir, args.backup, None, dry_run=args.dry_run) else 1


def cmd_restore(args) -> int:
    m = _load_manifest(args.backup)
    if not m['files']:
        print('nothing to restore')
        return 0
    names = args.names or list(m['files'])
    missing = _restore_partners(args.backup, m, names)
    if missing:
        print('[FAIL] these Ragnarok texpacks share textures with the ones being restored and are '
              'still shrunk; restore them together (nothing was restored):')
        for n in missing:
            print(f'    {n}')
        return 1
    gowr_dirs: set[str] = set()
    for name in names:
        info = m['files'].get(name)
        if info is None:
            print(f'[SKIP] {name}: not in manifest')
            continue
        bpack = os.path.join(args.backup, name)
        if info.get('kind') in ('dir', 'path'):
            target = info['target']
            if not os.path.exists(bpack) or os.path.exists(target):
                print(f'[FAIL] {name}: backup missing or target already exists')
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(bpack, target)
            del m['files'][name]
            _save_manifest(args.backup, m)
            print(f'[RESTORED] {name} -> {target}')
            continue
        target = os.path.join(info['target_dir'], name)
        if not os.path.exists(bpack) or os.path.getsize(bpack) != info['size']:
            print(f'[FAIL] {name}: backup file missing or size mismatch')
            continue
        _move(bpack, target)
        if os.path.exists(bpack + '.toc'):
            _move(bpack + '.toc', target + '.toc')
        del m['files'][name]
        _save_manifest(args.backup, m)
        print(f'[RESTORED] {name} -> {target}')
        if name.endswith('.texpack') and _pack_version(target) == tx.VERSION_GOWR:
            gowr_dirs.add(info['target_dir'])

    wads_left = any(v.get('kind') == 'wad' for v in m['files'].values())
    for d in sorted(gowr_dirs):
        if wads_left and not _wadsync(d, args.backup, m):
            return 1
    return 0


def cmd_shvol(args) -> int:
    from . import shvol

    wad_dir = args.dir
    manifest = None
    if args.backup and not args.dry_run:
        os.makedirs(args.backup, exist_ok=True)
        manifest = _load_manifest(args.backup)
    elif not args.backup and not args.dry_run:
        print('no --backup given: originals will be replaced without a backup')
    before = after = files = vols = 0
    for name in sorted(n for n in os.listdir(wad_dir) if n.endswith('.wad')):
        path = os.path.join(wad_dir, name)
        if os.path.getsize(path) == 0:
            continue
        bpath = os.path.join(args.backup, name) if args.backup else None
        src = bpath if bpath and os.path.exists(bpath) else path
        with open(src, 'rb') as f:
            buf = f.read()
        if b'SH0' not in buf:
            continue
        try:
            result = shvol.rewrite_wad(buf, 2)
        except shvol.ShVolError as e:
            print(f'[SKIP] {name}: {e}')
            continue
        if result.volumes == 0:
            continue
        errs = shvol.verify_wad(buf, result.data)
        if errs:
            print(f'[FAIL] {name}: {"; ".join(errs[:3])}; original left untouched')
            return 1
        files += 1
        vols += result.volumes
        before += len(buf)
        after += len(result.data)
        print(f'{name}: {result.volumes} volumes, {fmt_size(len(buf))} -> {fmt_size(len(result.data))}')
        if args.dry_run:
            continue
        tmp = path + '.gowshrink.tmp'
        with open(tmp, 'wb') as f:
            f.write(result.data)
        if bpath and src == path:
            _move(path, bpath)
            manifest['files'][name] = {'target_dir': os.path.abspath(wad_dir), 'size': len(buf)}
            _save_manifest(args.backup, manifest)
        os.replace(tmp, path)
    print()
    print(f'wads: {files}, volumes: {vols}, total {fmt_size(before)} -> {fmt_size(after)} '
          f'(saved {fmt_size(before - after)})')
    return 0


def cmd_lang(args) -> int:
    lang = lang_mod
    cfg = GAME_INFO[args.game].lang
    exec_dir = args.exec_dir
    installed = lang.installed_languages(exec_dir, cfg)
    if not installed:
        raise SystemExit(f'no voice language files under {exec_dir} (expected the game folder or its exec folder)')
    table = lang.read_language_table(exec_dir, cfg)

    keep = set()
    for want in args.keep or []:
        matches = [n for n in installed if n.lower() == want.lower()] or \
                  [n for n in installed if n.lower().startswith(want.lower())]
        if len(matches) == 1:
            keep.add(matches[0])
        elif matches:
            raise SystemExit(f'{want!r} is ambiguous: {", ".join(matches)}')
        else:
            keep.add(want)
    for code in args.keep_for or []:
        try:
            keep.add(lang.voice_folder_for(table, code, cfg))
        except lang.LangError as e:
            raise SystemExit(str(e))

    if not keep:
        print(f'{"voice language":24} {"size":>12}  items')
        for name, items in installed.items():
            print(f'{name:24} {fmt_size(sum(s for _, s in items))}  {len(items)}')
        if table:
            print()
            print('text language -> voice language:')
            for t in table:
                try:
                    v = lang.voice_folder_for(table, t.code, cfg)
                except lang.LangError:
                    v = '?'
                print(f'  {t.code:8} {t.name:14} -> {v}')
        print()
        print('choose what to keep with --keep <voice language> or --keep-for <text language code>')
        return 0

    unknown = keep - set(installed)
    if unknown:
        raise SystemExit(f'not installed: {", ".join(sorted(unknown))}; installed: {", ".join(installed)}')
    remove = [n for n in installed if n not in keep]
    if not remove:
        print('nothing to remove')
        return 0
    freed = sum(s for n in remove for _, s in installed[n])
    print(f'keep: {", ".join(sorted(keep))}')
    print(f'remove: {", ".join(remove)} ({fmt_size(freed)})')
    if args.dry_run:
        return 0

    manifest = None
    if args.backup:
        os.makedirs(args.backup, exist_ok=True)
        manifest = _load_manifest(args.backup)
    else:
        print('no --backup given: language files and folders will be deleted')
    for name in remove:
        for path, size in installed[name]:
            rel = os.path.relpath(path, exec_dir)
            if args.backup:
                dst = os.path.join(args.backup, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(path, dst)
                manifest['files'][rel] = {'kind': 'dir' if os.path.isdir(dst) else 'path',
                                          'target': os.path.abspath(path), 'size': size}
                _save_manifest(args.backup, manifest)
                print(f'  moved   {rel} ({fmt_size(size)})')
            else:
                lang.remove_path(path)
                print(f'  deleted {rel} ({fmt_size(size)})')
    print(f'freed {fmt_size(freed)}' + (' (after deleting the backup folder)' if args.backup else ''))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tool", description='Install size reduction tool for God of War (2018) and God of War Ragnarok on PC')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan', help='report estimated sizes after removal')
    p.add_argument('paths', nargs='+', help='game folder, .texpack files or folders')
    add_policy_args(p)
    add_game_arg(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser('mipdrop', aliases=['shrink'],
                       help='remove top mip levels of streamed textures and rewrite the texpacks')
    p.add_argument('paths', nargs='+', help='game folder, .texpack files or folders')
    add_policy_args(p)
    p.add_argument('--out', metavar='DIR', help='write results to this folder (originals untouched)')
    p.add_argument('--in-place', action='store_true',
                   help='replace files in the game folder; originals are moved to --backup')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder for originals (for --in-place); without it originals are replaced')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    add_game_arg(p)
    p.set_defaults(func=cmd_mipdrop)

    p = sub.add_parser('verify', help='check texpack/.toc integrity (and Ragnarok wad streaming info)')
    p.add_argument('paths', nargs='+', help='game folder, .texpack files or folders')
    p.add_argument('--quick', action='store_true', help='skip block header and wad checks')
    add_game_arg(p)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('wadsync', help='(gowr) update .wad texture streaming info to match the texpacks')
    p.add_argument('dir', help='game folder or the folder with .wad and .texpack files (exec\\wad\\pc_le)')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder: original wads are moved here and used as the source')
    p.add_argument('--dry-run', action='store_true', help='report what would change without writing')
    add_game_arg(p, ('gowr',))
    p.set_defaults(func=cmd_wadsync)

    p = sub.add_parser('shvol', help='(gow) halve the resolution of SH lighting volumes in .wad files')
    p.add_argument('dir', help='game folder or the folder with .wad files (exec\\wad\\pc_le)')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder for original .wad files; without it originals are replaced')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    add_game_arg(p, ('gow',))
    p.set_defaults(func=cmd_shvol)

    p = sub.add_parser('lang', help='list voice languages, or remove all but the ones you keep')
    p.add_argument('exec_dir', help="game folder or its exec folder")
    p.add_argument('--keep', action='append', metavar='LANG',
                   help='voice language to keep, e.g. "english(us)" (repeatable)')
    p.add_argument('--keep-for', action='append', metavar='CODE',
                   help='keep the voice used by this text language code, e.g. ko -> english(us) (repeatable)')
    p.add_argument('--backup', metavar='DIR', help='move removed files and folders here instead of deleting them')
    p.add_argument('--dry-run', action='store_true', help='print the plan without changing anything')
    add_game_arg(p, games_with('lang'))
    p.set_defaults(func=cmd_lang)

    p = sub.add_parser('restore', help='restore originals from the backup')
    p.add_argument('--backup', required=True, metavar='DIR')
    p.add_argument('names', nargs='*', help='file names to restore (default: all)')
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args(argv)
    resolve_dirs(args)
    if hasattr(args, 'games'):
        args.game = resolve_game(args)
    return args.func(args)
