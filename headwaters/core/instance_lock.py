"""Single-instance lock for the daemon (HDW-201).

An OS file lock, not a pid file: the OS drops it when the process dies, so a crash can never leave
a stale lock behind. ``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from headwaters.core import paths
from headwaters.core.daemon_info import lock_path

__all__ = ["InstanceLock"]


def _try_lock(handle: IO[bytes]) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class InstanceLock:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        """Take the lock without waiting. ``False`` when another process holds it."""
        if self._handle is not None:
            return True
        target = paths.long_safe(self._path or lock_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = open(target, "a+b")  # noqa: SIM115 - held for the life of the lock
        if not _try_lock(handle):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()
