from __future__ import annotations

import argparse
import collections
import functools
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass

from . import gowr_wad as gw
from .lang import gow as lang_mod
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
    tex_version: int | None = None


GAMES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'games.json')


def load_games(path: str = GAMES_FILE) -> dict[str, GameInfo]:
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)
    return {g: GameInfo(v['title'], v['exe'], v.get('texpack_version'),
                        {role: tuple(rel.replace('\\', '/').split('/')) for role, rel in v.get('dirs', {}).items()},
                        lang_mod.LangConfig.from_json(v['lang']) if 'lang' in v else None,
                        v.get('tex_version'))
            for g, v in raw.items()}


def games_with(attr: str) -> tuple[str, ...]:
    return tuple(g for g, i in GAME_INFO.items() if getattr(i, attr) is not None)


GAME_INFO = load_games()
GAMES = {g: i.title for g, i in GAME_INFO.items()}
GAME_OF_VERSION = {i.texpack_version: g for g, i in GAME_INFO.items() if i.texpack_version is not None}
GAME_EXE = {g: i.exe for g, i in GAME_INFO.items()}
ARG_DIR_ROLES = (('paths', 'wad'), ('dir', 'wad'), ('exec_dir', 'exec'))
KPKA_GAMES = games_with('tex_version')
LANG_MODULE_GAMES = KPKA_GAMES
REPACK_GAMES = ('ah',)
TEXTURE_GAMES = tuple(g for g in GAME_INFO if g not in REPACK_GAMES)


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
    if texture_cmd and not detected:
        for p in args.paths:
            g = _game_of_dir(p)
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
    def __init__(self, label: str, rate: bool = True):
        self.label = label
        self.rate = rate
        self.t0 = time.time()
        self.last = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.time()
        if now - self.last < 0.5 and done < total:
            return
        self.last = now
        pct = 100 * done / total if total else 100
        rate = done / max(now - self.t0, 1e-6) / (1 << 20)
        tail = f'  {rate:6.0f} MiB/s' if self.rate else ''
        sys.stderr.write(f'\r  {self.label}: {pct:5.1f}%{tail}')
        if done >= total:
            sys.stderr.write('\n')
        sys.stderr.flush()




def _shf_plans(args, policy, use_backup: bool = False):
    from .mipdrop import shf
    from .package import iostore
    utocs = shf.find_utocs(args.paths)
    if not utocs:
        raise SystemExit('no .utoc/.ucas containers found in ' + ', '.join(args.paths))
    for utoc in utocs:
        name = os.path.basename(utoc)
        src_toc, src_cas = utoc, shf.ucas_of(utoc)
        if use_backup and args.backup:
            btoc = os.path.join(args.backup, name)
            bcas = shf.ucas_of(btoc)
            if os.path.exists(btoc) and os.path.exists(bcas):
                src_toc, src_cas = btoc, bcas
        try:
            plan = shf.make_plan(src_toc, policy, src_cas)
        except (iostore.IoStoreError, OSError) as e:
            print(f'{name}\n  [SKIP] {e}')
            continue
        yield utoc, plan


def _shf_report(plan) -> tuple[int, int]:
    before = plan.ucas_size()
    after = before - plan.saved()
    skipped = sum(plan.skipped.values())
    print(f'  textures {len(plan.textures)}, shrunk {len(plan.changed)}, '
          f'packages skipped {skipped}')
    for reason, n in plan.skipped.most_common():
        print(f'    {n:6}  {reason}')
    print(f'  .ucas {fmt_size(before)} -> {fmt_size(after)} (saved {fmt_size(before - after)})')
    return before, after


def _shf_resolutions(plans) -> None:
    res_before = collections.Counter()
    res_after = collections.Counter()
    for plan in plans:
        for t in plan.textures:
            res_before[t.tex.mips[0].max_dim] += 1
            res_after[t.kept_top.max_dim] += 1
    print()
    print('textures by top resolution (max(width, height))')
    for r in sorted(set(res_before) | set(res_after)):
        print(f'  {r:5}: {res_before[r]:7} -> {res_after[r]:7}')


