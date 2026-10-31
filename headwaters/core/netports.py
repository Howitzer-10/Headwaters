"""Port selection for the local servers (HDW-201, v3.1 C3).

Servers bind ``127.0.0.1`` only. On Windows, Hyper-V and WSL reserve ranges of TCP ports
(``netsh int ipv4 show excludedportrange protocol=tcp``); binding inside one fails or, worse,
works until the next reboot. ``bind_first_free`` skips those ranges and any port that is taken,
and returns the bound socket itself so nothing can grab the port between the check and the bind.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import sys
from collections.abc import Callable, Collection, Sequence

__all__ = [
    "DEFAULT_SPAN",
    "EXCLUDED_RANGE_COMMAND",
    "NoFreePortError",
    "NonLoopbackBindError",
    "PortError",
    "PortRange",
    "assert_loopback_host",
    "bind_first_free",
    "bind_loopback",
    "is_excluded",
    "parse_excluded_ranges",
    "pick_free_port",
    "read_excluded_ranges",
]

EXCLUDED_RANGE_COMMAND: tuple[str, ...] = (
    "netsh",
    "int",
    "ipv4",
    "show",
    "excludedportrange",
    "protocol=tcp",
)
LOOPBACK_HOST = "127.0.0.1"
MAX_PORT = 65535
DEFAULT_SPAN = 200  # how many ports above the preferred one to try
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: a console child of a detached daemon must not flash
_RANGE_LINE = re.compile(r"^\s*(\d{1,5})\s+(\d{1,5})\s*\*?\s*$")

PortRange = tuple[int, int]
Runner = Callable[[Sequence[str]], str]
Binder = Callable[[int, str], socket.socket]


class PortError(RuntimeError):
    """A port could not be chosen or bound."""


class NonLoopbackBindError(PortError):
    """Something asked to bind a server to an address other than loopback."""


class NoFreePortError(PortError):
    """Every port in the tried span is excluded, taken or busy."""


def parse_excluded_ranges(text: str) -> list[PortRange]:
    """Ranges from ``netsh ... show excludedportrange`` output.

    Only lines that are two port numbers (optionally followed by ``*`` for an administered
    exclusion) count, so headers in any language and the footnote are ignored.
    """
    ranges: list[PortRange] = []
    for line in text.splitlines():
        match = _RANGE_LINE.match(line)
        if match is None:
            continue
        start, end = int(match.group(1)), int(match.group(2))
        if 1 <= start <= end <= MAX_PORT:
            ranges.append((start, end))
    return ranges


def _run_netsh(argv: Sequence[str]) -> str:
    try:
        done = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", _NO_WINDOW),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def read_excluded_ranges(
    run: Runner | None = None, *, platform: str | None = None
) -> list[PortRange]:
    """The TCP exclusion ranges of this machine; empty off Windows or when ``netsh`` fails."""
    if (platform or sys.platform) != "win32":
        return []
    return parse_excluded_ranges((run or _run_netsh)(EXCLUDED_RANGE_COMMAND))


def is_excluded(port: int, ranges: Collection[PortRange]) -> bool:
    return any(start <= port <= end for start, end in ranges)


def assert_loopback_host(host: str) -> str:
    """Return ``host`` when it is an IPv4 loopback literal, else raise.

    Names such as ``localhost`` are refused on purpose: they can resolve to something else.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise NonLoopbackBindError(
            f"Refusing to bind {host!r}: servers bind a 127.x.x.x address only."
        ) from None
    if address.version != 4 or not address.is_loopback:
        raise NonLoopbackBindError(
            f"Refusing to bind {host!r}: servers bind a 127.x.x.x address only."
        )
    return host


def bind_loopback(port: int, host: str = LOOPBACK_HOST) -> socket.socket:
    """A TCP socket bound to ``host:port`` (loopback only), not yet listening."""
    assert_loopback_host(host)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            # Without this, Windows lets a second process share the port.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def bind_first_free(
    preferred: int,
    *,
    excluded: Collection[PortRange] = (),
    taken: Collection[int] = (),
    span: int = DEFAULT_SPAN,
    host: str = LOOPBACK_HOST,
    binder: Binder = bind_loopback,
) -> socket.socket:
    """Bind the first usable port at or above ``preferred`` and return the socket."""
    assert_loopback_host(host)
    last = min(preferred + max(span, 0), MAX_PORT)
    for port in range(max(preferred, 1), last + 1):
        if port in taken or is_excluded(port, excluded):
            continue
        try:
            return binder(port, host)
        except OSError:
            continue
    raise NoFreePortError(f"No free port between {preferred} and {last} (excluded or in use).")


def pick_free_port(
    preferred: int,
    *,
    excluded: Collection[PortRange] = (),
    taken: Collection[int] = (),
    span: int = DEFAULT_SPAN,
    host: str = LOOPBACK_HOST,
    binder: Binder = bind_loopback,
) -> int:
    """Like ``bind_first_free`` but releases the port again: a probe, not a reservation."""
    sock = bind_first_free(
        preferred, excluded=excluded, taken=taken, span=span, host=host, binder=binder
    )
    try:
        port = int(sock.getsockname()[1])
    finally:
        sock.close()
    return port
