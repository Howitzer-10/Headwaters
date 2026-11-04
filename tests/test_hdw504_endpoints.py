"""HDW-504: the daemon's two endpoints for the tray, and the ``tray`` block of ``/v1/status``.

``POST /v1/scope`` writes through ``rules_edit.set_setting`` and then the same reload-and-report
path the CLI uses, so the tray, the CLI and the dashboard all read one answer. ``GET /v1/keys``
carries ``provider``, ``alias`` and ``source`` and nothing else.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.core import paths
from headwaters.core.catalog.schema import Catalog
from headwaters.core.daemon_info import DaemonPorts
from headwaters.core.keys import SOURCES, KeyStore
from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules
from headwaters.core.rules_io import load_rules, rules_path
from headwaters.core.state import Database, open_database
from headwaters.daemon.service import Daemon
from headwaters.gateway.server import build_app
from tests.keys_support import SECRET, BrokenKeyring, FakeKeyring
from tests.test_hdw514_registry import catalog_with, entry

TOKEN = "t0ken-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://127.0.0.1:4002"
ACMEAKE_ENV = "ACMEFAKE_API_KEY"


def _app(**kwargs: Any) -> TestClient:
    app = build_app(
        token=TOKEN,
        health=lambda: {"pid": 42},
        status=lambda: {"pid": 42},
        on_shutdown=lambda: None,
        **kwargs,
    )
    return TestClient(app, base_url=BASE)


# --------------------------------------------------------------------------- /v1/scope


def test_scope_posts_the_key_and_value_the_tray_chose() -> None:
    calls: list[tuple[str, object]] = []

    def write(key: str, value: object) -> dict[str, Any]:
        calls.append((key, value))
        return {"ok": True, "key": key, "value": value}

    client = _app(on_scope=write)
    reply = client.post("/v1/scope", json={"key": "default_scope", "value": "local"}, headers=AUTH)
    assert reply.status_code == 200
    assert reply.json()["ok"] is True
    assert calls == [("default_scope", "local")]


def test_scope_says_no_and_keeps_the_old_value_when_the_write_is_refused() -> None:
    client = _app(on_scope=lambda key, value: {"ok": False, "key": key, "reason": "no"})
    reply = client.post("/v1/scope", json={"key": "private_lock", "value": True}, headers=AUTH)
    assert reply.status_code == 409
    assert reply.json() == {"ok": False, "key": "private_lock", "reason": "no"}


def test_scope_reports_a_setting_no_surface_may_edit_as_a_bad_request() -> None:
    def write(key: str, value: object) -> dict[str, Any]:
        raise ValueError(f"not editable: {key}")

    reply = _app(on_scope=write).post(
        "/v1/scope", json={"key": "workspace", "value": "x"}, headers=AUTH
    )
    assert reply.status_code == 400
    assert reply.json()["reason"] == "not editable: workspace"


def test_scope_rejects_a_body_that_is_not_a_key_and_a_value() -> None:
    reply = _app(on_scope=lambda key, value: {"ok": True}).post(
        "/v1/scope", json={"key": "default_scope"}, headers=AUTH
    )
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "invalid_request"


def test_scope_needs_the_token() -> None:
    reply = _app(on_scope=lambda key, value: {"ok": True}).post(
        "/v1/scope", json={"key": "default_scope", "value": "local"}
    )
    assert reply.status_code == 401


def test_scope_is_not_supported_when_the_daemon_has_no_writer() -> None:
    reply = _app().post("/v1/scope", json={"key": "default_scope", "value": "local"}, headers=AUTH)
    assert reply.status_code == 501
    assert reply.json()["error"]["code"] == "not_supported"


# --------------------------------------------------------------------------- /v1/keys


def test_keys_carries_provider_alias_and_source_and_nothing_else() -> None:
    payload = {
        "providers": [{"provider": "acme", "alias": "work", "source": "credential store"}],
    }
    reply = _app(on_keys=lambda: payload).get("/v1/keys", headers=AUTH)
    assert reply.status_code == 200
    assert reply.json() == {"ok": True, "providers": payload["providers"]}
    for absent in ("env_var", "masked"):
        assert absent not in reply.text


def test_keys_needs_the_token_and_is_not_supported_without_a_reader() -> None:
    assert _app(on_keys=lambda: {"providers": []}).get("/v1/keys").status_code == 401
    assert _app().get("/v1/keys", headers=AUTH).status_code == 501


# --------------------------------------------------------------------------- the daemon


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated home with a rules.yaml the daemon reads from disk."""
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    config = tmp_path / "config"
    config.mkdir()
    (config / "rules.yaml").write_text("version: 1\ndefault_scope: auto\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def db(live: Path) -> Database:
    return open_database(live / "state.db")


def _daemon(db: Database, **over: Any) -> Daemon:
    # Every daemon in this file gets a store over the in-memory keyring: ``/v1/status`` carries the
    # tray block, which counts keys, and no test may reach the machine's real credential store.
    over.setdefault(
        "keys", KeyStore(Rules(), env={}, backend=FakeKeyring(), registry=default_registry())
    )
    return Daemon(
        db,
        socket.socket(),
        DaemonPorts(4002, 4001, 4004),
        TOKEN,
        rules_source=lambda: load_rules(rules_path()),
        **over,
    )


def _client(daemon: Daemon) -> TestClient:
    return TestClient(
        build_app(
            token=TOKEN,
            health=daemon.health,
            status=daemon.status,
            on_shutdown=lambda: None,
            on_scope=daemon.set_setting_now,
            on_keys=daemon.keys_payload,
        ),
        base_url=BASE,
    )


def test_scope_switch_really_writes_rules_yaml_and_reports_the_live_value(db: Database) -> None:
    client = _client(_daemon(db))
    reply = client.post("/v1/scope", json={"key": "default_scope", "value": "local"}, headers=AUTH)
    assert reply.status_code == 200
    body = reply.json()
    assert body["ok"] is True
    assert body["previous"] == "auto"
    assert body["default_scope"] == "local"
    assert "default_scope: local" in rules_path().read_text(encoding="utf-8")
    # And the dashboard's own read now says local too: one answer, not two.
    assert client.get("/v1/status", headers=AUTH).json()["scope"] == "local"


def test_a_private_lock_switch_is_written_and_shows_in_the_tray_block(db: Database) -> None:
    client = _client(_daemon(db))
    reply = client.post("/v1/scope", json={"key": "private_lock", "value": True}, headers=AUTH)
    assert reply.status_code == 200
    assert reply.json()["private_lock"] is True
    tray = client.get("/v1/status", headers=AUTH).json()
    assert tray["private_lock"] is True
    assert tray["tray"]["effective_scope"] == "local"


def test_the_tray_block_carries_exactly_the_fields_the_menu_and_icon_need(db: Database) -> None:
    tray = _client(_daemon(db)).get("/v1/status", headers=AUTH).json()["tray"]
    assert set(tray) == {
        "scope",
        "effective_scope",
        "tier",
        "warm_model",
        "walls",
        "last_status",
        "below_floor",
        "keys_set",
        "anonymous_provider",
    }
    assert tray["walls"] == []
    assert tray["keys_set"] == 0
    # A provider that needs no key is why an empty store is not "needs attention".
    assert tray["anonymous_provider"] is True


def test_a_broken_credential_store_does_not_break_the_status_read(db: Database) -> None:
    store = KeyStore(Rules(), env={}, backend=BrokenKeyring(), registry=default_registry())
    reply = _client(_daemon(db, keys=store)).get("/v1/status", headers=AUTH)
    assert reply.status_code == 200
    assert reply.json()["tray"]["keys_set"] == 0


def test_keys_reports_the_registrys_own_providers_with_no_secret(db: Database) -> None:
    catalog: Catalog = catalog_with(entry(name="acmefake", env_var=ACMEAKE_ENV))
    registry = Registry.from_catalog(catalog)
    store = KeyStore(Rules(), env={}, backend=FakeKeyring(), registry=registry)
    store.save("work", SECRET, provider="acmefake")
    daemon = _daemon(db, keys=store)
    reply = _client(daemon).get("/v1/keys", headers=AUTH)
    body = reply.json()
    assert body["ok"] is True
    names = [row["provider"] for row in body["providers"]]
    assert names == [p.name for p in store.providers()]
    assert "acmefake" in names
    assert all(set(row) == {"provider", "alias", "source"} for row in body["providers"])
    acme = next(row for row in body["providers"] if row["provider"] == "acmefake")
    assert acme == {"provider": "acmefake", "alias": "work", "source": "credential store"}
    assert SECRET not in reply.text
    assert ACMEAKE_ENV not in reply.text


def test_a_provider_with_no_key_says_not_set_and_one_with_a_key_says_where_from(
    db: Database,
) -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring(), registry=default_registry())
    store.save("work", SECRET, provider="openai")
    reply = _client(_daemon(db, keys=store)).get("/v1/keys", headers=AUTH)
    rows = reply.json()["providers"]
    openai = next(row for row in rows if row["provider"] == "openai")
    assert openai["alias"] == "work"
    assert openai["source"] == "credential store"
    assert {row["source"] for row in rows} <= set(SOURCES)
    assert len(rows) == len(store.providers())