def _scan_shf(args) -> int:
    policy = policy_from(args)
    plans = []
    tot_before = tot_after = 0
    for utoc, plan in _shf_plans(args, policy):
        print(os.path.basename(utoc))
        b, a = _shf_report(plan)
        tot_before += b
        tot_after += a
        plans.append(plan)
    print(f'total {fmt_size(tot_before)} -> {fmt_size(tot_after)} (saved {fmt_size(tot_before - tot_after)})')
    _shf_resolutions(plans)
    return 0


def _verify_shf(args) -> int:
    from .mipdrop import shf
    bad = 0
    for utoc in shf.find_utocs(args.paths):
        name = os.path.basename(utoc)
        errs = shf.verify(utoc, deep=not args.quick, progress=None if args.quick else Progress(f'hashing {name}'))
        if errs:
            bad += 1
            print(f'[FAIL] {name}')
            for e in errs[:20]:
                print(f'    {e}')
        else:
            print(f'[OK]   {name}')
    return 1 if bad else 0


def _mipdrop_shf(args) -> int:
    from .mipdrop import shf
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

    plans = []
    for utoc, plan in _shf_plans(args, policy, use_backup=args.in_place):
        name = os.path.basename(utoc)
        cas_name = os.path.basename(shf.ucas_of(utoc))
        game_dir = os.path.dirname(utoc)
        print(name)
        if plan.utoc != utoc:
            print(f'  source: using backup ({plan.utoc})')
        before, after = _shf_report(plan)
        plans.append(plan)
        if args.dry_run:
            continue
        if not plan.changed and plan.utoc == utoc:
            print('  nothing to change')
            continue

        out_dir = args.out if args.out else game_dir
        out_toc = os.path.join(out_dir, name)
        out_cas = os.path.join(out_dir, cas_name)
        if args.in_place:
            out_toc += '.gowshrink.tmp'
            out_cas += '.gowshrink.tmp'
        elif os.path.abspath(out_toc) in (os.path.abspath(plan.utoc), os.path.abspath(utoc)):
            raise SystemExit('--out points at the source folder; refusing to overwrite the original')
        _check_free(out_dir, after)

        try:
            shf.write(plan, out_toc, out_cas, hash_progress=Progress('hashing'),
                      write_progress=Progress('writing'))
            errs = shf.verify(out_toc, out_cas, chunks=[t.package for t in plan.changed],
                               progress=Progress('checking'))
        except Exception as e:
            errs = [f'{type(e).__name__}: {e}']
        if errs:
            print('  [FAIL] output failed verification; original left untouched:')
            for e in errs[:10]:
                print(f'    {e}')
            if args.in_place:
                for p in (out_toc, out_cas):
                    if os.path.exists(p):
                        os.remove(p)
            return 1
        print(f'  output verified ({fmt_size(os.path.getsize(out_cas))})')

        if args.in_place and not args.backup:
            os.replace(out_cas, shf.ucas_of(utoc))
            os.replace(out_toc, utoc)
            print('  replaced (no backup)')
        elif args.in_place:
            for n, path in ((name, utoc), (cas_name, shf.ucas_of(utoc))):
                bpath = os.path.join(args.backup, n)
                if not os.path.exists(bpath):
                    size = os.path.getsize(path)
                    _move(path, bpath)
                    manifest['files'][n] = {'target_dir': game_dir, 'size': size}
                    _save_manifest(args.backup, manifest)
                    print(f'  original moved to backup: {bpath}')
            os.replace(out_cas, shf.ucas_of(utoc))
            os.replace(out_toc, utoc)
            manifest['files'].setdefault(name, {'target_dir': game_dir,
                                                'size': os.path.getsize(os.path.join(args.backup, name))}
                                         )['policy'] = vars(policy)
            _save_manifest(args.backup, manifest)
            print('  replaced')
    _shf_resolutions(plans)
    return 0


def _re8_targets(paths: list[str], game: str) -> tuple[list[str], list[str]]:
    from .package import kpka
    targets = kpka.find_paks(paths)
    if not targets:
        raise SystemExit('no .pak files found in ' + ', '.join(paths))
    root = kpka.find_root(paths[0], GAME_EXE[game])
    context = kpka.root_paks(root) if root else targets
    return targets, context


