"""HDW-113: hard filter, quality floor and score terms. Every weight comes from rules."""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.analyzer.requirements import HardRequirements, SoftRequirements
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.state import BatteryState, GpuBusy, HardwareState
from headwaters.core.policy.filters import Candidate, FilterResult, hard_filter
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules, ScoringRules
from headwaters.core.scope import ALL_TIERS, FREE_TIERS, LOCAL_TIERS, ScopeDecision, TierName
from headwaters.core.state_models import ModelRow

RULES = Rules()
_ROW_KEYS = ("modalities", "tools_ok", "json_ok", "ctx", "price_in", "price_out")


def soft(
    task: str = "code", complexity: int = 2, risk: str = "low", agentic: bool = False
) -> SoftRequirements:
    return SoftRequirements(task, complexity, risk, agentic, (), False, False)


def hard(
    vision: bool = False, tools: bool = False, json: bool = False, min_ctx: int = 1000
) -> HardRequirements:
    return HardRequirements(vision, tools, json, min_ctx)


def scope(tiers: frozenset[TierName] = ALL_TIERS) -> ScopeDecision:
    return ScopeDecision("auto", tiers, "cost_saver", "agentic")


def cand(
    mid: str = "m", tier: str = "free_cloud", scores: dict[str, float] | None = None, **kw: Any
) -> Candidate:
    row_kw: dict[str, Any] = {
        "modalities": ["text"],
        "tools_ok": True,
        "json_ok": True,
        "ctx": 100_000,
    }
    for key in _ROW_KEYS:
        if key in kw:
            row_kw[key] = kw.pop(key)
    row = ModelRow(id=mid, tier=tier, provider="p", **row_kw)
    return Candidate(row, scores if scores is not None else {"code": 80.0}, **kw)


def hw(policy: str = "normal", busy: bool = False) -> HardwareState:
    return HardwareState(
        gpu=None,
        profile="custom",
        reserve_gb=2.0,
        reserve_source="override",
        budget_gb=10.0,
        battery=BatteryState(True, policy != "normal"),
        battery_policy=policy,  # type: ignore[arg-type]
        gpu_busy=GpuBusy(busy, 0, 0),
        local_suggestion="ok",
    )


def fit(status: str = "fits", reason: str = "ok") -> FitResult:
    return FitResult("m", status, None if status == "no_fit" else 8192, reason)  # type: ignore[arg-type]


def local(mid: str = "loc", **kw: Any) -> Candidate:
    kw.setdefault("fit", fit())
    return cand(mid, "local", **kw)


def why(res: FilterResult, mid: str) -> str:
    return next(r.reason for r in res.rejected if r.model_id == mid)


# ---- hard filter -------------------------------------------------------------------------


def test_filter_keeps_a_model_meeting_everything() -> None:
    res = hard_filter([cand("a")], hard(), scope(), RULES)
    assert [c.id for c in res.kept] == ["a"]
    assert not res.rejected


@pytest.mark.parametrize(
    ("need", "kw", "code"),
    [
        (hard(vision=True), {}, "no_vision"),
        (hard(tools=True), {"tools_ok": False}, "no_tools"),
        (hard(tools=True), {"tools_ok": None}, "no_tools"),
        (hard(json=True), {"json_ok": False}, "no_json"),
    ],
)
def test_filter_requirements(need: HardRequirements, kw: dict[str, Any], code: str) -> None:
    assert why(hard_filter([cand("a", **kw)], need, scope(), RULES), "a") == code


def test_filter_vision_accepts_image_modality() -> None:
    c = cand("a", modalities=["text", "image"])
    assert hard_filter([c], hard(vision=True), scope(), RULES).kept


def test_filter_context_needs_headroom() -> None:
    need = hard(min_ctx=10_000)  # needs 12_000 at the default 1.2x
    assert why(hard_filter([cand("a", ctx=11_999)], need, scope(), RULES), "a") == "ctx_too_small"
    assert hard_filter([cand("a", ctx=12_000)], need, scope(), RULES).kept
    assert why(hard_filter([cand("a", ctx=None)], need, scope(), RULES), "a") == "ctx_too_small"


def test_filter_headroom_comes_from_rules() -> None:
    rules = Rules(scoring=ScoringRules(ctx_headroom=2.0))
    res = hard_filter([cand("a", ctx=12_000)], hard(min_ctx=10_000), scope(), rules)
    assert why(res, "a") == "ctx_too_small"


def test_filter_availability() -> None:
    cooling = cand("w", availability=Availability("cooldown", 30.0))
    spent = cand("x", availability=Availability("exhausted"))
    soft_ = cand("s", availability=Availability("soft"))
    res = hard_filter([cooling, spent, soft_], hard(), scope(), RULES)
    assert [c.id for c in res.kept] == ["s"]
    assert why(res, "w") == "unavailable_cooldown"
    assert why(res, "x") == "unavailable_exhausted"


def test_filter_scope_tiers() -> None:
    paid, free, loc = cand("p", "paid"), cand("f", "free_cloud"), local("l")
    res = hard_filter([paid, free, loc], hard(), scope(FREE_TIERS), RULES)
    assert {c.id for c in res.kept} == {"f", "l"}
    assert why(res, "p") == "scope"
    res = hard_filter([paid, free, loc], hard(), scope(LOCAL_TIERS), RULES)
    assert [c.id for c in res.kept] == ["l"]


def test_filter_openrouter_auto_tier_uses_scope_tier() -> None:
    auto = cand("or/auto", "paid", scope_tier="openrouter_auto")
    assert hard_filter([auto], hard(), scope(frozenset({"paid"})), RULES).rejected
    assert hard_filter([auto], hard(), scope(frozenset({"openrouter_auto"})), RULES).kept


def test_filter_hardware_fit() -> None:
    no_data = cand("n", "local")
    too_big = local("b", fit=fit("no_fit", "weights_too_large"))
    offload = local("o", fit=fit("offload", "kv_exceeds_budget"))
    res = hard_filter([no_data, too_big, offload], hard(), scope(), RULES)
    assert [c.id for c in res.kept] == ["o"]
    assert why(res, "n") == "no_fit_data"
    assert why(res, "b") == "no_fit_weights_too_large"
