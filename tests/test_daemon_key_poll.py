"""HDW-131: the daemon timer calls ``poll_key`` from the live rules and the key's environment."""

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path
from typing import Any

import pytest

from headwaters.core import paths
from headwaters.core.daemon_info import DaemonPorts
from headwaters.core.quotas import QuotaTracker, credit_subject, key_subject
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_limits import active_walls, record_wall
from headwaters.daemon import service
from headwaters.daemon.service import Daemon

PAID_KEY = credit_subject(key_subject("work"), "paid")
FREE_KEY = credit_subject(key_subject("work"), "free")


class FakeHttp:
    calls = 0

    def __init__(self, timeout: float = 0.0) -> None:
        pass

    def request_json(self, method: str, url: str, **kw: Any) -> Any:
        FakeHttp.calls += 1
        return {"data": {"limit_remaining": 5.0, "usage": 1.0}}


class Live:
    def __init__(self, rules: Rules) -> None:
        self.rules = rules

    def __call__(self) -> Rules:
        return self.rules


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(service, "UrllibHttp", FakeHttp)
    FakeHttp.calls = 0
    return open_database(tmp_path / "state.db")


def _daemon(db: Database, live: Live) -> Daemon:
    sock = socket.socket()
    return Daemon(db, sock, DaemonPorts(1, 2, 3), "tok", rules_source=live)


def _walls(db: Database) -> set[str]:
    return {r.subject for r in active_walls(db)}


def test_timer_polls_a_paid_wall_and_lifts_it_but_not_the_free_wall(db: Database) -> None:
    for subject in (PAID_KEY, FREE_KEY):
        record_wall(db, subject, "exhausted", until=time.time() + 3600)
    daemon = _daemon(db, Live(Rules()))
    result = daemon.key_poll_once()
    assert result.lifted == ("work",)
    assert FakeHttp.calls == 1
    assert _walls(db) == {FREE_KEY}
    assert daemon.last_key_poll is result


def test_timer_reads_the_lock_live_and_polls_nothing_under_it(db: Database) -> None:
    record_wall(db, PAID_KEY, "exhausted", until=time.time() + 3600)
    live = Live(Rules(private_lock=True))
    daemon = _daemon(db, live)
    assert daemon.key_poll_once().blocked is not None
    live.rules = Rules(airplane=True)
    assert daemon.key_poll_once().blocked is not None
    assert FakeHttp.calls == 0
    assert PAID_KEY in _walls(db)


def test_timer_is_scheduled_and_interval_follows_live_rules(db: Database) -> None:
    live = Live(Rules())
    daemon = _daemon(db, live)
    assert daemon.key_poll_interval() == 600.0
    live.rules = Rules.model_validate({"walls": {"key_poll_minutes": 5}})
    assert daemon.key_poll_interval() == 300.0
    assert "key_poll_once" in _task_sources()


def _task_sources() -> str:
    import inspect

    return inspect.getsource(Daemon.serve)


def test_poll_key_is_the_call_made(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    real = QuotaTracker.poll_key

    def spy(self: QuotaTracker, http: Any, api_key: str, **kw: Any) -> Any:
        seen.append(kw)
        return real(self, http, api_key, **kw)

    monkeypatch.setattr(QuotaTracker, "poll_key", spy)
    record_wall(db, PAID_KEY, "exhausted", until=time.time() + 3600)
    _daemon(db, Live(Rules())).key_poll_once()
    assert seen[0]["alias"] == "work"
    assert seen[0]["airplane"] is False
    assert seen[0]["local_lock"] is False
    assert asyncio.iscoroutinefunction(Daemon.serve)
