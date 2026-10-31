"""Decision and outcome logging for the router (HDW-117). Metadata only.

One ``decisions`` row per request plus one ``outcomes`` row, and an event for each registered
``LedgerPlugin``. Prompts, answers and keys never reach this module. A storage failure is logged
and swallowed: routing must not fail because the database is busy.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from headwaters.core.analyzer.requirements import Requirements
from headwaters.core.executor.types import ExecResult
from headwaters.core.logging import get_logger
from headwaters.core.plugins import LedgerPlugin
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_types import Attempt, Surface, VerifyOutcome
from headwaters.core.state import Database
from headwaters.core.state_decisions import DecisionRow, OutcomeRow, insert_decision, save_outcome

_log = get_logger("router")
_TOKENS_PER_MILLION = 1_000_000.0


@dataclass(frozen=True)
class LogEntry:
    surface: Surface
    session: str
    scope: str
    strategy: str
    status: str  # ok | verify_failed | an error code
    latency_ms: int
    locks: Sequence[str] = ()
    requirements: Requirements | None = None
    decision: RouteDecision | None = None
    attempts: Sequence[Attempt] = ()
    notes: Sequence[str] = ()
    result: ExecResult | None = None
    verify: VerifyOutcome | None = None
    cost_usd: float | None = None
    escalated_to: str | None = None
    extra_reasons: Sequence[str] = field(default=())


def requirements_dict(req: Requirements | None) -> dict[str, Any]:
    return {} if req is None else asdict(req)


def verify_label(verify: VerifyOutcome | None) -> str | None:
    if verify is None:
        return None
    if not verify.ran:
        return "skipped"
    return "pass" if verify.passed else "fail"


def cost_usd(price_in: float | None, price_out: float | None, result: ExecResult) -> float:
    """Catalog prices are per million tokens; unpriced models cost 0."""
    tokens_in = result.usage.prompt_tokens or 0
    tokens_out = result.usage.completion_tokens or 0
    return (tokens_in * (price_in or 0.0) + tokens_out * (price_out or 0.0)) / _TOKENS_PER_MILLION


def _alternatives(entry: LogEntry) -> list[Any]:
    chosen = entry.decision.model if entry.decision else None
    tried: list[Any] = [
        {"model": a.model, "status": a.status}
        for a in entry.attempts
        if not (a.model == chosen and a.status in ("ok", "verify_failed"))
    ]
    if entry.decision is not None:
        tried.extend(entry.decision.fallbacks)
    return tried


def write(db: Database, entry: LogEntry) -> int | None:
    """Insert the decision and its outcome; return the decision id (None if storage failed)."""
    d = entry.decision
    reasons = [*(d.reasons if d else ()), *entry.notes, *entry.extra_reasons]
    row = DecisionRow(
        surface=entry.surface,
        scope=entry.scope,
        strategy=entry.strategy,
        locks_applied=list(entry.locks),
        session=entry.session,
        requirements=requirements_dict(entry.requirements),
        chosen=d.model if d else None,
        alternatives=_alternatives(entry),
        floor=d.floor if d else None,
        score=d.score if d else None,
        below_floor=d.below_floor if d else False,
        reasons=reasons,
    )
    usage = entry.result.usage if entry.result else None
    try:
        decision_id = insert_decision(db, row)
        save_outcome(
            db,
            OutcomeRow(
                decision_id=decision_id,
                status=entry.status,
                latency_ms=entry.latency_ms,
                tokens_in=usage.prompt_tokens if usage else None,
                tokens_out=usage.completion_tokens if usage else None,
                cost_usd=entry.cost_usd,
                verify_result=verify_label(entry.verify),
                escalated_to=entry.escalated_to,
            ),
        )
    except sqlite3.Error as exc:
        _log.warning("could not store routing decision (%s)", type(exc).__name__)
        return None
    return decision_id


def ledger_event(entry: LogEntry, decision_id: int | None) -> dict[str, object]:
    d = entry.decision
    usage = entry.result.usage if entry.result else None
    return {
        "decision_id": decision_id,
        "surface": entry.surface,
        "scope": entry.scope,
        "strategy": entry.strategy,
        "status": entry.status,
        "model": d.model if d else None,
        "tier": d.tier if d else None,
        "latency_ms": entry.latency_ms,
        "tokens_in": usage.prompt_tokens if usage else None,
        "tokens_out": usage.completion_tokens if usage else None,
        "cost_usd": entry.cost_usd,
        "attempts": len(entry.attempts),
        "verify": verify_label(entry.verify),
    }


def notify_ledgers(
    ledgers: Sequence[LedgerPlugin], entry: LogEntry, decision_id: int | None
) -> None:
    event: Mapping[str, object] = ledger_event(entry, decision_id)
    for ledger in ledgers:
        try:
            ledger.record(event)
        except Exception as exc:  # a plugin must never break routing
            _log.warning("ledger plugin failed (%s)", type(exc).__name__)