def _re8_sources(backup: str | None, paks: list[str]) -> dict[str, str]:
    out = {}
    if backup:
        for p in paks:
            b = os.path.join(backup, os.path.basename(p))
            if os.path.exists(b):
                out[p] = b
    return out


def _re8_rewrite(path: str, source: str, before: int, after: int, args, manifest: dict | None,
                 write, verify, record: dict) -> bool:
    name = os.path.basename(path)
    game_dir = os.path.dirname(path)
    out_dir = getattr(args, 'out', None)
    in_place = not out_dir
    out = os.path.join(out_dir or game_dir, name)
    if in_place:
        out += '.gowshrink.tmp'
    elif os.path.abspath(out) in (os.path.abspath(source), os.path.abspath(path)):
        raise SystemExit('--out points at the source folder; refusing to overwrite the original')
    _check_free(out_dir or game_dir, after)
    try:
        crcs = write(out)
        errs = verify(out, crcs)
    except Exception as e:
        errs = [f'{type(e).__name__}: {e}']
    if errs:
        print('  [FAIL] output failed verification; original left untouched:')
        for e in errs[:10]:
            print(f'    {e}')
        if in_place and os.path.exists(out):
            os.remove(out)
        return False
    print(f'  output verified ({fmt_size(os.path.getsize(out))})')
    if not in_place:
        return True
    if not args.backup:
        os.replace(out, path)
        print('  replaced (no backup)')
        return True
    bpath = os.path.join(args.backup, name)
    if not os.path.exists(bpath):
        _move(path, bpath)
        manifest['files'][name] = {'target_dir': game_dir, 'size': before}
        _save_manifest(args.backup, manifest)
        print(f'  original moved to backup: {bpath}')
    os.replace(out, path)
    manifest['files'].setdefault(name, {'target_dir': game_dir, 'size': before}).update(record)
    _save_manifest(args.backup, manifest)
    print('  replaced')
    return True


def _re8_plan(args, policy, use_backup: bool):
    from .mipdrop import re8
    from .package import kpka
    targets, context = _re8_targets(args.paths, args.game)
    sources = _re8_sources(args.backup, context) if use_backup else {}
    try:
        return re8.make_plan(targets, context, policy, GAME_INFO[args.game].tex_version, sources,
                             progress=Progress('reading materials', rate=False))
    except (kpka.PakError, OSError) as e:
        raise SystemExit(f'[FAIL] {e}')


def _re8_report(pp) -> tuple[int, int]:
    before = pp.pak.file_size
    after = pp.estimate()
    print(f'  streamed textures {len(pp.textures)}, shrunk {len(pp.drops)}, '
          f'skipped {sum(pp.skipped.values())}')
    for reason, n in pp.skipped.most_common():
        print(f'    {n:6}  {reason}')
    print(f'  {fmt_size(before)} -> about {fmt_size(after)} (saved about {fmt_size(before - after)})')
    return before, after


def _re8_resolutions(plan) -> None:
    res_before = collections.Counter()
    res_after = collections.Counter()
    for pp in plan.paks:
        for t in pp.textures:
            res_before[t.tex.max_dim()] += 1
            res_after[t.kept_dim if t.blob in pp.drops else t.tex.max_dim()] += 1
    print()
    print('streamed textures by top resolution (max(width, height))')
    for r in sorted(set(res_before) | set(res_after)):
        print(f'  {r:5}: {res_before[r]:7} -> {res_after[r]:7}')


def _re8_header(plan) -> None:
    print(f'texture paths found in materials: {plan.names}, streamed textures with a base copy: {plan.streams}')


def _scan_re8(args) -> int:
    plan = _re8_plan(args, policy_from(args), use_backup=False)
    _re8_header(plan)
    tot_before = tot_after = 0
    for pp in plan.paks:
        if not pp.textures:
            continue
        print(os.path.basename(pp.path))
        b, a = _re8_report(pp)
        tot_before += b
        tot_after += a
    print(f'total {fmt_size(tot_before)} -> about {fmt_size(tot_after)} '
          f'(saved about {fmt_size(tot_before - tot_after)})')
    _re8_resolutions(plan)
    return 0


