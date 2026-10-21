"""Bound reads from local regular files without blocking on special files."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


def read_bounded_file(path: Path, maximum_bytes: int) -> bytes:
    """Read at most ``maximum_bytes + 1`` bytes from a regular file.

    The extra byte lets callers distinguish an exact-limit file from an
    oversized one. Non-blocking open plus a post-open ``fstat`` prevents a
    path replaced by a FIFO from hanging the reader.
    """
    if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool) or maximum_bytes < 0:
        raise ValueError("file_read_limit_invalid")
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError(errno.EINVAL, "regular file required", str(path))
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)

    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(errno.EINVAL, "regular file required", str(path))
        source = os.fdopen(descriptor, "rb")
        descriptor = -1
        with source:
            return source.read(maximum_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
