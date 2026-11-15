from __future__ import annotations

import numpy as np

IV = np.array([0x6A09E667, 0xBB67AE85, 0x3C6EF372, 0xA54FF53A,
               0x510E527F, 0x9B05688C, 0x1F83D9AB, 0x5BE0CD19], dtype=np.uint32)
_PERM = [2, 6, 3, 10, 7, 0, 4, 13, 1, 11, 12, 5, 9, 14, 15, 8]
_SCHED = [list(range(16))]
for _ in range(6):
    _SCHED.append([_SCHED[-1][p] for p in _PERM])
_G = (((0, 4, 8, 12), 0), ((1, 5, 9, 13), 2), ((2, 6, 10, 14), 4), ((3, 7, 11, 15), 6),
      ((0, 5, 10, 15), 8), ((1, 6, 11, 12), 10), ((2, 7, 8, 13), 12), ((3, 4, 9, 14), 14))
CHUNK_START, CHUNK_END, PARENT, ROOT = 1, 2, 4, 8
CHUNK_LEN = 1024
_U16, _U12, _U8, _U7 = (np.uint32(n) for n in (16, 12, 8, 7))
_L16, _L20, _L24, _L25 = (np.uint32(n) for n in (16, 20, 24, 25))

try:
    import blake3 as _native
except ImportError:
    _native = None


def _compress(cv: np.ndarray, m: np.ndarray, counter: np.ndarray, blen, flags) -> np.ndarray:
    n = cv.shape[1]
    s = [cv[i].copy() for i in range(8)] + [np.full(n, IV[i], np.uint32) for i in range(4)]
    s.append((counter & np.uint64(0xffffffff)).astype(np.uint32))
    s.append((counter >> np.uint64(32)).astype(np.uint32))
    s.append(np.broadcast_to(np.asarray(blen, np.uint32), (n,)).copy())
    s.append(np.broadcast_to(np.asarray(flags, np.uint32), (n,)).copy())
    t = np.empty(n, np.uint32)
    for r in range(7):
        sched = _SCHED[r]
        for (a, b, c, d), x in _G:
            sa, sb, sc, sd = s[a], s[b], s[c], s[d]
            sa += sb
            sa += m[sched[x]]
            sd ^= sa
            np.right_shift(sd, _U16, out=t); sd <<= _L16; sd |= t
            sc += sd
            sb ^= sc
            np.right_shift(sb, _U12, out=t); sb <<= _L20; sb |= t
            sa += sb
            sa += m[sched[x + 1]]
            sd ^= sa
            np.right_shift(sd, _U8, out=t); sd <<= _L24; sd |= t
            sc += sd
            sb ^= sc
            np.right_shift(sb, _U7, out=t); sb <<= _L25; sb |= t
    return np.stack([s[i] ^ s[i + 8] for i in range(8)])


def _chunk_cvs(data, start_counter: int, last_is_root: bool) -> np.ndarray:
    size = len(data)
    n = max(1, -(-size // CHUNK_LEN))
    buf = np.zeros(n * CHUNK_LEN, np.uint8)
    buf[:size] = np.frombuffer(data, np.uint8)
    words = buf.view('<u4').reshape(n, 16, 16).transpose(1, 2, 0)
    last_len = size - (n - 1) * CHUNK_LEN
    last_blocks = max(1, -(-last_len // 64))
    counter = np.arange(start_counter, start_counter + n, dtype=np.uint64)
    cv = np.repeat(IV[:, None], n, axis=1)
    for k in range(16):
        flags = np.zeros(n, np.uint32)
        blen = np.full(n, 64, np.uint32)
        if k == 0:
            flags |= CHUNK_START
        if k == 15:
            flags |= CHUNK_END
        if k == last_blocks - 1:
            flags[-1] |= CHUNK_END
            blen[-1] = last_len - k * 64
            if last_is_root:
                flags[-1] |= ROOT
        new = _compress(cv, np.ascontiguousarray(words[k]), counter, blen, flags)
        if k >= last_blocks:
            new[:, -1] = cv[:, -1]
        cv = new
    return cv


def _reduce(cv: np.ndarray) -> np.ndarray:
    while cv.shape[1] > 1:
        m = cv.shape[1]
        pairs = m // 2
        msg = np.ascontiguousarray(np.concatenate([cv[:, 0:2 * pairs:2], cv[:, 1:2 * pairs:2]]))
        flags = np.full(pairs, PARENT, np.uint32)
        if m == 2:
            flags |= ROOT
        out = _compress(np.repeat(IV[:, None], pairs, axis=1), msg, np.zeros(pairs, np.uint64), 64, flags)
        cv = np.concatenate([out, cv[:, 2 * pairs:]], axis=1) if m % 2 else out
    return cv


def blake3_160(data) -> bytes:
    if _native is not None:
        return _native.blake3(bytes(data)).digest(20)
    n = max(1, -(-len(data) // CHUNK_LEN))
    cv = _chunk_cvs(data, 0, n == 1)
    return _reduce(cv)[:, 0].astype('<u4').tobytes()[:20]