def check_re8_resource(version: int, data: bytes) -> str | None:
    from .lang import re8 as lang_re8
    from .mipdrop import re8
    return re8.check_tex(version, data) or lang_re8.check_sound(data)


def _verify_re8(args) -> int:
    from .package import kpka
    targets, _ = _re8_targets(args.paths, args.game)
    check = functools.partial(check_re8_resource, GAME_INFO[args.game].tex_version)
    bad = 0
    for path in targets:
        name = os.path.basename(path)
        errs = kpka.verify(path, deep=not args.quick, check=check,
                           progress=None if args.quick else Progress(f'checking {name}', rate=False))
        if errs:
            bad += 1
            print(f'[FAIL] {name}')
            for e in errs[:20]:
                print(f'    {e}')
        else:
            print(f'[OK]   {name}')
    return 1 if bad else 0


def _backup_manifest(args, in_place: bool = True) -> dict | None:
    if in_place and args.backup:
        os.makedirs(args.backup, exist_ok=True)
        return _load_manifest(args.backup)
    if in_place:
        print('no --backup given: originals will be replaced without a backup')
    return None


def _mipdrop_re8(args) -> int:
    from .mipdrop import re8
    policy = policy_from(args)
    if bool(args.out) == bool(args.in_place):
        raise SystemExit('specify exactly one of --out DIR or --in-place')
    manifest = _backup_manifest(args, args.in_place)
    if args.out:
        os.makedirs(args.out, exist_ok=True)

    plan = _re8_plan(args, policy, use_backup=args.in_place)
    _re8_header(plan)
    for pp in plan.paks:
        if not pp.textures and pp.source == pp.path:
            continue
        print(os.path.basename(pp.path))
        if pp.source != pp.path:
            print(f'  source: using backup ({pp.source})')
        before, after = _re8_report(pp)
        if args.dry_run:
            continue
        if not pp.drops and pp.source == pp.path:
            print('  nothing to change')
            continue
        keys = {t.key for t in pp.changed}
        ok = _re8_rewrite(
            pp.path, pp.source, before, after, args, manifest,
            write=lambda out, pp=pp: re8.write(pp, out, progress=Progress('writing')),
            verify=lambda out, crcs, keys=keys, v=pp.version: re8.verify(
                out, v, crcs, deep=False, deep_keys=keys, progress=Progress('checking', rate=False)),
            record={'policy': vars(policy)})
        if not ok:
            return 1
    _re8_resolutions(plan)
    return 0


def cmd_scan(args) -> int:
    if args.game in KPKA_GAMES:
        return _scan_re8(args)
    if args.game == 'shf':
        return _scan_shf(args)
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
    if args.game in KPKA_GAMES:
        return _verify_re8(args)
    if args.game == 'shf':
        return _verify_shf(args)
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


