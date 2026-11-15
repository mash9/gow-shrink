from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Callable

from . import lz4frame
from . import texpack as tx

INFO_SIZE = 0x24
GNF_V4_HEAD = b'GNF \xf8\x0f\x00\x00\x04'
STREAM_PAD = 0x1000


class WadError(Exception):
    pass


@dataclass(frozen=True)
class Chain:
    top_mip: int
    width: int
    height: int
    total: int

    @classmethod
    def of(cls, t: tx.Texture) -> 'Chain':
        top = t.chain[-1]
        return cls(top.top_mip, top.width, top.height, sum(r.payload_size for r in t.chain))


def index_texpacks(texpack_dir: str) -> dict[bytes, Chain]:
    out: dict[bytes, Chain] = {}
    where: dict[bytes, str] = {}
    conflicts = []
    for path in tx.iter_texpacks([texpack_dir]):
        tp = tx.Texpack.open(path)
        if tp.version != tx.VERSION_GOWR:
            raise WadError(f'{path}: not a Ragnarok texpack (version {tp.version})')
        name = os.path.basename(path)
        for t in tp.textures:
            h = t.hash[8:]
            c = Chain.of(t)
            old = out.get(h)
            if old is None:
                out[h] = c
                where[h] = name
            elif old != c:
                conflicts.append(f'{h.hex()}: {where[h]} {old} vs {name} {c}')
    if conflicts:
        raise WadError('texture shared by several texpacks has different chains:\n  '
                       + '\n  '.join(conflicts[:10]))
    return out


def outside_chains(texpack_dir: str, exclude: set[str]) -> dict[bytes, list[tuple]]:
    out: dict[bytes, list[tuple]] = {}
    for path in tx.iter_texpacks([texpack_dir]):
        if os.path.basename(path) in exclude:
            continue
        tp = tx.Texpack.open(path)
        for t in tp.textures:
            out[t.hash[8:]] = _rec_key(t.chain)
    return out


def _rec_key(chain: list[tx.Record]) -> list[tuple]:
    return [(r.top_mip, r.bottom_mip, r.width, r.height, r.payload_size) for r in chain]


def align_drops(plan: tx.Plan, outside: dict[bytes, list[tuple]]) -> int:
    changed = 0
    for i, t in enumerate(plan.tp.textures):
        other = outside.get(t.hash[8:])
        if other is None:
            continue
        mine = _rec_key(t.chain)
        if len(other) > len(mine) or mine[:len(other)] != other:
            raise WadError(f'{plan.tp.path}: texture {i} differs from the copy in another texpack')
        n = len(mine) - len(other)
        if plan.drops[i] != n:
            plan.drops[i] = n
            changed += 1
    return changed


def iter_infos(buf: bytes):
    p = buf.find(GNF_V4_HEAD)
    while p >= 0:
        if p >= INFO_SIZE:
            yield p - INFO_SIZE, bytes(buf[p + 0x38:p + 0x40])
        p = buf.find(GNF_V4_HEAD, p + 4)


