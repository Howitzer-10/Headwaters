"""Behavioural capability observations (HDW-512).

One row per model and capability: what was *observed*, not what the provider advertises. The
catalog keeps the advertisement (``models.tools_ok``); this table keeps the measurement, and the
candidate builder overlays it so a later refresh cannot resurrect a broken capability.

Only definitive outcomes are recorded here. A skipped or failed probe writes nothing, which is
why an unverified model never silently reads as capable.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from headwaters.core.state import Database

CAPABILITY_TOOLS = "tools"


@dataclass(frozen=True)
class ProbeRow:
    model_id: str
    capability: str
    ok: bool
    detail: str
    checked_at: float


def _probe(r: sqlite3.Row) -> ProbeRow:
    return ProbeRow(
        model_id=r["model_id"],
        capability=r["capability"],
        ok=bool(r["ok"]),
        detail=r["detail"],
        checked_at=r["checked_at"],
    )


def record_probe(
    db: Database,
    model_id: str,
    capability: str,
    ok: bool,
    detail: str,
    *,
    now: float | None = None,
) -> None:
    """Store (or replace) the observation for one model and capability. Idempotent."""
    with db.write() as c:
        c.execute(
            "INSERT INTO capability_probes (model_id, capability, ok, detail, checked_at)"
            " VALUES (?,?,?,?,?) ON CONFLICT(model_id, capability) DO UPDATE SET"
            " ok=excluded.ok, detail=excluded.detail, checked_at=excluded.checked_at",
            (model_id, capability, int(ok), detail, time.time() if now is None else now),
        )


def get_probe(db: Database, model_id: str, capability: str) -> ProbeRow | None:
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM capability_probes WHERE model_id = ? AND capability = ?",
            (model_id, capability),
        ).fetchone()
    return _probe(r) if r else None


def probes(db: Database, capability: str | None = None) -> dict[str, ProbeRow]:
    """Every stored observation, keyed by model id (optionally for one capability)."""
    sql = "SELECT * FROM capability_probes"
    args: tuple[str, ...] = ()
    if capability is not None:
        sql += " WHERE capability = ?"
        args = (capability,)
    with db.read() as c:
        return {r["model_id"]: _probe(r) for r in c.execute(sql + " ORDER BY model_id", args)}


def clear_probes(db: Database, model_id: str, capability: str | None = None) -> int:
    sql = "DELETE FROM capability_probes WHERE model_id = ?"
    args: tuple[str, ...] = (model_id,)
    if capability is not None:
        sql += " AND capability = ?"
        args = (*args, capability)
    with db.write() as c:
        return c.execute(sql, args).rowcount