def _mipdrop_ah(args) -> int:
    from .mipdrop import ue4
    from .package import uepak
    policy = policy_from(args)
    if bool(args.out) == bool(args.in_place):
        raise SystemExit('specify exactly one of --out DIR or --in-place')
    paks = uepak.find_paks(args.paths)
    if not paks:
        raise SystemExit('no .pak files found in ' + ', '.join(args.paths))
    sources = _re8_sources(args.backup, paks) if args.in_place else {}
    folders = sorted({os.path.dirname(os.path.abspath(p)) for p in paks})
    every = uepak.find_paks(folders)
    try:
        ui = ue4.ui_textures(every, Progress('finding textures used by the UI', rate=False))
    except (uepak.PakError, OSError) as e:
        raise SystemExit(f'[FAIL] {e}')
    print(f'{len(ui)} packages are referenced by widgets, Slate assets or UI materials')
    plans = []
    by_res: collections.Counter = collections.Counter()
    for path in paks:
        source = sources.get(path, path)
        try:
            plan = ue4.plan_pak(path, source, policy, Progress(f'reading {os.path.basename(path)}', rate=False),
                                ui=ui)
        except (uepak.PakError, ue4.PackageError, OSError) as e:
            raise SystemExit(f'[FAIL] {os.path.basename(path)}: {e}')
        if not plan.textures and source == path:
            continue
        note = ' (source: backup)' if source != path else ''
        print(f'{os.path.basename(path)}: {len(plan.textures)} textures, '
              f'removes {fmt_size(plan.removed)} of texture data{note}')
        if plan.ui:
            print(f'  {plan.ui} UI textures left as they are')
        for reason, n in sorted(plan.skipped.items(), key=lambda x: -x[1]):
            print(f'  [SKIP] {n} textures: {reason}')
        for t in plan.textures:
            by_res[(t.max_dim, t.max_dim >> t.drop)] += 1
        plans.append(plan)
    total = sum(p.removed for p in plans)
    print(f'total: {sum(len(p.textures) for p in plans)} textures, removes {fmt_size(total)} '
          f'of texture data (before pak compression)')
    for (a, b), n in sorted(by_res.items(), reverse=True):
        print(f'  {a:5d} -> {b:5d}: {n} textures')
    if args.dry_run or not plans:
        return 0
    manifest = _backup_manifest(args, args.in_place)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
    for plan in plans:
        path, source = plan.path, plan.source
        print(os.path.basename(path))
        before = os.path.getsize(path)
        pak = plan.pak
        method = uepak.pak_method(pak)
        if source != path:
            current = uepak.Pak.open(path)
            method = uepak.pak_method(current)
            gone = {p for p, _ in pak.files()} - {p for p, _ in current.files()}
            if gone:
                print(f'  {len(gone)} files that are no longer in the game pak are left out')
                pak = uepak.without(pak, gone)

        def write(out, plan=plan, pak=pak, method=method):
            r = uepak.write(pak, plan.source, out, method, changes=plan.changes,
                            progress=Progress('writing'))
            return r.expect

        def verify(out, expect, pak=pak):
            return uepak.verify(out, pak, expect, progress=Progress('checking', rate=False))

        ok = _re8_rewrite(path, source, before, max(before, plan.pak.file_size), args, manifest,
                          write=write, verify=verify, record={'policy': vars(policy)})
        if not ok:
            return 1
    return 0


def cmd_mipdrop(args) -> int:
    if args.game == 'ah':
        return _mipdrop_ah(args)
    if args.game in KPKA_GAMES:
        return _mipdrop_re8(args)
    if args.game == 'shf':
        return _mipdrop_shf(args)
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


def cmd_prune(args) -> int:
    from .package import kpka
    from .prune import re8 as prune_re8
    _, context = _re8_targets([args.dir], args.game)
    chain = prune_re8.main_chain(context)
    try:
        plans = prune_re8.plan(chain, _re8_sources(args.backup, chain))
    except (kpka.PakError, OSError) as e:
        raise SystemExit(f'[FAIL] {e}')
    errs = prune_re8.check_winners(plans, Progress('checking the replacing entries', rate=False))
    if errs:
        print('[FAIL] some replacing entries are not usable; nothing written:')
        for e in errs[:10]:
            print(f'    {e}')
        return 1
    dlc_plans = []
    if args.dlc:
        dlc = [p for p in context if prune_re8.is_dlc(p)]
        try:
            dlc_plans = prune_re8.dlc_plan(chain, dlc, _re8_sources(args.backup, dlc),
                                           progress=Progress('comparing DLC entries with the main paks'))
        except (kpka.PakError, OSError) as e:
            raise SystemExit(f'[FAIL] {e}')
    from_dlc = {id(d) for d in dlc_plans}
    total = 0
    for d in plans + dlc_plans:
        if not d.remove:
            continue
        before = d.pak.file_size
        note = ' (no entries left; the file is kept as an empty pak)' if d.emptied else ''
        what = 'copies of main pak entries' if id(d) in from_dlc else 'replaced entries'
        print(f'{os.path.basename(d.path)}: {len(d.remove)} {what}, '
              f'{fmt_size(before)} -> {fmt_size(before - d.saved())}{note}')
        total += d.saved()
    plans = plans + dlc_plans
    print(f'total saved {fmt_size(total)}')
    if args.dry_run:
        return 0
    if not total and all(d.source == d.path for d in plans):
        print('nothing to remove')
        return 0
    manifest = _backup_manifest(args)
    for d in plans:
        if not d.remove and d.source == d.path:
            continue
        print(os.path.basename(d.path))
        if d.source != d.path:
            print(f'  source: using backup ({d.source})')
        before = d.pak.file_size
        ok = _re8_rewrite(
            d.path, d.source, before, before - d.saved(), args, manifest,
            write=lambda out, d=d: prune_re8.write(d, out, progress=Progress('writing')),
            verify=lambda out, crcs: prune_re8.verify(out, crcs, progress=Progress('checking', rate=False)),
            record={'prune': 'dlc' if id(d) in from_dlc else True})
        if not ok:
            return 1
    return 0


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


