"""HDW-401: one key source for the executor, the key-scoped walls and the key-poll timer.

A key saved with ``headwaters keys set work --provider openrouter`` must reach the provider call,
must name the wall ``key:work:paid`` after a 402, and must be the key the daemon's timer polls.
Every store here is built over ``FakeKeyring``: the Windows Credential Manager is never touched.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Any

import pytest

from headwaters.core import keys as keys_module
from headwaters.core import paths
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.daemon_info import DaemonPorts
from headwaters.core.engine import build_engine
from headwaters.core.executor.types import Delta
from headwaters.core.keys import KeyStore
from headwaters.core.quotas import QuotaTracker, credit_subject, key_subject
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_limits import record_wall
from headwaters.daemon import service
from headwaters.daemon.service import Daemon
from tests.executor_support import REPO_ROOT, ScriptedBackend, decision, make_executor, run_once
from tests.keys_support import ENV_SECRET, SECRET, FakeKeyring
from tests.router_support import NO_CREDIT
from tests.test_daemon_key_poll import FakeHttp, Live
from tests.test_router_walls import FREE, OK, PAID, Clock, rig, subjects

SAVED = "work"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> KeyStore:
    for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    found = KeyStore(Rules(), env={}, backend=FakeKeyring())
    found.save(SAVED, SECRET, provider="openrouter")
    return found


def test_the_provider_call_carries_the_stored_key(store: KeyStore) -> None:
    backend = ScriptedBackend([Delta(content="hi")])
    executor = make_executor(
        backend, api_key_for=store.api_key_for, key_alias_for=store.key_alias_for
    )
    run_once(executor, decision())
    assert backend.requests[0].api_key == SECRET
    assert executor.key_alias("openrouter") == SAVED


def test_a_402_on_a_stored_key_walls_the_alias(tmp_path: Path, store: KeyStore) -> None:
    clock = Clock()
    r = rig(
        tmp_path,
        {PAID: [NO_CREDIT], FREE: OK},
        clock=clock,
        key_alias_for=store.key_alias_for,
    )
    r.run()
    assert subjects(r, clock) == ["key:work:paid"]


def test_the_engine_takes_its_key_source_from_the_store(tmp_path: Path, store: KeyStore) -> None:
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    db: Database = open_database(tmp_path / "state.db")
    engine = build_engine(Rules(), catalog, "seed", db, key_store=store)
    executor = engine.router._executor  # the router holds the executor it was built with
    assert executor.key_alias("openrouter") == SAVED
    assert executor.key_alias("anthropic") is None  # nothing stored, nothing exported


def test_an_environment_key_keeps_the_provider_as_its_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", ENV_SECRET)
    # The engine builds its own store here, so keep even that one off the real credential store.
    monkeypatch.setattr(keys_module, "default_backend", lambda: FakeKeyring())
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    db: Database = open_database(tmp_path / "state.db")
    engine = build_engine(Rules(), catalog, "seed", db)
    assert engine.router._executor.key_alias("openrouter") == "openrouter"


def test_the_timer_polls_the_stored_key_and_never_stores_it(
    tmp_path: Path, store: KeyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(service, "UrllibHttp", FakeHttp)
    FakeHttp.calls = 0
    calls: list[str] = []
    real = QuotaTracker.poll_key

    def spy(self: QuotaTracker, http: Any, api_key: str, **kw: Any) -> Any:
        calls.append(api_key)
        return real(self, http, api_key, **kw)

    monkeypatch.setattr(QuotaTracker, "poll_key", spy)
    db: Database = open_database(tmp_path / "state.db")
    record_wall(db, credit_subject(key_subject(SAVED), "paid"), "exhausted", until=time.time() + 60)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV_SECRET)

    daemon = Daemon(
        db,
        socket.socket(),
        DaemonPorts(1, 2, 3),
        "tok",
        rules_source=Live(Rules()),
        keys=store,
    )
    result = daemon.key_poll_once()
    assert result.lifted == (SAVED,)
    assert calls == [SECRET]
    assert FakeHttp.calls == 1
    assert SECRET.encode() not in (tmp_path / "state.db").read_bytes()
    assert ENV_SECRET.encode() not in (tmp_path / "state.db").read_bytes()


def test_the_timer_skips_a_key_on_a_provider_it_cannot_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(service, "UrllibHttp", FakeHttp)
    FakeHttp.calls = 0
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    store.save("claude", ENV_SECRET, provider="anthropic")
    db: Database = open_database(tmp_path / "state.db")
    record_wall(
        db, credit_subject(key_subject("claude"), "paid"), "exhausted", until=time.time() + 60
    )
    daemon = Daemon(
        db, socket.socket(), DaemonPorts(1, 2, 3), "tok", rules_source=Live(Rules()), keys=store
    )
    assert daemon.key_poll_once().polled == ()
    assert FakeHttp.calls == 0
