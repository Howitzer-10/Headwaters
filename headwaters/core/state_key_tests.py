"""Repository for ``key_tests`` (HDW-503): the last answer a provider gave about one alias.

Nothing persisted a key-test result before this table existed: ``headwaters keys test`` printed it
and threw it away, so the dashboard had nothing to show for "is this key still good?". One row per
alias, replaced on every test, so a page can never show an old answer beside a live key. ``detail``
is what a ping already keeps - a host and an HTTP code - and never the key itself
(``headwaters.control.keys``).
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from headwaters.core.state import Database

__all__ = [
    "KeyTestRow",
    "clear_key_test",
    "get_key_test",
    "key_tests",
    "record_key_test",
    "record_key_tests",
]


@dataclass(frozen=True, slots=True)
class KeyTestRow:
    alias: str
    ts: float
    status: str  # ok | unauthorized | http_error | unreachable | blocked | unsupported
    detail: str


class Tested(Protocol):
    """What a ping result provides. Core cannot import ``control.keys``, so it is structural."""

    alias: str
    status: str
    detail: str


def record_key_test(
    db: Database,
    alias: str,
    status: str,
    detail: str = "",
    *,
    now: float | None = None,
) -> KeyTestRow:
    """Store one alias's answer, replacing whatever was there."""
    moment = time.time() if now is None else now
    with db.write() as c:
        c.execute(
            "INSERT INTO key_tests (alias, ts, status, detail) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (alias) DO UPDATE SET ts = excluded.ts, status = excluded.status, "
            "detail = excluded.detail",
            (alias, moment, status, detail),
        )
    return KeyTestRow(alias, moment, status, detail)


def record_key_tests(
    db: Database,
    results: Iterable[Tested],
    *,
    now: float | None = None,
) -> int:
    """Store a whole ``keys test`` run in one transaction. Returns the rows written."""
    moment = time.time() if now is None else now
    rows = [(r.alias, moment, r.status, r.detail) for r in results]
    if not rows:
        return 0
    with db.write() as c:
        c.executemany(
            "INSERT INTO key_tests (alias, ts, status, detail) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (alias) DO UPDATE SET ts = excluded.ts, status = excluded.status, "
            "detail = excluded.detail",
            rows,
        )
    return len(rows)


def get_key_test(db: Database, alias: str) -> KeyTestRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM key_tests WHERE alias = ?", (alias,)).fetchone()
    return KeyTestRow(**dict(r)) if r else None


def key_tests(db: Database) -> dict[str, KeyTestRow]:
    """Every recorded answer, by alias. The page looks up only the aliases it shows."""
    with db.read() as c:
        rows = c.execute("SELECT * FROM key_tests").fetchall()
    return {row["alias"]: KeyTestRow(**dict(row)) for row in rows}


def clear_key_test(db: Database, alias: str) -> bool:
    """Forget one alias's answer (a new or removed key has not been tested yet)."""
    with db.write() as c:
        cur = c.execute("DELETE FROM key_tests WHERE alias = ?", (alias,))
    return cur.rowcount > 0