def _match_keep(wants: list[str], installed) -> set[str]:
    keep = set()
    for want in wants:
        matches = [n for n in installed if n.lower() == want.lower()] or \
                  [n for n in installed if n.lower().startswith(want.lower())]
        if len(matches) == 1:
            keep.add(matches[0])
        elif matches:
            raise SystemExit(f'{want!r} is ambiguous: {", ".join(matches)}')
        else:
            keep.add(want)
    return keep


def _lang_re8(args) -> int:
    from .lang import re8 as lang_re8
    from .package import kpka
    if args.keep_for:
        raise SystemExit(f'--keep-for is not supported for {GAMES[args.game]}; use --keep <voice language>')
    targets, _ = _re8_targets([args.exec_dir], args.game)
    try:
        scans = lang_re8.scan(targets, _re8_sources(args.backup, targets))
    except (kpka.PakError, OSError) as e:
        raise SystemExit(f'[FAIL] {e}')
    installed = lang_re8.installed(scans)
    if not installed:
        raise SystemExit(f'no voice language data found in the .pak files under {args.exec_dir}')
    keep = _match_keep(args.keep or [], installed)

    if not keep:
        print(f'{"voice language":24} {"size":>12}  {"packs":>6} {"banks":>6}')
        for name, (packs, banks, size) in installed.items():
            print(f'{name:24} {fmt_size(size)}  {packs:6} {banks:6}')
        print()
        print('choose what to keep with --keep <voice language>')
        return 0

    unknown = keep - set(installed)
    if unknown:
        raise SystemExit(f'not installed: {", ".join(sorted(unknown))}; installed: {", ".join(installed)}')
    lang_re8.plan_removal(scans, keep)
    remove = [n for n in installed if n not in keep]
    freed = sum(ps.saved() for ps in scans)
    print(f'keep: {", ".join(sorted(keep))}')
    print(f'remove: {", ".join(remove) or "-"} (about {fmt_size(freed)})')
    for ps in scans:
        for reason, n in ps.skipped.most_common():
            print(f'  {os.path.basename(ps.path)}: skipped {n} ({reason})')
    if args.dry_run:
        return 0
    if not freed and all(ps.source == ps.path for ps in scans):
        print('nothing to remove')
        return 0

    manifest = _backup_manifest(args)
    for ps in scans:
        if not ps.remove and ps.source == ps.path:
            continue
        print(os.path.basename(ps.path))
        if ps.source != ps.path:
            print(f'  source: using backup ({ps.source})')
        before = ps.pak.file_size
        after = before - ps.saved()
        print(f'  {len(ps.remove)} voice packs and banks emptied, {fmt_size(before)} -> about {fmt_size(after)}')
        keys = {k for s in ps.remove for k in s.keys}
        ok = _re8_rewrite(
            ps.path, ps.source, before, after, args, manifest,
            write=lambda out, ps=ps: lang_re8.write(ps, out, progress=Progress('writing')),
            verify=lambda out, crcs, keys=keys: lang_re8.verify(out, crcs, deep=False, deep_keys=keys,
                                                                progress=Progress('checking', rate=False)),
            record={'keep': sorted(keep)})
        if not ok:
            return 1
    print(f'freed about {fmt_size(freed)}' + (' (after deleting the backup folder)' if args.backup else ''))
    return 0


