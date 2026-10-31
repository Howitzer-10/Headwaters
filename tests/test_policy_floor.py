"""HDW-113: quality floor, filter on battery, and agentic-tools requirement."""

from __future__ import annotations

import pytest

from headwaters.core.policy.filters import hard_filter
from headwaters.core.policy.scoring import floor_for, meets_floor
from headwaters.core.rules import Rules
from tests.test_policy_scoring import RULES, cand, hard, hw, local, scope, soft, why


def test_filter_battery_local_only_drops_cloud() -> None:
    res = hard_filter([cand("c"), local("l")], hard(), scope(), RULES, hw("local_only"))
    assert [c.id for c in res.kept] == ["l"]
    assert why(res, "c") == "on_battery_local_only"


def test_floor_base_plus_k_times_complexity() -> None:
    f = floor_for(soft("code", 3), RULES)  # 70 + 5*3
    assert (f.value, f.task_base, f.complexity_part) == (85, 70, 15)
    assert floor_for(soft("chat", 2), RULES).value == 50 + 3 * 2
    assert floor_for(soft("creative", 1), RULES).value == 53


def test_floor_high_risk_and_agentic_bonuses() -> None:
    assert floor_for(soft("code", 2, "high"), RULES).value == 70 + 10 + 10
    assert floor_for(soft("code", 2, "medium"), RULES).value == 80
    both = floor_for(soft("code", 2, "high", True), RULES)
    assert (both.risk_part, both.agentic_part, both.value) == (10, 15, 105)


def test_floor_values_come_from_rules() -> None:
    rules = Rules.model_validate(
        {
            "floors": {
                "code": {"base": 10, "per_complexity": 1},
                "agentic_bonus": 2,
                "high_risk_bonus": 3,
            }
        }
    )
    assert floor_for(soft("code", 4, "high", True), rules).value == 10 + 4 + 3 + 2


def test_floor_unknown_task_raises() -> None:
    with pytest.raises(ValueError, match="no floor"):
        floor_for(soft("nonsense"), RULES)


def test_meets_floor_and_agentic_tools() -> None:
    c = cand(scores={"code": 90.0, "agentic_tools": 80.0})
    assert meets_floor(c, soft("code", 2), 80)
    assert not meets_floor(c, soft("code", 2), 91)
    assert meets_floor(c, soft(agentic=True), 80)  # agentic_tools == floor passes
    assert not meets_floor(c, soft(agentic=True), 81)
    assert not meets_floor(cand(scores={"code": 99.0}), soft(agentic=True), 50)  # no tools score
    assert meets_floor(cand(scores={"code": 99.0}), soft(agentic=False), 50)
