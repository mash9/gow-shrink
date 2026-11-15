from __future__ import annotations

MAGIC = b'\x04\x22\x4d\x18'


class LZ4Error(Exception):
    pass


def _frame():
    try:
        import lz4.frame
    except ImportError:
        raise LZ4Error('the "lz4" package is required for God of War Ragnarok files: '
                       'python -m pip install lz4') from None
    return lz4.frame


def is_frame(data: bytes) -> bool:
    return data[:4] == MAGIC


def decompress(data: bytes) -> bytes:
    if not is_frame(data):
        raise LZ4Error('not an LZ4 frame')
    try:
        return _frame().decompress(data)
    except RuntimeError as e:
        raise LZ4Error(str(e)) from None


def compress(data: bytes) -> bytes:
    f = _frame()
    return f.compress(data, compression_level=0, block_size=f.BLOCKSIZE_MAX64KB,
                      block_linked=False, content_checksum=True, store_size=True)