def _lang_ah(args) -> int:
    from .lang import ah as lang_ah
    from .package import uepak
    if args.keep_for:
        raise SystemExit(f'--keep-for is not supported for {GAMES[args.game]}; use --keep <voice language>')
    paks = uepak.find_paks([args.exec_dir])
    if not paks:
        raise SystemExit(f'no .pak files found in {args.exec_dir}')
    try:
        scans = lang_ah.scan(paks)
    except (uepak.PakError, OSError) as e:
        raise SystemExit(f'[FAIL] {e}')
    installed = lang_ah.installed(scans)
    if not installed:
        raise SystemExit(f'no voice language data found in the .pak files under {args.exec_dir}')
    keep = _match_keep([lang_ah.resolve(k) for k in args.keep or []], installed)

    if not keep:
        print(f'{"voice language":24} {"voice":>12} {"files":>6}  {"lip-sync":>12} {"files":>6}')
        for name, g in installed.items():
            lip = f'{fmt_size(g.lipsync_size)} {g.lipsync_files:6}' if g.lipsync_files else f'{"-":>12} {"":6}'
            print(f'{name:24} {fmt_size(g.size)} {g.files:6}  {lip}')
        print()
        print('choose what to keep with --keep <voice language>')
        return 0

    unknown = keep - set(installed)
    if unknown:
        raise SystemExit(f'not installed: {", ".join(sorted(unknown))}; installed: {", ".join(installed)}')
    lang_ah.plan_removal(scans, keep)
    remove = [n for n in installed if n not in keep]
    freed = sum(ps.saved() for ps in scans)
    print(f'keep: {", ".join(sorted(keep))}')
    lip = sorted({lang_ah.LIPSYNC[n] for n in remove if n in lang_ah.LIPSYNC and installed[n].lipsync_files})
    print(f'remove: {", ".join(remove) or "-"} (about {fmt_size(freed)} before pak compression)')
    if lip:
        print(f'  with their lip-sync animations: L10N/{", L10N/".join(lip)}')
    if args.dry_run:
        return 0
    if not freed:
        print('nothing to remove')
        return 0
    manifest = _backup_manifest(args)
    for ps in scans:
        if not ps.remove:
            continue
        print(os.path.basename(ps.path))
        print(f'  {len(ps.remove)} files removed ({fmt_size(ps.saved())} before pak compression)')
        before = os.path.getsize(ps.path)
        pak = uepak.without(ps.pak, ps.remove)

        def write(out, pak=pak, ps=ps):
            return uepak.write(pak, ps.path, out, None, progress=Progress('writing')).expect

        def verify(out, expect, pak=pak):
            return uepak.verify(out, pak, expect, progress=Progress('checking', rate=False))

        ok = _re8_rewrite(ps.path, ps.path, before, before, args, manifest, write=write, verify=verify,
                          record={'keep': sorted(keep)})
        if not ok:
            return 1
    print(f'freed about {fmt_size(freed)}' + (' (after deleting the backup folder)' if args.backup else ''))
    return 0


def cmd_lang(args) -> int:
    if args.game == 'ah':
        return _lang_ah(args)
    if args.game in KPKA_GAMES:
        return _lang_re8(args)
    lang = lang_mod
    cfg = GAME_INFO[args.game].lang
    exec_dir = args.exec_dir
    installed = lang.installed_languages(exec_dir, cfg)
    if not installed:
        raise SystemExit(f'no voice language files under {exec_dir} (expected the game folder or its exec folder)')
    table = lang.read_language_table(exec_dir, cfg)

    keep = _match_keep(args.keep or [], installed)
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