def rebuilt_info(src: bytes, chain: Chain) -> bytes:
    flags, b2, nmip, w, h = struct.unpack_from('<HBBHH', src, 0)
    table = bytearray(src[8:24])
    size, pad1, pad2 = struct.unpack_from('<III', src, 24)
    top = chain.top_mip
    if not 0 <= top < nmip <= 16:
        raise WadError(f'mip count {nmip} does not cover texpack top mip {top}')
    sw, sh = w, h
    for _ in range(nmip - 1 - top):
        sw, sh = max(1, sw // 2), max(1, sh // 2)
    if (sw, sh) != (chain.width, chain.height):
        raise WadError(f'{w}x{h} with {nmip} mips does not reduce to texpack top '
                       f'{chain.width}x{chain.height} (mip {top})')
    if nmip == top + 1 and size not in (0, chain.total + STREAM_PAD):
        raise WadError(f'stream size {size:#x} != texpack {chain.total + STREAM_PAD:#x}')
    if any(table[k] > table[k + 1] for k in range(15)):
        raise WadError(f'size table is not cumulative: {table.hex()}')
    for k in range(top + 1, 16):
        table[k] = table[top]
    new_size = 0 if size == 0 else chain.total + STREAM_PAD
    out = bytearray(src)
    struct.pack_into('<BHH', out, 3, top + 1, chain.width, chain.height)
    out[8:24] = table
    struct.pack_into('<I', out, 24, new_size)
    return bytes(out)


@dataclass
class WadResult:
    name: str
    refs: int = 0
    patched: int = 0
    action: str = 'unchanged'
    errors: list[str] = field(default_factory=list)


def plan_wad(src_raw: bytes, chains: dict[bytes, Chain], name: str) -> tuple[WadResult, bytes | None]:
    res = WadResult(name)
    try:
        buf = lz4frame.decompress(src_raw)
    except lz4frame.LZ4Error as e:
        res.errors.append(f'{name}: {e}')
        return res, None
    new = None
    for off, h in iter_infos(buf):
        chain = chains.get(h)
        if chain is None:
            continue
        res.refs += 1
        src = bytes(buf[off:off + INFO_SIZE])
        try:
            info = rebuilt_info(src, chain)
        except WadError as e:
            res.errors.append(f'{name} @ {off:#x} ({h.hex()}): {e}')
            continue
        if info != src:
            if new is None:
                new = bytearray(buf)
            new[off:off + INFO_SIZE] = info
            res.patched += 1
    if res.errors:
        return res, None
    return res, (bytes(new) if new is not None else None)


def check_rewrite(original: bytes, new: bytes, raw_out: bytes) -> None:
    if lz4frame.decompress(raw_out) != new:
        raise WadError('rewritten wad does not decode to the intended content')
    if len(original) != len(new):
        raise WadError('content size changed')
    masked = bytearray(new)
    for off, _ in iter_infos(original):
        masked[off:off + INFO_SIZE] = original[off:off + INFO_SIZE]
    if masked != original:
        raise WadError('bytes outside the info blocks changed')


def iter_wads(wad_dir: str) -> list[str]:
    return sorted(n for n in os.listdir(wad_dir) if n.endswith('.wad'))


def sync(wad_dir: str, texpack_dir: str | None = None, backup: str | None = None,
         dry_run: bool = False, on_backup: Callable[[str, str, int], None] | None = None,
         on_restore: Callable[[str], None] | None = None,
         progress: Callable[[int, int], None] | None = None,
         move: Callable[[str, str], None] = os.replace) -> list[WadResult]:
    chains = index_texpacks(texpack_dir or wad_dir)
    names = iter_wads(wad_dir)
    results: list[WadResult] = []
    todo = []
    for i, name in enumerate(names):
        game = os.path.join(wad_dir, name)
        bcopy = os.path.join(backup, name) if backup else None
        src = bcopy if bcopy and os.path.exists(bcopy) else game
        with open(src, 'rb') as f:
            raw = f.read()
        res, new = plan_wad(raw, chains, name)
        results.append(res)
        if not res.errors:
            if new is not None:
                res.action = 'rewrite'
                todo.append((res, src, new))
            elif src != game:
                res.action = 'restore'
                todo.append((res, src, None))
        if progress:
            progress(i + 1, len(names))
    if dry_run or any(r.errors for r in results):
        return results

    for res, src, new in todo:
        game = os.path.join(wad_dir, res.name)
        if new is None:
            move(src, game)
            if on_restore:
                on_restore(res.name)
            continue
        with open(src, 'rb') as f:
            original = lz4frame.decompress(f.read())
        raw_out = lz4frame.compress(new)
        check_rewrite(original, new, raw_out)
        tmp = game + '.gowshrink.tmp'
        with open(tmp, 'wb') as f:
            f.write(raw_out)
        tx.copy_times(src, tmp)
        if backup and src == game:
            os.makedirs(backup, exist_ok=True)
            size = os.path.getsize(game)
            move(game, os.path.join(backup, res.name))
            if on_backup:
                on_backup(res.name, wad_dir, size)
        os.replace(tmp, game)
    return results
