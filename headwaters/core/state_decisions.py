"""Repositories for ``decisions`` and ``outcomes`` (metadata and token counts only)."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from headwaters.core.state import Database, dumps


@dataclass(frozen=True)
class DecisionRow:
    surface: str  # gateway | mcp | hook | cli
    scope: str
    strategy: str
    locks_applied: Sequence[str] = ()
    # FU-128-2: every lock in force, not only the ones that narrowed the scope this request.
    active_locks: Sequence[str] = ()
    offload_penalty: float | None = None  # FU-133-1: points lost to a model that spills to RAM
    session: str | None = None
    requirements: dict[str, Any] = field(default_factory=dict)
    chosen: str | None = None
    alternatives: Sequence[Any] = ()
    floor: float | None = None
    score: float | None = None
    below_floor: bool = False
    reasons: Sequence[str] = ()
    id: int | None = None
    ts: float | None = None


def insert_decision(db: Database, d: DecisionRow) -> int:
    with db.write() as c:
        cur = c.execute(
            "INSERT INTO decisions (ts, surface, session, scope, strategy, locks_applied,"
            " active_locks, offload_penalty, requirements_json, chosen, alternatives, floor, score,"
            " below_floor, reasons) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.ts or time.time(),
                d.surface,
                d.session,
                d.scope,
                d.strategy,
                dumps(list(d.locks_applied)),
                dumps(list(d.active_locks)),
                d.offload_penalty,
                dumps(d.requirements),
                d.chosen,
                dumps(list(d.alternatives)),
                d.floor,
                d.score,
                int(d.below_floor),
                dumps(list(d.reasons)),
            ),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid


def _decision(r: sqlite3.Row) -> DecisionRow:
    return DecisionRow(
        id=r["id"],
        ts=r["ts"],
        surface=r["surface"],
        session=r["session"],
        scope=r["scope"],
        strategy=r["strategy"],
        locks_applied=json.loads(r["locks_applied"]),
        active_locks=json.loads(r["active_locks"]),
        offload_penalty=r["offload_penalty"],
        requirements=json.loads(r["requirements_json"]),
        chosen=r["chosen"],
        alternatives=json.loads(r["alternatives"]),
        floor=r["floor"],
        score=r["score"],
        below_floor=bool(r["below_floor"]),
        reasons=json.loads(r["reasons"]),
    )


def get_decision(db: Database, decision_id: int) -> DecisionRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    return _decision(r) if r else None


def recent_decisions(db: Database, limit: int = 50) -> list[DecisionRow]:
    with db.read() as c:
        rows = c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
        return [_decision(r) for r in rows]


def latest_decision_ts(db: Database) -> float | None:
    """Epoch seconds of the most recent routed request, or ``None`` when none was ever routed.

    HDW-518: the keep-warm idle backoff needs "when did this device last do real work", and every
    routed request already writes one ``decisions`` row (indexed on ``ts``), so nothing new is
    recorded. It is any tier's request, not only local ones: a cloud request still means the person
    is at the machine.
    """
    with db.read() as c:
        row = c.execute("SELECT MAX(ts) FROM decisions").fetchone()
    value = row[0] if row is not None else None
    return float(value) if value is not None else None


@dataclass(frozen=True)
class OutcomeRow:
    decision_id: int
    status: str
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    counterfactual_usd: float | None = None  # nullable: unknown unless computed
    verify_result: str | None = None
    escalated_to: str | None = None
    user_feedback: str | None = None


_OUTCOME_COLUMNS = (
    "decision_id",
    "status",
    "latency_ms",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "counterfactual_usd",
    "verify_result",
    "escalated_to",
    "user_feedback",
)


def save_outcome(db: Database, o: OutcomeRow) -> None:
    values = tuple(getattr(o, col) for col in _OUTCOME_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in _OUTCOME_COLUMNS[1:])
    with db.write() as c:
        c.execute(
            f"INSERT INTO outcomes ({', '.join(_OUTCOME_COLUMNS)})"
            f" VALUES ({', '.join('?' * len(_OUTCOME_COLUMNS))})"
            f" ON CONFLICT(decision_id) DO UPDATE SET {updates}",
            values,
        )


def get_outcome(db: Database, decision_id: int) -> OutcomeRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM outcomes WHERE decision_id = ?", (decision_id,)).fetchone()
    return OutcomeRow(**dict(r)) if r else None


def set_user_feedback(db: Database, decision_id: int, feedback: str) -> bool:
    with db.write() as c:
        return (
            c.execute(
                "UPDATE outcomes SET user_feedback = ? WHERE decision_id = ?",
                (feedback, decision_id),
            ).rowcount
            > 0
        )