def cmd_repack(args) -> int:
    from .package import uepak
    paks = uepak.find_paks(args.paths)
    if not paks:
        raise SystemExit('no .pak files found in ' + ', '.join(args.paths))
    plans = []
    total_before = total_after = 0
    for path in paks:
        name = os.path.basename(path)
        source = path
        try:
            pak = uepak.Pak.open(source)
        except (uepak.PakError, OSError) as e:
            print(f'[SKIP] {name}: {e}')
            continue
        if uepak.compressed_count(pak):
            print(f'[SKIP] {name}: already compressed')
            continue
        before = os.path.getsize(path)
        after = uepak.estimate(pak, source, args.method, args.level)
        print(f'{name}: {len(pak.entries)} entries, {fmt_size(before)} -> about {fmt_size(after)}')
        total_before += before
        total_after += after
        plans.append((path, source, pak, before, after))
    print(f'total {fmt_size(total_before)} -> about {fmt_size(total_after)} '
          f'(saves about {fmt_size(total_before - total_after)}, estimated from samples)')
    if args.dry_run or not plans:
        return 0
    manifest = _backup_manifest(args)
    for path, source, pak, before, after in plans:
        print(os.path.basename(path))
        result = {}

        def write(out, pak=pak, source=source):
            result['r'] = uepak.write(pak, source, out, args.method, args.level, workers=args.workers,
                                      progress=Progress('compressing'))
            return result['r'].expect

        def verify(out, expect, pak=pak):
            return uepak.verify(out, pak, expect, workers=args.workers,
                                progress=Progress('checking', rate=False))

        ok = _re8_rewrite(path, source, before, before, args, manifest, write=write, verify=verify,
                          record={'repack': args.method, 'level': args.level})
        if not ok:
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tool", description='Install size reduction tool for Atomic Heart, God of War (2018), God of War Ragnarok, '
                                             'SILENT HILL f, Resident Evil Village and Resident Evil 7 on PC')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan', help='report estimated sizes after removal')
    p.add_argument('paths', nargs='+', help='game folder, .texpack/.utoc/.pak files or folders')
    add_policy_args(p)
    add_game_arg(p, TEXTURE_GAMES)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser('mipdrop', aliases=['shrink'],
                       help='remove top mip levels of streamed textures and rewrite the texpacks')
    p.add_argument('paths', nargs='+', help='game folder, .texpack/.utoc/.pak files or folders')
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
    p.add_argument('paths', nargs='+', help='game folder, .texpack/.utoc/.pak files or folders')
    p.add_argument('--quick', action='store_true', help='skip block header and wad checks')
    add_game_arg(p, TEXTURE_GAMES)
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
    p.add_argument('--backup', metavar='DIR',
                   help='move removed files and folders (re7/re8: the original .pak files) here instead of deleting them')
    p.add_argument('--dry-run', action='store_true', help='print the plan without changing anything')
    add_game_arg(p, games_with('lang') + LANG_MODULE_GAMES + REPACK_GAMES)
    p.set_defaults(func=cmd_lang)

    p = sub.add_parser('prune', help='(re7/re8) remove pak entries that a later patch pak replaces')
    p.add_argument('dir', help='game folder')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder for the original .pak files; without it originals are replaced')
    p.add_argument('--dlc', action='store_true',
                   help='also remove entries of the dlc paks that are byte-identical to the main paks')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    add_game_arg(p, KPKA_GAMES)
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser('repack', help='(ah) compress the entries of uncompressed Unreal Engine 4 .pak files')
    p.add_argument('paths', nargs='+', help='game folder, .pak files or folders')
    p.add_argument('--method', choices=['zlib', 'lz4'], default='zlib',
                   help='compression method (default: zlib)')
    p.add_argument('--level', type=int, default=6, metavar='N',
                   help='compression level (zlib 1-9, lz4 1-12; default: 6)')
    p.add_argument('--workers', type=int, metavar='N', help='worker processes (default: CPU count - 1)')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder for the original .pak files; without it originals are replaced')
    p.add_argument('--dry-run', action='store_true', help='print the estimate without writing anything')
    add_game_arg(p, REPACK_GAMES)
    p.set_defaults(func=cmd_repack)

    p = sub.add_parser('restore', help='restore originals from the backup')
    p.add_argument('--backup', required=True, metavar='DIR')
    p.add_argument('names', nargs='*', help='file names to restore (default: all)')
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args(argv)
    resolve_dirs(args)
    if hasattr(args, 'games'):
        args.game = resolve_game(args)
    return args.func(args)
