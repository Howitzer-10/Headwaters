"""HDW-512 slice B: ``models.overrides`` reaches routing, last, and without a restart.

The rules docstring always promised a user could correct a wrong catalog fact. Until now the
override was folded in once, at discovery time, so a rules edit only landed after a refresh, and
``tier``/``scores``/``tools_ok`` could not be corrected at all.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from headwaters.core.analyzer.requirements import HardRequirements, Requirements
from headwaters.core.model_overrides import apply_overrides, override_index
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import ModelOverride, Rules
from tests.router_rig import Rig
from tests.router_support import OK, candidate
from tests.test_policy_scoring import soft

BIG = "vendor/big:free"
SMALL = "vendor/small:free"
# A session pins the model that answered it, so each assertion is a fresh conversation: the
# override has to win on its own merits, not because the previous answer was evicted.
SESSION = "hdw512"


class Live:
    """The rules the next request reads: the daemon's watcher swaps it, a test just assigns."""

    def __init__(self, rules: Rules | None = None) -> None:
        self.rules = rules or Rules()

    def __call__(self) -> Rules:
        return self.rules


def with_overrides(**overrides: Any) -> Rules:
    return Rules.model_validate({"models": {"overrides": overrides}})


def pool() -> list[Candidate]:
    """Two free models, ten points apart: an override has to be worth more than that."""
    return [candidate(BIG, "free_cloud", 90.0), candidate(SMALL, "free_cloud", 80.0)]


def tool_requirements() -> Requirements:
    hard = HardRequirements(False, True, False, 1000)
    return Requirements(hard, soft("code", 2, "low"), 0.9, "t", 1000, False)


def rig(tmp_path: Path, rules: Rules | None = None, **kw: Any) -> Rig:
    return Rig(tmp_path, {BIG: OK, SMALL: OK}, pool(), rules=rules, **kw)


# ------------------------------------------------------------------------- the override applies


def test_no_override_leaves_the_candidates_untouched() -> None:
    cands = pool()
    assert apply_overrides(cands, Rules()) == tuple(cands)


def test_a_score_override_changes_the_answer(tmp_path: Path) -> None:
    live = Live()
    rigged = rig(tmp_path, rules_source=live)
    assert rigged.run(session_id="first").decision.model == BIG
    live.rules = with_overrides(**{SMALL: {"scores": {"code": 100}}})
    assert rigged.run(session_id="second").decision.model == SMALL


def test_a_tools_ok_override_removes_a_model_from_a_tool_request(tmp_path: Path) -> None:
    live = Live()
    rigged = rig(tmp_path, rules_source=live, reqs=tool_requirements())
    assert rigged.run(session_id="first").decision.model == BIG
    live.rules = with_overrides(**{BIG: {"tools_ok": False}})
    assert rigged.run(session_id="second").decision.model == SMALL


def test_a_context_override_rescues_a_context_starved_model(tmp_path: Path) -> None:
    big = candidate(BIG, "free_cloud", 90.0)
    starved = [replace(big, row=replace(big.row, ctx=500)), candidate(SMALL, "free_cloud", 80.0)]
    live = Live()
    rigged = Rig(tmp_path, {BIG: OK, SMALL: OK}, starved, rules_source=live)
    assert rigged.run(session_id="first").decision.model == SMALL  # BIG cannot hold it
    live.rules = with_overrides(**{BIG: {"ctx": 200_000}})
    assert rigged.run(session_id="second").decision.model == BIG


def test_a_tier_override_drops_the_stale_local_fit() -> None:
    local = candidate("qwen3:8b", "local", 90.0)
    assert local.fit is not None
    (moved,) = apply_overrides([local], with_overrides(**{"qwen3:8b": {"tier": "free_cloud"}}))
    assert moved.row.tier == "free_cloud"
    assert moved.fit is None  # a cloud model has no local fit, so a stale one cannot decide it


def test_an_override_matches_however_the_name_is_written() -> None:
    rules = with_overrides(**{"QWEN3:8B": {"tools_ok": False}})
    assert list(override_index(rules)) == ["qwen3:8b"]
    cands = apply_overrides([candidate("qwen3:8b:latest", "local", 90.0)], rules)
    assert cands[0].row.tools_ok is False


# ---------------------------------------------------------------- a rules edit needs no restart


def test_a_fresh_rules_edit_lands_on_the_next_decision(tmp_path: Path) -> None:
    """The daemon reads rules through a callable (the watcher swaps it), so does this."""
    live = Live()
    rigged = rig(tmp_path, rules_source=live)
    assert rigged.run(session_id="first").decision.model == BIG
    live.rules = with_overrides(**{SMALL: {"scores": {"code": 100}}})
    assert rigged.run(session_id="second").decision.model == SMALL  # no refresh, no restart
    live.rules = Rules()  # the override goes away again
    assert rigged.run(session_id="first").decision.model == BIG


# -------------------------------------------------------------------------- invalid is rejected


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"tier": "mars"}, "tier"),
        ({"tools_ok": "yes"}, "tools_ok"),
        ({"ctx": 0}, "ctx"),
        ({"scores": {"code": 200}}, "score"),
        ({"nonsense": 1}, "nonsense"),
    ],
)
def test_an_invalid_override_is_rejected_with_a_message(
    override: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        with_overrides(**{"m": override})
    assert message in str(caught.value)


def test_a_valid_override_round_trips_through_the_model() -> None:
    override = ModelOverride(
        tier="paid", scores={"code": 5}, tools_ok=False, json_ok=True, ctx=9000
    )
    rules = Rules.model_validate({"models": {"overrides": {"m": override.model_dump()}}})
    assert rules.models.overrides["m"] == override
