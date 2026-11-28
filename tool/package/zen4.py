from __future__ import annotations

import struct
from dataclasses import dataclass, field

SUMMARY = struct.Struct('<QQIIiiiiiiiiii')
EXPORT_ENTRY = struct.Struct('<QQQQQQQQIB3x')
STORE_ENTRY = struct.Struct('<QiiIIII')
SCRIPT_ENTRY = struct.Struct('<QQQQ')
NONE_INDEX = 0xffffffffffffffff


class ZenError(Exception):
    pass


def name_batch(b: bytes, count: int, p: int = 0) -> tuple[list[str], int]:
    out = []
    for _ in range(count):
        if p + 2 > len(b):
            raise ZenError('name batch is truncated')
        h = struct.unpack_from('>H', b, p)[0]
        p += 2
        n = h & 0x7fff
        if h & 0x8000:
            out.append(b[p:p + 2 * n].decode('utf-16-le', 'replace'))
            p += 2 * n
        else:
            out.append(b[p:p + n].decode('latin-1'))
            p += n
    return out, p


def mapped_name(names: list[str], v: int) -> str:
    idx, num = v & 0xffffffff, v >> 32
    idx &= 0x3fffffff
    if idx >= len(names):
        raise ZenError(f'name index {idx} out of range')
    return names[idx] + (f'_{num - 1}' if num else '')


