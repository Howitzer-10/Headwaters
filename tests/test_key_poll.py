"""HDW-131 / FU-122-2: the daemon timer polls keys that have a spent paid wall."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog.http import SourceError
from headwaters.core.key_poll import KeyPollResult, paid_walls, poll_paid_walls
from headwaters.core.quotas import QuotaTracker, credit_subject, key_subject, provider_subject
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_limits import active_walls, record_wall

BAL_OK = {"data": {"limit_remaining": 5.0, "usage": 1.0}}
BAL_SPENT = {"data": {"limit_remaining": 0, "usage": 9.0}}
PAID_KEY = credit_subject(key_subject("work"), "paid")
FREE_KEY = credit_subject(key_subject("work"), "free")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


class Http:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0

    def request_json(self, method: str, url: str, **kw: Any) -> Any:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _wall(db: Database, subject: str) -> None:
    record_wall(db, subject, "exhausted", until=time.time() + 3600)


def _subjects(db: Database) -> set[str]:
    return {r.subject for r in active_walls(db)}


def _poll(db: Database, http: Http, rules: Rules, secret: str | None = "sk-test") -> KeyPollResult:
    return poll_paid_walls(db, QuotaTracker(db, rules.walls), http, rules, lambda _p: secret)


def test_finds_key_and_provider_paid_walls_only(db: Database) -> None:
    _wall(db, PAID_KEY)
    _wall(db, credit_subject(provider_subject("openrouter"), "paid"))
    _wall(db, FREE_KEY)
    _wall(db, "model:x")
    found = {(w.alias, w.provider) for w in paid_walls(db)}
    assert found == {("work", "openrouter"), ("openrouter", "openrouter")}


def test_a_passing_check_lifts_the_paid_wall_and_leaves_the_free_wall(db: Database) -> None:
    _wall(db, PAID_KEY)
    _wall(db, FREE_KEY)
    http = Http(BAL_OK)
    result = _poll(db, http, Rules())
    assert result.lifted == ("work",)
    assert _subjects(db) == {FREE_KEY}
    assert http.calls == 1


def test_a_spent_balance_keeps_the_wall(db: Database) -> None:
    _wall(db, PAID_KEY)
    result = _poll(db, Http(BAL_SPENT), Rules())
    assert result.polled == ("work",)
    assert result.lifted == ()
    assert PAID_KEY in _subjects(db)


def test_a_failed_check_changes_nothing(db: Database) -> None:
    _wall(db, PAID_KEY)
    result = _poll(db, Http(SourceError("down")), Rules())
    assert result.polled == ()
    assert PAID_KEY in _subjects(db)


def test_a_free_wall_alone_is_never_polled(db: Database) -> None:
    _wall(db, FREE_KEY)
    http = Http()
    _poll(db, http, Rules())
    assert http.calls == 0
    assert _subjects(db) == {FREE_KEY}


@pytest.mark.parametrize(
    "rules",
    [Rules(airplane=True), Rules(private_lock=True), Rules(default_scope="local")],
)
def test_nothing_is_polled_under_airplane_a_private_lock_or_local_scope(
    db: Database, rules: Rules
) -> None:
    _wall(db, PAID_KEY)
    http = Http(BAL_OK)
    result = _poll(db, http, rules)
    assert result.blocked is not None
    assert http.calls == 0
    assert PAID_KEY in _subjects(db)


def test_a_key_without_a_secret_is_skipped(db: Database) -> None:
    _wall(db, PAID_KEY)
    http = Http(BAL_OK)
    assert _poll(db, http, Rules(), secret=None).polled == ()
    assert http.calls == 0
