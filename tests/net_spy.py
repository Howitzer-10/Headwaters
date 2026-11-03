"""Socket-level network spy shared by the catalog updater and router tests (HDW-120, HDW-306)."""

from __future__ import annotations

import ipaddress
import socket
import sys
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

# FU-120-3: CPython's audit hook sees a connect/getaddrinfo even when a C extension calls the
# socket API itself, which the monkeypatched methods in ``forbid_network`` cannot. Hooks cannot be
# removed, so one is installed once at import and stays inert until an ``audit_egress`` scope is
# active.
_scopes: list[list[str]] = []


def _is_loopback(host: str | None) -> bool:
    """True for localhost names and any loopback address (127.0.0.0/8 and ::1)."""
    if not host:
        return True
    name = host.strip().strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def audited_host(event: str, args: tuple[Any, ...]) -> str | None:
    """The non-loopback host of an audited socket event, or None when it is local/unrelated."""
    if event == "socket.getaddrinfo":
        host = args[0] if args else None
    elif event == "socket.connect":
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple) or not address:
            return None  # AF_UNIX and friends never leave the machine
        host = address[0]
    else:
        return None
    if host is None:
        return None
    text = str(host)
    return None if _is_loopback(text) else text


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    if not _scopes:
        return
    host = audited_host(event, args)
    if host is None:
        return
    for scope in _scopes:
        scope.append(host)
    raise AssertionError(f"network used: {host} (audit hook)")


sys.addaudithook(_audit_hook)


@contextmanager
def audit_egress() -> Iterator[list[str]]:
    """Fail a ``socket.connect`` / ``socket.getaddrinfo`` to a non-loopback host (FU-120-3).

    Use it around code that must stay on the machine, together with ``forbid_network``. The two
    cover different layers: the monkeypatch catches Python calls, the audit hook catches a C
    extension that opens its own socket (unless it makes raw syscalls). Yields the refused hosts.
    """
    attempts: list[str] = []
    _scopes.append(attempts)
    try:
        yield attempts
    finally:
        for index, scope in enumerate(_scopes):
            if scope is attempts:  # identity, not equality: two empty scopes compare equal
                del _scopes[index]
                break


def _host_of(args: tuple[Any, ...]) -> str:
    """Host of a connect/create_connection argument (an ``(host, port, ...)`` address tuple)."""
    address = args[0] if args else None
    if isinstance(address, tuple) and address:
        return str(address[0])
    return str(address)


def forbid_network(monkeypatch: pytest.MonkeyPatch, *, allow_loopback: bool = False) -> list[str]:
    """Record every connect attempt (host) and fail it; loopback passes when allowed.

    The returned list holds the hosts of the *refused* attempts, so ``assert not opened`` means
    nothing tried to leave the machine. Loopback attempts that were allowed are not listed.
    """
    opened: list[str] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def refuse(host: str) -> None:
        opened.append(host)
        raise AssertionError(f"network used: {host}")

    def connect(self: socket.socket, address: Any) -> Any:
        host = _host_of((address,))
        if allow_loopback and host in LOOPBACK_HOSTS:
            return real_connect(self, address)
        return refuse(host)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        host = _host_of((address,))
        if allow_loopback and host in LOOPBACK_HOSTS:
            return real_connect_ex(self, address)
        return refuse(host)

    def create_connection(address: Any, *a: Any, **k: Any) -> Any:
        host = _host_of((address,))
        if allow_loopback and host in LOOPBACK_HOSTS:
            return real_create(address, *a, **k)
        return refuse(host)

    def urlopen(*a: Any, **k: Any) -> Any:
        return refuse(str(a[0]) if a else "urlopen")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return opened
