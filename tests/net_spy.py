"""Socket-level network spy shared by the catalog updater and router tests (HDW-120)."""

from __future__ import annotations

import socket
import urllib.request
from typing import Any

import pytest

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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
