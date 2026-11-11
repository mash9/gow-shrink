"""gow-shrink command-line tool.

    python -m tool scan    <texpack|dir>... [--drop N] [--max-res R] [--min-res R]
    python -m tool shrink  <texpack|dir>... (--out DIR | --in-place --backup DIR) [...]
    python -m tool verify  <texpack|dir>...
    python -m tool restore --backup DIR
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import time

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


# --------------------------------------------------------------------------


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
        # Different drive: copy, then delete
        shutil.copy2(src, dst + '.tmp')
        os.replace(dst + '.tmp', dst)
        os.remove(src)


def _check_free(directory: str, need: int) -> None:
    free = shutil.disk_usage(directory).free
    if free < need + (1 << 30):
        raise SystemExit(f'not enough free disk space in {directory}: {fmt_size(free)} free, '
                         f'need {fmt_size(need)} + 1 GiB margin')


def cmd_shrink(args) -> int:
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
    for path in paths:
        name = os.path.basename(path)
        game_dir = os.path.dirname(os.path.abspath(path))
        print(f'{name}')

        # If a backup exists it holds the original; always rebuild from it on repeated runs
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
    return 0


def cmd_restore(args) -> int:
    m = _load_manifest(args.backup)
    if not m['files']:
        print('nothing to restore')
        return 0
    names = args.names or list(m['files'])
    for name in names:
        info = m['files'].get(name)
        if info is None:
            print(f'[SKIP] {name}: not in manifest')
            continue
        bpack = os.path.join(args.backup, name)
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
    return 0


def cmd_lod0(args) -> int:
    from . import lod as lodmod

    wad_dir = args.dir
    lod = lodmod.load_lod_hashes(wad_dir)
    print(f'lodpack entries: {len(lod)}')
    manifest = None
    if not args.dry_run:
        os.makedirs(args.backup, exist_ok=True)
        manifest = _load_manifest(args.backup)

    stats: dict = {}
    total_patches = files = 0
    lod0_bytes = 0
    wads = sorted(n for n in os.listdir(wad_dir) if n.endswith('.wad'))
    for name in wads:
        path = os.path.join(wad_dir, name)
        if os.path.getsize(path) == 0:
            continue
        # Always rebuild from the pristine backup copy when there is one
        bpath = os.path.join(args.backup, name)
        src = bpath if os.path.exists(bpath) else path
        with open(src, 'rb') as f:
            buf = f.read()
        patches = lodmod.find_patches(buf, lod, stats)
        del buf
        if not patches:
            continue
        files += 1
        total_patches += len(patches)
        lod0_bytes += sum(p.lod0_size for p in patches)
        print(f'{name}: {len(patches)} meshes')
        if args.dry_run:
            continue

        tmp = path + '.gowshrink.tmp'
        lodmod.write_patched(src, tmp, patches)
        errs = lodmod.verify_patched(src, tmp, patches)
        if errs:
            os.remove(tmp)
            print(f'  [FAIL] {"; ".join(errs)}; original left untouched')
            return 1
        if src == path:
            _move(path, bpath)
            manifest['files'][name] = {'target_dir': os.path.abspath(wad_dir),
                                       'size': os.path.getsize(bpath)}
            _save_manifest(args.backup, manifest)
        os.replace(tmp, path)

    print()
    print(f'wads patched: {files}, meshes patched: {total_patches}')
    print('skipped/patched counts:', stats)
    print(f'LOD0 data no longer referenced by patched meshes: {fmt_size(lod0_bytes)} '
          f'(counted per wad, lodpack not modified)')
    return 0


def cmd_lod0_free(args) -> int:
    from . import lod as lodmod

    wad_dir = args.dir
    entries = lodmod.lodpack_entries(wad_dir)
    sizes = {h: e[2] for h, e in entries.items()}
    originals = sorted(os.path.join(args.wad_backup, n) for n in os.listdir(args.wad_backup)
                       if n.endswith('.wad'))
    if not originals:
        raise SystemExit(f'no original .wad files in {args.wad_backup}; run lod0 first')
    pairs = lodmod.replaced_lod0(originals, sizes)
    print(f'LOD0 entries replaced by lod0: {len(pairs)}')

    wads = sorted(os.path.join(wad_dir, n) for n in os.listdir(wad_dir)
                  if n.endswith('.wad') and os.path.getsize(os.path.join(wad_dir, n)))
    watch = set(entries) | lodmod.lodpack_group_hashes(wad_dir)
    refs = lodmod.referenced_hashes(wads, watch, progress=Progress('scanning wads'))

    per_pack: dict[str, list[tuple[int, int]]] = collections.defaultdict(list)
    skipped = collections.Counter()
    for h0, h1s in pairs.items():
        name, off, size, group = entries[h0]
        if len(h1s) != 1:
            skipped['several LOD1 targets'] += 1
        elif refs.get(h0):
            skipped['LOD0 still referenced'] += 1
        elif refs.get(group):
            skipped['whole group referenced'] += 1
        else:
            per_pack[name].append((off, size))
    freed = sum(s for r in per_pack.values() for _, s in r)
    print(f'skipped: {dict(skipped)}')
    print(f'unreferenced LOD0 entries: {sum(len(r) for r in per_pack.values())}, {fmt_size(freed)}')
    if args.dry_run:
        return 0

    os.makedirs(args.backup, exist_ok=True)
    manifest = _load_manifest(args.backup)
    before = after = 0
    for name, ranges in sorted(per_pack.items()):
        path = os.path.join(wad_dir, name)
        bpath = os.path.join(args.backup, name)
        src = bpath if os.path.exists(bpath) else path
        tmp = path + '.gowshrink.tmp'
        shutil.copyfile(src, tmp)
        lodmod.zero_ranges_sparse(tmp, ranges)
        errs = lodmod.verify_zeroed(src, tmp, ranges)
        if errs:
            os.remove(tmp)
            print(f'[FAIL] {name}: {"; ".join(errs)}; original left untouched')
            return 1
        if src == path:
            _move(path, bpath)
            manifest['files'][name] = {'target_dir': os.path.abspath(wad_dir),
                                       'size': os.path.getsize(bpath)}
            _save_manifest(args.backup, manifest)
        os.replace(tmp, path)
        b, a = os.path.getsize(path), lodmod.size_on_disk(path)
        before += b
        after += a
        print(f'{name}: {len(ranges)} entries zeroed, on disk {fmt_size(b)} -> {fmt_size(a)}')
    print(f'total on disk {fmt_size(before)} -> {fmt_size(after)}')
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tool", description='God of War (PC) texpack size reduction tool')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan', help='report estimated sizes after removal')
    p.add_argument('paths', nargs='+', help='.texpack files or folders')
    add_policy_args(p)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser('shrink', help='remove top mip levels and rewrite')
    p.add_argument('paths', nargs='+', help='.texpack files or folders')
    add_policy_args(p)
    p.add_argument('--out', metavar='DIR', help='write results to this folder (originals untouched)')
    p.add_argument('--in-place', action='store_true',
                   help='replace files in the game folder; originals are moved to --backup')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder for originals (for --in-place); without it originals are replaced')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    p.set_defaults(func=cmd_shrink)

    p = sub.add_parser('verify', help='check texpack/.toc integrity')
    p.add_argument('paths', nargs='+', help='.texpack files or folders')
    p.add_argument('--quick', action='store_true', help='skip block header checks')
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('lod0', help='(experimental) make meshes use LOD1 in place of LOD0 by patching .wad files')
    p.add_argument('dir', help='folder with .wad and .lodpack files (exec\\wad\\pc_le)')
    p.add_argument('--backup', required=True, metavar='DIR', help='backup folder for original .wad files')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    p.set_defaults(func=cmd_lod0)

    p = sub.add_parser('lod0-free', help='(experimental) free disk space of LOD0 data that lod0 made unused '
                                         '(zeroed NTFS sparse ranges; offsets unchanged)')
    p.add_argument('dir', help='folder with .wad and .lodpack files (exec\\wad\\pc_le)')
    p.add_argument('--wad-backup', required=True, metavar='DIR',
                   help='backup folder used by lod0 (holds the original .wad files)')
    p.add_argument('--backup', required=True, metavar='DIR', help='backup folder for original .lodpack files')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    p.set_defaults(func=cmd_lod0_free)

    p = sub.add_parser('shvol', help='(experimental) halve the resolution of SH lighting volumes in .wad files')
    p.add_argument('dir', help='folder with .wad files (exec\\wad\\pc_le)')
    p.add_argument('--backup', metavar='DIR',
                   help='backup folder for original .wad files; without it originals are replaced')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    p.set_defaults(func=cmd_shvol)

    p = sub.add_parser('restore', help='restore originals from the backup')
    p.add_argument('--backup', required=True, metavar='DIR')
    p.add_argument('names', nargs='*', help='file names to restore (default: all)')
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args(argv)
    return args.func(args)
