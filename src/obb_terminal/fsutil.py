"""Small filesystem helpers shared by every module that persists state.

Locks are flock on POSIX and a one-byte msvcrt lock on Windows; both are per open handle, so
threads in one process exclude each other too, and both are dropped if the holder dies.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
# Windows refuses to replace a file another process has open; readers hold it only briefly.
REPLACE_ATTEMPTS = 20
REPLACE_RETRY_S = 0.05

if IS_WINDOWS:
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class LockBusy(Exception):
    """Another process held the lock for longer than the timeout."""


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (temp file + fsync + rename + dir fsync on POSIX).

    Creates missing parent directories. Raises OSError; never leaves a temp file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        _replace(tmp_name, path)
        tmp_name = None
        if not IS_WINDOWS:  # Windows cannot open a directory; NTFS journals the rename itself
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        if tmp_name is not None:
            Path(tmp_name).unlink(missing_ok=True)


def _replace(src: str, dst: Path) -> None:
    if not IS_WINDOWS:
        os.replace(src, dst)
        return
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_S)


@contextmanager
def file_lock(path: Path, timeout_s: float, poll_s: float = 0.05) -> Iterator[None]:
    """Hold an exclusive lock on `path` (created 0600 if missing) for the `with` body.

    A fresh descriptor per call, so threads in one process exclude each other too. The OS
    drops the lock if the holder dies. Raises LockBusy after `timeout_s`, OSError on I/O errors.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOINHERIT", 0)
    fd = os.open(path, flags, 0o600)
    try:
        deadline = time.monotonic() + timeout_s
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LockBusy(str(path)) from None
            time.sleep(poll_s)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)
