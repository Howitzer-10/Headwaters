"""Repositories for ``models`` and ``scores``. Each call is one short transaction."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from headwaters.core.state import Database, dumps


@dataclass(frozen=True)
class ModelRow:
    id: str
    tier: str
    provider: str
    modalities: list[str]
    tools_ok: bool | None = None
    json_ok: bool | None = None
    ctx: int | None = None
    price_in: float | None = None
    price_out: float | None = None
    size_gb: float | None = None
    arch: dict[str, Any] | None = None
    source: str = ""
    last_seen: float = 0.0
    deprecated_by: str | None = None
    unscored: bool = False
    retains_data: bool = False
    warm: bool = False
    size_vram_gb: float | None = None
    # Can reason on request: Ollama ``capabilities: thinking``, OpenRouter
    # ``supported_parameters: reasoning`` (HDW-140). None = unknown, treated as not capable.
    reasoning_ok: bool | None = None


def _opt_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _opt_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def upsert_model(db: Database, m: ModelRow) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO models (id, tier, provider, modalities, tools_ok, json_ok, ctx,"
            " price_in, price_out, size_gb, arch_json, source, last_seen, deprecated_by,"
            " unscored, retains_data, warm, size_vram_gb, reasoning_ok)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET tier=excluded.tier, provider=excluded.provider,"
            " modalities=excluded.modalities, tools_ok=excluded.tools_ok,"
            " json_ok=excluded.json_ok, ctx=excluded.ctx, price_in=excluded.price_in,"
            " price_out=excluded.price_out, size_gb=excluded.size_gb,"
            " arch_json=excluded.arch_json, source=excluded.source,"
            " last_seen=excluded.last_seen, deprecated_by=excluded.deprecated_by,"
            " unscored=excluded.unscored, retains_data=excluded.retains_data,"
            " warm=excluded.warm, size_vram_gb=excluded.size_vram_gb,"
            " reasoning_ok=excluded.reasoning_ok",
            (
                m.id,
                m.tier,
                m.provider,
                dumps(m.modalities),
                _opt_int(m.tools_ok),
                _opt_int(m.json_ok),
                m.ctx,
                m.price_in,
                m.price_out,
                m.size_gb,
                dumps(m.arch or {}),
                m.source,
                m.last_seen or time.time(),
                m.deprecated_by,
                int(m.unscored),
                int(m.retains_data),
                int(m.warm),
                m.size_vram_gb,
                _opt_int(m.reasoning_ok),
            ),
        )


def set_model_tools_ok(db: Database, model_id: str, ok: bool) -> bool:
    """Record an observed tool capability (HDW-512). Returns False when the model is unknown.

    The column is what ``models list`` shows and what discovery re-advertises on refresh; the
    behavioural observation that decides routing lives in ``capability_probes``.
    """
    with db.write() as c:
        return (
            c.execute("UPDATE models SET tools_ok = ? WHERE id = ?", (int(ok), model_id)).rowcount
            > 0
        )


def delete_models(db: Database, model_ids: list[str]) -> int:
    """Remove models (and their scores). Returns how many model rows were removed."""
    if not model_ids:
        return 0
    marks = ",".join("?" * len(model_ids))
    with db.write() as c:
        c.execute(f"DELETE FROM scores WHERE model_id IN ({marks})", model_ids)
        return c.execute(f"DELETE FROM models WHERE id IN ({marks})", model_ids).rowcount


def _model(r: sqlite3.Row) -> ModelRow:
    return ModelRow(
        id=r["id"],
        tier=r["tier"],
        provider=r["provider"],
        modalities=json.loads(r["modalities"]),
        tools_ok=_opt_bool(r["tools_ok"]),
        json_ok=_opt_bool(r["json_ok"]),
        ctx=r["ctx"],
        price_in=r["price_in"],
        price_out=r["price_out"],
        size_gb=r["size_gb"],
        arch=json.loads(r["arch_json"]),
        source=r["source"],
        last_seen=r["last_seen"],
        deprecated_by=r["deprecated_by"],
        unscored=bool(r["unscored"]),
        retains_data=bool(r["retains_data"]),
        warm=bool(r["warm"]),
        size_vram_gb=r["size_vram_gb"],
        reasoning_ok=_opt_bool(r["reasoning_ok"]),
    )


def get_model(db: Database, model_id: str) -> ModelRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    return _model(r) if r else None


def list_models(db: Database, tier: str | None = None) -> list[ModelRow]:
    sql = "SELECT * FROM models"
    args: tuple[str, ...] = ()
    if tier is not None:
        sql += " WHERE tier = ?"
        args = (tier,)
    with db.read() as c:
        return [_model(r) for r in c.execute(sql + " ORDER BY id", args)]


@dataclass(frozen=True)
class ScoreRow:
    model_id: str
    task: str
    seed: float | None = None
    override: float | None = None
    learned_ema: float | None = None
    n_samples: int = 0


def set_score(db: Database, s: ScoreRow) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO scores (model_id, task, seed, override, learned_ema, n_samples)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(model_id, task) DO UPDATE SET"
            " seed=excluded.seed, override=excluded.override,"
            " learned_ema=excluded.learned_ema, n_samples=excluded.n_samples",
            (s.model_id, s.task, s.seed, s.override, s.learned_ema, s.n_samples),
        )


def get_score(db: Database, model_id: str, task: str) -> ScoreRow | None:
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM scores WHERE model_id = ? AND task = ?", (model_id, task)
        ).fetchone()
    return ScoreRow(**dict(r)) if r else None


def effective_scores(db: Database) -> dict[str, dict[str, float]]:
    """Score per model and task (0..100): user override, else learned average, else seed."""
    out: dict[str, dict[str, float]] = {}
    with db.read() as c:
        rows = c.execute("SELECT model_id, task, seed, override, learned_ema FROM scores")
        for r in rows:
            picks = (r["override"], r["learned_ema"], r["seed"])
            value = next((v for v in picks if v is not None), None)
            if value is not None:
                out.setdefault(r["model_id"], {})[r["task"]] = float(value)
    return out
