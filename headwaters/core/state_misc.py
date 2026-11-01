"""Repositories for ``sessions``, ``jobs``, ``handoffs`` and ``kv``."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from headwaters.core.state import Database, dumps

# ---------------------------------------------------------------- sessions


@dataclass(frozen=True)
class SessionRow:
    session_key: str
    pinned_model: str | None
    tier: str | None
    created: float
    last_switch: float | None
    handoff_packet_id: int | None


def get_session(db: Database, key: str) -> SessionRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM sessions WHERE session_key = ?", (key,)).fetchone()
    return SessionRow(**dict(r)) if r else None


def pin_session(
    db: Database,
    key: str,
    model: str | None,
    tier: str | None,
    *,
    handoff_packet_id: int | None = None,
) -> SessionRow:
    """Create the session or switch its pinned model (stamping ``last_switch``)."""
    now = time.time()
    with db.write() as c:
        c.execute(
            "INSERT INTO sessions (session_key, pinned_model, tier, created, handoff_packet_id)"
            " VALUES (?,?,?,?,?) ON CONFLICT(session_key) DO UPDATE SET"
            " pinned_model=excluded.pinned_model, tier=excluded.tier, last_switch=?,"
            " handoff_packet_id=excluded.handoff_packet_id",
            (key, model, tier, now, handoff_packet_id, now),
        )
        r = c.execute("SELECT * FROM sessions WHERE session_key = ?", (key,)).fetchone()
    return SessionRow(**dict(r))


# ---------------------------------------------------------------- jobs


@dataclass(frozen=True)
class JobRow:
    """One background job. ``message`` is a short fixed status line (a stage or a model id),
    ``error_json`` the structured failure of a job that did not finish. Never request text."""

    job_id: str
    status: str
    progress: float
    result_ref: str | None
    created: float
    updated: float
    tool: str | None = None
    message: str | None = None
    error_json: str | None = None


UNFINISHED_JOB_STATES = ("queued", "running")


def create_job(
    db: Database, job_id: str, status: str = "queued", *, tool: str | None = None
) -> JobRow:
    now = time.time()
    with db.write() as c:
        c.execute(
            "INSERT INTO jobs (job_id, status, progress, created, updated, tool)"
            " VALUES (?,?,0,?,?,?)",
            (job_id, status, now, now, tool),
        )
    return JobRow(job_id, status, 0.0, None, now, now, tool)


def update_job(
    db: Database,
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    result_ref: str | None = None,
    message: str | None = None,
    error_json: str | None = None,
) -> bool:
    """Update only the fields given."""
    sets = ["updated = ?"]
    args: list[str | float] = [time.time()]
    for column, value in (
        ("status", status),
        ("progress", progress),
        ("result_ref", result_ref),
        ("message", message),
        ("error_json", error_json),
    ):
        if value is not None:
            sets.append(f"{column} = ?")
            args.append(value)
    with db.write() as c:
        return (
            c.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", (*args, job_id)
            ).rowcount
            > 0
        )


def fail_unfinished_jobs(db: Database, error_json: str) -> int:
    """Mark every queued or running job failed (a daemon that stopped took its tasks with it)."""
    marks = ",".join("?" for _ in UNFINISHED_JOB_STATES)
    with db.write() as c:
        return c.execute(
            f"UPDATE jobs SET status = 'failed', error_json = ?, updated = ?"
            f" WHERE status IN ({marks})",
            (error_json, time.time(), *UNFINISHED_JOB_STATES),
        ).rowcount


def get_job(db: Database, job_id: str) -> JobRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return JobRow(**dict(r)) if r else None


# ---------------------------------------------------------------- handoffs


def add_handoff(db: Database, session: str, packet: dict[str, Any]) -> int:
    """Store a handoff packet (metadata/summary only; text-like keys are stripped)."""
    with db.write() as c:
        cur = c.execute(
            "INSERT INTO handoffs (session, packet_json, created) VALUES (?,?,?)",
            (session, dumps(packet), time.time()),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid


def get_handoff(db: Database, handoff_id: int) -> dict[str, Any] | None:
    with db.read() as c:
        r = c.execute("SELECT packet_json FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
    return json.loads(r["packet_json"]) if r else None


def latest_handoff_id(db: Database, session: str) -> int | None:
    with db.read() as c:
        r: sqlite3.Row | None = c.execute(
            "SELECT MAX(id) AS id FROM handoffs WHERE session = ?", (session,)
        ).fetchone()
    return None if r is None or r["id"] is None else int(r["id"])


# ---------------------------------------------------------------- kv


def kv_set(db: Database, key: str, value: Any) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO kv (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, dumps(value)),
        )


def kv_get(db: Database, key: str, default: Any = None) -> Any:
    with db.read() as c:
        r = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def kv_delete(db: Database, key: str) -> bool:
    with db.write() as c:
        return c.execute("DELETE FROM kv WHERE key = ?", (key,)).rowcount > 0
