"""Repositories for ``walls`` and ``quotas``. Each call is one short transaction."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass

from headwaters.core.state import Database


@dataclass(frozen=True)
class WallRow:
    subject: str  # model id, provider, or key alias
    kind: str
    since: float
    until: float | None
    consecutive_count: int
    last_error: str | None


def _wall(r: sqlite3.Row) -> WallRow:
    return WallRow(
        r["subject"], r["kind"], r["since"], r["until"], r["consecutive_count"], r["last_error"]
    )


def record_wall(
    db: Database, subject: str, kind: str, *, until: float | None, error: str | None = None
) -> WallRow:
    """Create the wall, or bump ``consecutive_count`` when the same wall is hit again."""
    with db.write() as c:
        c.execute(
            "INSERT INTO walls (subject, kind, since, until, consecutive_count, last_error)"
            " VALUES (?,?,?,?,1,?) ON CONFLICT(subject, kind) DO UPDATE SET"
            " until=excluded.until, last_error=excluded.last_error,"
            " consecutive_count=consecutive_count+1",
            (subject, kind, time.time(), until, error),
        )
        r = c.execute(
            "SELECT * FROM walls WHERE subject = ? AND kind = ?", (subject, kind)
        ).fetchone()
    return _wall(r)


def clear_wall(db: Database, subject: str, kind: str | None = None) -> int:
    with db.write() as c:
        if kind is None:
            cur = c.execute("DELETE FROM walls WHERE subject = ?", (subject,))
        else:
            cur = c.execute("DELETE FROM walls WHERE subject = ? AND kind = ?", (subject, kind))
        return cur.rowcount


def clear_walls(db: Database, subjects: Sequence[str], kinds: Sequence[str]) -> int:
    """Delete the walls of ``kinds`` for any of ``subjects`` in one transaction."""
    if not subjects or not kinds:
        return 0
    s_marks = ",".join("?" * len(subjects))
    k_marks = ",".join("?" * len(kinds))
    with db.write() as c:
        return c.execute(
            f"DELETE FROM walls WHERE subject IN ({s_marks}) AND kind IN ({k_marks})",
            (*subjects, *kinds),
        ).rowcount


def get_wall(db: Database, subject: str, kind: str) -> WallRow | None:
    """One wall row, active or expired."""
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM walls WHERE subject = ? AND kind = ?", (subject, kind)
        ).fetchone()
    return _wall(r) if r else None


def active_walls(db: Database, now: float | None = None) -> list[WallRow]:
    """Walls with no end time (hard) or an end time still in the future."""
    at = time.time() if now is None else now
    with db.read() as c:
        rows = c.execute(
            "SELECT * FROM walls WHERE until IS NULL OR until > ? ORDER BY subject, kind", (at,)
        )
        return [_wall(r) for r in rows]


@dataclass(frozen=True)
class QuotaRow:
    subject: str
    window: str
    count: int
    limit: int | None
    soft_exhausted: bool


def _quota(r: sqlite3.Row) -> QuotaRow:
    return QuotaRow(r["subject"], r["window"], r["count"], r["limit"], bool(r["soft_exhausted"]))


def add_quota_usage(db: Database, subject: str, window: str, n: int = 1) -> QuotaRow:
    """Atomic increment (safe across processes)."""
    with db.write() as c:
        c.execute(
            "INSERT INTO quotas (subject, window, count) VALUES (?,?,?)"
            " ON CONFLICT(subject, window) DO UPDATE SET count=count+excluded.count",
            (subject, window, n),
        )
        r = c.execute(
            "SELECT * FROM quotas WHERE subject = ? AND window = ?", (subject, window)
        ).fetchone()
    return _quota(r)


def set_quota_limit(
    db: Database, subject: str, window: str, *, limit: int | None, soft_exhausted: bool
) -> None:
    with db.write() as c:
        c.execute(
            'INSERT INTO quotas (subject, window, "limit", soft_exhausted) VALUES (?,?,?,?)'
            ' ON CONFLICT(subject, window) DO UPDATE SET "limit"=excluded."limit",'
            " soft_exhausted=excluded.soft_exhausted",
            (subject, window, limit, int(soft_exhausted)),
        )


def get_quota(db: Database, subject: str, window: str) -> QuotaRow | None:
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM quotas WHERE subject = ? AND window = ?", (subject, window)
        ).fetchone()
    return _quota(r) if r else None


def prune_quota_windows(db: Database, subject: str, prefix: str, keep: str) -> int:
    """Delete a subject's windows that start with ``prefix`` except ``keep`` (old time buckets)."""
    with db.write() as c:
        return c.execute(
            "DELETE FROM quotas WHERE subject = ? AND substr(window, 1, ?) = ? AND window != ?",
            (subject, len(prefix), prefix, keep),
        ).rowcount


def reset_quota_window(db: Database, window: str) -> int:
    with db.write() as c:
        return c.execute(
            "UPDATE quotas SET count = 0, soft_exhausted = 0 WHERE window = ?", (window,)
        ).rowcount
