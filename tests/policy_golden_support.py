"""Shared pieces for the HDW-114 golden table: a model pool, a scenario runner, assertions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from headwaters.core.analyzer.requirements import HardRequirements, Requirements
from headwaters.core.plugins import CURRENT_API_VERSION
from headwaters.core.policy.decide import decide
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.types import PolicyContext, PolicyRequest
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules
from headwaters.core.scope import LOCAL_TIERS, SCOPE_TIERS, ScopeDecision
from headwaters.core.state_models import ModelRow
from tests.test_policy_scoring import fit, hw, soft


class Escalator:
    api_version = CURRENT_API_VERSION

    def should_escalate(self, attempt: int, problems: Any) -> bool:
        return True


def _c(mid: str, tier: str, scores: dict[str, float], **kw: Any) -> Candidate:
    row_kw: dict[str, Any] = {"modalities": ["text"], "tools_ok": True, "json_ok": True}
    row_kw["ctx"] = kw.pop("ctx", 128_000)
    for key in ("price_in", "price_out"):
        if key in kw:
            row_kw[key] = kw.pop(key)
    row = ModelRow(id=mid, tier=tier, provider="prov", **row_kw)
    return Candidate(row, scores, **kw)


def pool() -> dict[str, Candidate]:
    """Default models. Code floor at complexity 2 is 80; at 3 it is 85; at 5 it is 95."""
    base = {"chat": 90.0, "agentic_tools": 60.0}
    return {
        "loc14": _c("loc14", "local", {"code": 85.0, **base}, fit=fit(), expected_tps=35.0),
        "loc7": _c("loc7", "local", {"code": 70.0, **base}, fit=fit(), expected_tps=60.0),
        "free1": _c("free1", "free_cloud", {"code": 82.0, "chat": 90.0, "agentic_tools": 70.0}),
        "free2": _c("free2", "free_cloud", {"code": 75.0, "chat": 90.0, "agentic_tools": 70.0}),
        "paid1": _c(
            "paid1",
            "paid",
            {"code": 95.0, "chat": 95.0, "agentic_tools": 96.0},
            price_in=3.0,
            price_out=15.0,
            is_primary=True,
        ),
        "orauto": _c(
            "orauto",
            "paid",
            {"code": 90.0, "chat": 90.0, "agentic_tools": 90.0},
            price_in=1.0,
            price_out=2.0,
            role="openrouter_auto",
        ),
        "orfree": _c("orfree", "free_cloud", {"code": 60.0, "chat": 60.0}, role="openrouter_free"),
        "prem": _c("prem", "paid", {"code": 99.0, "chat": 99.0}, role="premium"),
    }


def build(
    mods: dict[str, dict[str, Any]] | None, drop: tuple[str, ...], only: tuple[str, ...]
) -> list[Candidate]:
    out: list[Candidate] = []
    for mid, cand in pool().items():
        if mid in drop or (only and mid not in only):
            continue
        change = dict((mods or {}).get(mid, {}))
        row = change.pop("row", None)
        if row:
            cand = replace(cand, row=replace(cand.row, **row))
        out.append(replace(cand, **change))
    for mid, spec in (mods or {}).items():
        if mid.startswith("new:"):  # an extra model: {"tier":..., "scores":...}
            extra = dict(spec)  # never mutate the shared scenario table
            out.append(_c(mid[4:], extra.pop("tier"), extra.pop("scores"), **extra))
    return out


def run(s: dict[str, Any]) -> Any:
    """Run one scenario spec and return the ``RouteDecision`` or the ``NoRouteError``."""
    rules = Rules.model_validate(s.get("rules", {}))
    task = s.get("task", "code")
    reqs = Requirements(
        HardRequirements(
            s.get("vision", False),
            s.get("tools", False),
            s.get("json", False),
            s.get("min_ctx", 1000),
        ),
        soft(task, s.get("complexity", 2), s.get("risk", "low"), s.get("agentic", False)),
        0.9,
        "test",
        s.get("tokens", 1000),
        False,
    )
    name = s.get("scope", "auto")
    locks = tuple(s.get("locks", ()))
    tiers = LOCAL_TIERS if locks else SCOPE_TIERS[name]
    scope = ScopeDecision(name, tiers, s.get("strategy", "cost_saver"), "agentic", locks)
    has_hw = "battery" in s or "busy" in s
    state = hw(s.get("battery", "normal"), s.get("busy", False)) if has_hw else None
    cands = build(s.get("mods"), tuple(s.get("drop", ())), tuple(s.get("only", ())))
    esc = Escalator() if s.get("escalation") else None
    ctx = PolicyContext(cands, rules, state, esc)
    try:
        return decide(PolicyRequest(reqs, scope, s.get("pin"), s.get("force")), ctx)
    except NoRouteError as err:
        return err


def check(s: dict[str, Any], expect: dict[str, Any]) -> None:
    got = run(s)
    if "error" in expect:
        assert isinstance(got, NoRouteError), f"expected error, got {got}"
        assert got.code == expect["error"]
        codes = {x.code for x in got.suggestions}
        assert set(expect.get("sugg", ())) <= codes
        if "etas" in expect:
            assert got.etas == expect["etas"]
        return
    assert not isinstance(got, NoRouteError), f"unexpected error {got.code}"
    assert got.model == expect["model"], got.reasons
    for key, attr in (("rung", "rung"), ("below", "below_floor"), ("verify", "verify_required")):
        if key in expect:
            assert getattr(got, attr) == expect[key]
    if "sugg" in expect:
        assert {x.code for x in got.suggestions} == set(expect["sugg"])
    if "fallbacks" in expect:
        assert list(got.fallbacks[: len(expect["fallbacks"])]) == expect["fallbacks"]


EXHAUSTED = Availability("exhausted", None)
COOLDOWN = Availability("cooldown", 45.0)
