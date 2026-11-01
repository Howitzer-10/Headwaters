"""HDW-113: every score term (quality, complexity, warm, evict, latency, battery, GPU, cost)."""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.analyzer.requirements import SoftRequirements
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.scoring import ScoreBreakdown, estimate_cost_usd, score_candidate
from headwaters.core.quotas import Availability
from headwaters.core.rules import Rules, ScoringRules
from tests.test_policy_scoring import RULES, cand, fit, hw, local, soft


def total(
    c: Candidate,
    s: SoftRequirements | None = None,
    strategy: Any = "cost_saver",
    rules: Rules = RULES,
    state: Any = None,
    tokens: int = 1000,
) -> ScoreBreakdown:
    return score_candidate(c, s or soft(complexity=1), tokens, strategy, rules, state)


def test_score_quality_only() -> None:
    b = total(cand(scores={"code": 77.0}))
    assert b.total == 77.0
    assert b.quality == 77.0
    assert (b.warm_bonus, b.latency_penalty, b.evict_penalty, b.cost_penalty) == (0, 0, 0, 0)


def test_score_missing_task_score_is_zero() -> None:
    assert total(cand(scores={}), soft("code")).quality == 0.0


def test_score_complexity_penalty() -> None:
    b = total(cand(scores={"code": 80.0}), soft(complexity=4))
    assert b.complexity_penalty == pytest.approx(6.0)  # 10.0 * (4 - 1) * (100 - 80) / 100
    assert b.total == pytest.approx(74.0)
    rules = Rules(scoring=ScoringRules(complexity_penalty_per_level=1.0))
    got = total(cand(scores={"code": 80.0}), soft(complexity=4), rules=rules)
    assert got.total == pytest.approx(79.4)


def test_complexity_penalty_depends_on_the_model() -> None:
    hard_ask = soft(complexity=5)
    strong = total(cand("s", scores={"code": 95.0}), hard_ask)
    weak = total(cand("w", scores={"code": 60.0}), hard_ask)
    assert strong.complexity_penalty == pytest.approx(2.0)
    assert weak.complexity_penalty == pytest.approx(16.0)
    assert total(cand(scores={"code": 100.0}), hard_ask).complexity_penalty == 0.0
    assert total(cand(scores={"code": 60.0}), soft(complexity=1)).complexity_penalty == 0.0
    # A 5-point quality lead is wiped out by the harder penalty on the weaker model.
    assert weak.total + 5 < strong.total


def test_score_offload_penalty() -> None:
    fits = total(local(fit=fit("fits")))
    off = total(local(fit=fit("offload", "kv_exceeds_budget")))
    assert fits.offload_penalty == 0.0
    assert off.offload_penalty == 30.0
    assert off.total == fits.total - 30.0
    assert total(cand()).offload_penalty == 0.0  # cloud has no fit
    rules = Rules(scoring=ScoringRules(offload_penalty=7.0))
    assert total(local(fit=fit("offload")), rules=rules).offload_penalty == 7.0


def test_score_warm_bonus_and_evict_penalty() -> None:
    warm = local(residency=ResidencyInputs(True, 0.15, (), 0.0))
    cold = local(residency=ResidencyInputs(False, 0.0, ("x", "y"), 0.5))
    assert total(warm).warm_bonus == 15.0
    assert total(warm).total == 95.0
    assert total(cold).evict_penalty == 50.0
    assert total(cold).total == 30.0


def test_score_residency_scale_from_rules() -> None:
    rules = Rules(scoring=ScoringRules(residency_scale=10.0))
    warm = local(residency=ResidencyInputs(True, 0.15, (), 0.0))
    assert total(warm, rules=rules).warm_bonus == pytest.approx(1.5)


def test_score_latency_penalty_from_expected_tps() -> None:
    assert total(cand(expected_tps=60.0)).latency_penalty == 0.0  # faster than reference
    assert total(cand(expected_tps=30.0)).latency_penalty == 0.0
    assert total(cand(expected_tps=15.0)).latency_penalty == 5.0  # weight 5 * (30/15 - 1)
    assert total(cand(expected_tps=0.1)).latency_penalty == 20.0  # capped
    assert total(cand(expected_tps=None)).latency_penalty == 0.0


def test_score_battery_penalty_local_only_and_per_rule() -> None:
    state = hw("prefer_cloud")
    assert total(local(), state=state).latency_penalty == 20.0
    assert total(cand(), state=state).latency_penalty == 0.0  # cloud unaffected
    assert total(local(), state=hw("normal")).latency_penalty == 0.0  # AC or on_battery=normal
    assert total(local(), state=hw("local_only")).latency_penalty == 0.0
    rules = Rules(scoring=ScoringRules(on_battery_local_penalty=7.0))
    assert total(local(), state=state, rules=rules).latency_penalty == 7.0


def test_score_gpu_busy_penalty_local_only() -> None:
    assert total(local(), state=hw(busy=True)).latency_penalty == 15.0
    assert total(cand(), state=hw(busy=True)).latency_penalty == 0.0
    assert total(local(), state=hw("prefer_cloud", busy=True)).latency_penalty == 35.0


def test_score_cost_term() -> None:
    c = cand("paid1", "paid", price_in=3.0, price_out=15.0)
    assert estimate_cost_usd(c, 100_000, RULES) == pytest.approx(0.3 + 0.015)
    b = total(c, tokens=100_000)
    assert b.cost_penalty == pytest.approx(400 * 0.315)
    assert b.total < 0
    assert estimate_cost_usd(cand(), 100_000, RULES) == 0.0  # free / unpriced


def test_score_soft_quota_penalty() -> None:
    assert total(cand(availability=Availability("soft"))).soft_penalty == 10.0
    assert total(cand(availability=Availability("ok"))).soft_penalty == 0.0
