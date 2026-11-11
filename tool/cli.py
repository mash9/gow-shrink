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
    if args.in_place and not args.backup:
        raise SystemExit('--in-place requires --backup DIR')

    manifest = None
    if args.in_place:
        os.makedirs(args.backup, exist_ok=True)
        manifest = _load_manifest(args.backup)
    else:
        os.makedirs(args.out, exist_ok=True)

    paths = tx.iter_texpacks(args.paths)
    for path in paths:
        name = os.path.basename(path)
        game_dir = os.path.dirname(os.path.abspath(path))
        print(f'{name}')

        # If a backup exists it holds the original; always rebuild from it on repeated runs
        src = path
        if args.in_place:
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

        if args.in_place:
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
        _move(bpack + '.toc', target + '.toc')
        del m['files'][name]
        _save_manifest(args.backup, m)
        print(f'[RESTORED] {name} -> {target}')
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
    p.add_argument('--backup', metavar='DIR', help='backup folder for originals (for --in-place)')
    p.add_argument('--dry-run', action='store_true', help='print the plan without writing anything')
    p.set_defaults(func=cmd_shrink)

    p = sub.add_parser('verify', help='check texpack/.toc integrity')
    p.add_argument('paths', nargs='+', help='.texpack files or folders')
    p.add_argument('--quick', action='store_true', help='skip block header checks')
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser('restore', help='restore originals from the backup')
    p.add_argument('--backup', required=True, metavar='DIR')
    p.add_argument('names', nargs='*', help='file names to restore (default: all)')
    p.set_defaults(func=cmd_restore)

    args = ap.parse_args(argv)
    return args.func(args)
