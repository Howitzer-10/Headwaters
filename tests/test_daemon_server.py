"""HDW-201: the daemon's HTTP surface: loopback-only checks, token auth, health, shutdown."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.gateway.server import build_app, is_loopback_authority

TOKEN = "t0ken-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Rig:
    def __init__(self) -> None:
        self.stopped = 0
        self.client = TestClient(
            build_app(
                token=TOKEN,
                health=lambda: {"pid": 42},
                status=lambda: {"pid": 42, "scope": "auto"},
                on_shutdown=self._stop,
            ),
            base_url="http://127.0.0.1:4002",
        )

    def _stop(self) -> None:
        self.stopped += 1

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(path, **kw)


@pytest.fixture
def rig() -> Rig:
    return Rig()


def test_health_is_public(rig: Rig) -> None:
    reply = rig.get("/health")
    assert reply.status_code == 200
    assert reply.json() == {"ok": True, "pid": 42}


def test_status_needs_the_token(rig: Rig) -> None:
    assert rig.get("/v1/status").status_code == 401
    assert rig.get("/v1/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert rig.get("/v1/status", headers={"Authorization": TOKEN}).status_code == 401
    ok = rig.get("/v1/status", headers=AUTH)
    assert ok.status_code == 200
    assert ok.json()["scope"] == "auto"


def test_the_401_does_not_leak_the_token_and_names_the_scheme(rig: Rig) -> None:
    reply = rig.get("/v1/status")
    assert reply.headers["www-authenticate"] == "Bearer"
    assert TOKEN not in reply.text


def test_shutdown_needs_the_token_and_post(rig: Rig) -> None:
    assert rig.client.post("/v1/shutdown").status_code == 401
    assert rig.stopped == 0
    assert rig.client.get("/v1/shutdown", headers=AUTH).status_code == 405
    reply = rig.client.post("/v1/shutdown", headers=AUTH)
    assert reply.status_code == 200
    assert reply.json()["stopping"] is True
    assert rig.stopped == 1


@pytest.mark.parametrize(
    "host", ["evil.example.com", "127.0.0.1.evil.com", "10.0.0.5:4002", "0.0.0.0:4002", ""]
)
def test_foreign_host_headers_are_refused_even_with_the_token(rig: Rig, host: str) -> None:
    reply = rig.get("/health", headers={"Host": host, **AUTH})
    assert reply.status_code == 421


@pytest.mark.parametrize("host", ["127.0.0.1:4002", "localhost:4002", "[::1]:4002", "localhost"])
def test_loopback_host_headers_are_accepted(rig: Rig, host: str) -> None:
    assert rig.get("/health", headers={"Host": host}).status_code == 200


def test_a_web_page_cannot_call_the_api(rig: Rig) -> None:
    reply = rig.get("/health", headers={"Origin": "https://evil.example.com", **AUTH})
    assert reply.status_code == 403
    same = rig.get("/health", headers={"Origin": "http://localhost:4002"})
    assert same.status_code == 200


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("127.0.0.1", True),
        ("127.0.0.1:4002", True),
        ("localhost:80", True),
        ("[::1]:4002", True),
        ("LOCALHOST", True),
        ("", False),
        ("example.com", False),
        ("127.0.0.1.example.com", False),
        ("192.168.0.1:4002", False),
        ("[::2]:4002", False),
        ("127.0.0.1:notaport", False),
    ],
)
def test_loopback_authority(value: str, expected: bool) -> None:
    assert is_loopback_authority(value) is expected
