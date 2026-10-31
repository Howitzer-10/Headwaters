"""HDW-113: strategies (cost_saver, quality_first, primary_first), breakdown and ranking."""

from __future__ import annotations

import pytest

from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.policy.scoring import rank_candidates
from headwaters.core.rules import Rules, StrategyWeights
from tests.test_policy_scoring import RULES, cand, hw, local, soft
from tests.test_policy_terms import total


def test_strategy_cost_weight_differs() -> None:
    c = cand("p", "paid", scores={"code": 95.0}, price_in=3.0, price_out=15.0)
    saver = total(c, strategy="cost_saver", tokens=10_000)
    quality = total(c, strategy="quality_first", tokens=10_000)
    primary = total(c, strategy="primary_first", tokens=10_000)
    assert saver.cost_penalty == pytest.approx(400 * 0.045)
    assert quality.cost_penalty == pytest.approx(40 * 0.045)
    assert primary.cost_penalty == 0.0
    assert saver.total < quality.total < primary.total


def test_strategy_primary_first_bonus_only_for_primary() -> None:
    assert total(cand(is_primary=True), strategy="primary_first").primary_bonus == 30.0
    assert total(cand(is_primary=False), strategy="primary_first").primary_bonus == 0.0
    assert total(cand(is_primary=True), strategy="cost_saver").primary_bonus == 0.0


def test_strategy_quality_first_halves_latency_and_residency() -> None:
    state = hw("prefer_cloud")
    c = local(residency=ResidencyInputs(False, 0.0, ("x",), 0.25))
    assert total(c, state=state, strategy="cost_saver").latency_penalty == 20.0
    assert total(c, state=state, strategy="quality_first").latency_penalty == 10.0
    assert total(c, strategy="cost_saver").evict_penalty == 25.0
    assert total(c, strategy="quality_first").evict_penalty == 12.5
    warm = local(residency=ResidencyInputs(True, 0.15, (), 0.0))
    assert total(warm, strategy="quality_first").warm_bonus == 7.5


def test_strategy_weights_come_from_rules() -> None:
    rules = Rules.model_validate(
        {"scoring": {"strategies": {"cost_saver": {"cost_weight": 1, "primary_bonus": 5}}}}
    )
    c = cand("p", "paid", price_in=1_000_000.0, price_out=0.0, is_primary=True)
    b = total(c, strategy="cost_saver", rules=rules, tokens=1)
    assert b.cost_penalty == 1.0
    assert b.primary_bonus == 5.0
    assert isinstance(rules.scoring.strategies.quality_first, StrategyWeights)


def test_breakdown_reports_floor_and_gap() -> None:
    ok = total(cand(scores={"code": 90.0}), soft("code", 2))  # floor 80
    assert (ok.floor, ok.meets_floor, ok.below_floor_by) == (80, True, 0.0)
    low = total(cand(scores={"code": 75.0}), soft("code", 2))
    assert (low.meets_floor, low.below_floor_by) == (False, 5.0)
    agentic = total(cand(scores={"code": 95.0, "agentic_tools": 60.0}), soft(agentic=True))
    assert agentic.meets_floor is False
    assert agentic.below_floor_by == 35.0  # floor 95


def test_rank_orders_by_total_then_local_then_id() -> None:
    a = cand("a", scores={"code": 80.0})
    b = cand("b", scores={"code": 90.0})
    loc = local("z-local", scores={"code": 80.0})
    s = soft(complexity=1)
    ranked = rank_candidates([a, b, loc], s, 100, "cost_saver", RULES)
    assert [c.id for c, _ in ranked] == ["b", "z-local", "a"]  # tie at 80: local first
    ranked = rank_candidates([cand("y"), cand("x")], s, 100, "cost_saver", RULES)
    assert [c.id for c, _ in ranked] == ["x", "y"]


def test_rules_schema_has_scoring() -> None:
    assert "scoring" in Rules.model_json_schema()["properties"]
