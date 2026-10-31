"""UTF-8 stdio and a stdout guard for MCP stdio servers.

MCP stdio carries JSON-RPC and nothing else on stdout. ``guard_stdout`` swaps ``sys.stdout``
for a sentinel so a stray ``print`` is caught, and hands the protocol layer a writer bound to
the real stdout.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import IO, Any


class StdoutViolationError(RuntimeError):
    """Something other than the protocol writer wrote to stdout."""


def configure_utf8() -> None:
    """Force UTF-8 on stdin/stdout/stderr and for child Python processes."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if stream is sys.stdin:
            reconfigure(encoding="utf-8", errors="replace")
        else:
            reconfigure(encoding="utf-8", errors="replace", newline="\n")


class _GuardedStdout:
    """Stand-in for ``sys.stdout`` that rejects every write."""

    def __init__(self, real: IO[Any], strict: bool) -> None:
        self._real = real
        self._strict = strict
        self.violations: list[str] = []

    @property
    def encoding(self) -> str:
        return "utf-8"

    def write(self, data: str | bytes) -> int:
        text = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
        if not text:
            return 0
        self.violations.append(text)
        if self._strict:
            raise StdoutViolationError(f"non-protocol write to stdout: {text[:80]!r}")
        sys.stderr.write(text)  # production: divert to stderr instead of corrupting the stream
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    @property
    def buffer(self) -> _GuardedStdout:
        return self


@contextmanager
def guard_stdout(*, strict: bool = True) -> Iterator[Callable[[str], None]]:
    """Guard stdout; yield ``write_protocol(text)``, the only sanctioned way to emit output.

    With ``strict=True`` (tests) any other write raises ``StdoutViolationError``. With
    ``strict=False`` (production) stray writes are diverted to stderr and the stream stays
    clean. Only Python-level writes via ``sys.stdout`` are caught; raw fd-1 writes from C
    extensions or subprocesses inheriting stdout are not.
    """
    real = sys.stdout
    guard = _GuardedStdout(real, strict)

    def write_protocol(text: str) -> None:
        real.write(text)
        real.flush()

    sys.stdout = guard
    try:
        yield write_protocol
    finally:
        sys.stdout = real