def script_objects(meta: bytes, global_names: bytes, name_hashes: bytes) -> dict[int, str]:
    names, _ = name_batch(global_names, len(name_hashes) // 8 - 1)
    n = struct.unpack_from('<i', meta, 0)[0]
    objs = {}
    for i in range(n):
        name, gi, outer, _ = SCRIPT_ENTRY.unpack_from(meta, 4 + SCRIPT_ENTRY.size * i)
        objs[gi] = (mapped_name(names, name), outer)
    out = {}
    for gi in objs:
        parts = []
        k = gi
        while k in objs and len(parts) < 16:
            nm, k = objs[k]
            parts.append(nm)
        out[gi] = '.'.join(reversed(parts))
    return out


@dataclass
class Export:
    pos: int
    serial_offset: int
    serial_size: int
    name: str
    class_index: int
    cls: str


@dataclass
class Package:
    data: bytes
    flags: int
    cooked_header_size: int
    names: list[str]
    exports: list[Export]
    header_end: int

    @property
    def delta(self) -> int:
        return self.cooked_header_size - self.header_end


def read_package(b: bytes, scripts: dict[int, str] | None = None) -> Package:
    if len(b) < SUMMARY.size:
        raise ZenError('package is too short')
    (_, _, flags, chs, no, ns, ho, hs, io, eo, bo, go, gs, _) = SUMMARY.unpack_from(b, 0)
    if not (SUMMARY.size <= no <= ho <= io <= eo <= bo <= go and go + gs <= len(b)) or hs < 8 or hs % 8:
        raise ZenError('bad package summary')
    names, end = name_batch(b[no:no + ns], hs // 8 - 1)
    if end != ns:
        raise ZenError('name batch size mismatch')
    if (bo - eo) % EXPORT_ENTRY.size:
        raise ZenError('bad export map size')
    exports = []
    for p in range(eo, bo, EXPORT_ENTRY.size):
        so, ss, on, _, cls, _, _, _, _, _ = EXPORT_ENTRY.unpack_from(b, p)
        exports.append(Export(p, so, ss, mapped_name(names, on), cls,
                              (scripts or {}).get(cls, f'{cls:#018x}')))
    return Package(b, flags, chs, names, exports, go + gs)


def _fstring(b: bytes, p: int) -> tuple[str, int]:
    n = struct.unpack_from('<i', b, p)[0]
    p += 4
    if n < 0:
        return b[p:p - 2 * n].decode('utf-16-le')[:-1], p - 2 * n
    return b[p:p + n].decode('latin-1')[:-1], p + n


def _put_fstring(s: str) -> bytes:
    if s.isascii():
        raw = s.encode() + b'\0'
        return struct.pack('<i', len(raw)) + raw
    raw = (s + '\0').encode('utf-16-le')
    return struct.pack('<i', -(len(raw) // 2)) + raw


@dataclass
class StoreEntry:
    export_bundles_size: int
    export_count: int
    export_bundle_count: int
    load_order: int
    pad: int
    imports: tuple[int, ...]


@dataclass
class ContainerHeader:
    container_id: int
    names: bytes
    name_hashes: bytes
    package_ids: list[int]
    entries: list[StoreEntry]
    cultures: list[tuple[str, list[tuple[int, int]]]] = field(default_factory=list)
    redirects: list[tuple[int, int]] = field(default_factory=list)

    def index(self) -> dict[int, int]:
        return {pid: i for i, pid in enumerate(self.package_ids)}

    def pack(self) -> bytes:
        out = bytearray(struct.pack('<QI', self.container_id, len(self.package_ids)))
        out += struct.pack('<i', len(self.names)) + self.names
        out += struct.pack('<i', len(self.name_hashes)) + self.name_hashes
        out += struct.pack('<i', len(self.package_ids)) + struct.pack(f'<{len(self.package_ids)}Q', *self.package_ids)
        fixed = STORE_ENTRY.size * len(self.entries)
        store = bytearray(fixed)
        for k, e in enumerate(self.entries):
            off = 0
            if e.imports:
                off = len(store) - (STORE_ENTRY.size * k + 24)
                store += struct.pack(f'<{len(e.imports)}Q', *e.imports)
            STORE_ENTRY.pack_into(store, STORE_ENTRY.size * k, e.export_bundles_size, e.export_count,
                                  e.export_bundle_count, e.load_order, e.pad, len(e.imports), off)
        out += struct.pack('<i', len(store)) + store
        out += struct.pack('<i', len(self.cultures))
        for name, pairs in self.cultures:
            out += _put_fstring(name) + struct.pack('<i', len(pairs))
            out += b''.join(struct.pack('<QQ', a, c) for a, c in pairs)
        out += struct.pack('<i', len(self.redirects))
        out += b''.join(struct.pack('<QQ', a, c) for a, c in self.redirects)
        return bytes(out)

    def without(self, package_ids) -> 'ContainerHeader':
        gone = set(package_ids)
        keep = [i for i, pid in enumerate(self.package_ids) if pid not in gone]
        for i in keep:
            if gone & set(self.entries[i].imports):
                raise ZenError(f'package {self.package_ids[i]:#018x} imports a removed package')
        cultures = []
        for name, pairs in self.cultures:
            pairs = [(a, c) for a, c in pairs if c not in gone]
            if pairs:
                cultures.append((name, pairs))
        return ContainerHeader(self.container_id, self.names, self.name_hashes,
                               [self.package_ids[i] for i in keep], [self.entries[i] for i in keep],
                               cultures, [(a, c) for a, c in self.redirects if a not in gone and c not in gone])


def read_container_header(b: bytes) -> ContainerHeader:
    try:
        cid, count = struct.unpack_from('<QI', b, 0)
        p = 12
        n = struct.unpack_from('<i', b, p)[0]
        names = b[p + 4:p + 4 + n]
        p += 4 + n
        n = struct.unpack_from('<i', b, p)[0]
        hashes = b[p + 4:p + 4 + n]
        p += 4 + n
        n = struct.unpack_from('<i', b, p)[0]
        if n != count:
            raise ZenError('package id count does not match the package count')
        ids = list(struct.unpack_from(f'<{n}Q', b, p + 4))
        p += 4 + 8 * n
        size = struct.unpack_from('<i', b, p)[0]
        sp = p + 4
        if size < STORE_ENTRY.size * count:
            raise ZenError('store entries are truncated')
        entries = []
        for k in range(count):
            ebs, ec, ebc, lo, pad, ic, off = STORE_ENTRY.unpack_from(b, sp + STORE_ENTRY.size * k)
            imports: tuple[int, ...] = ()
            if ic:
                st = sp + STORE_ENTRY.size * k + 24 + off
                if not (sp + STORE_ENTRY.size * count <= st and st + 8 * ic <= sp + size):
                    raise ZenError('imported package array out of range')
                imports = struct.unpack_from(f'<{ic}Q', b, st)
            entries.append(StoreEntry(ebs, ec, ebc, lo, pad, imports))
        p = sp + size
        cultures = []
        nc = struct.unpack_from('<i', b, p)[0]
        p += 4
        for _ in range(nc):
            name, p = _fstring(b, p)
            m = struct.unpack_from('<i', b, p)[0]
            pairs = [struct.unpack_from('<QQ', b, p + 4 + 16 * j) for j in range(m)]
            p += 4 + 16 * m
            cultures.append((name, pairs))
        nr = struct.unpack_from('<i', b, p)[0]
        redirects = [struct.unpack_from('<QQ', b, p + 4 + 16 * j) for j in range(nr)]
        p += 4 + 16 * nr
    except struct.error:
        raise ZenError('container header is truncated') from None
    if p != len(b):
        raise ZenError(f'container header has {len(b) - p} unexpected trailing bytes')
    return ContainerHeader(cid, names, hashes, ids, entries, cultures, redirects)
