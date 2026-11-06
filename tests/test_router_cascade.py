"""HDW-517: the draft-and-verify cascade — one downward retry after a failed check."""

from __future__ import annotations

import time
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.executor.types import Delta
from headwaters.core.policy.filters import Candidate
from headwaters.core.quotas import model_subject
from headwaters.core.router import RouterConfig
from headwaters.core.router_types import VerifyOutcome
from headwaters.core.rules import RouterRules, Rules
from headwaters.core.scope import PAID_TIERS, tier_walk
from headwaters.core.state_decisions import get_outcome, recent_decisions
from headwaters.core.state_limits import record_wall
from headwaters.core.verify_basic import BasicVerifier
from headwaters.mcp.tools.verify import Verification
from tests.router_rig import Rig
from tests.router_support import (
    OK,
    Escalator,
    Ledger,
    Verifier,
    candidate,
    registry_with,
    requirements,
)

DRAFTER = "openrouter/drafter:free"
SIBLING = "openrouter/sibling:free"
LOCAL = "qwen-local"
LOCAL_ALT = "qwen-local-alt"
BAD = [Delta(content="this is bad output"), Delta(finish_reason="stop")]
BAD_AGAIN = [Delta(content="bad once more"), Delta(finish_reason="stop")]
PROBLEM = "bad_output: contains the word bad"


def cascade_rules(*, model: str = "", tasks: list[str] | None = None) -> Rules:
    return Rules(
        router=RouterRules(
            tier_order="descend",
            tier_direction="cloud_first",
            verify_cascade=True,
            verify_cascade_model=model,
            verify_cascade_tasks=["code"] if tasks is None else tasks,
        )
    )


def draft_and_local() -> list[Candidate]:
    return [candidate(DRAFTER, "free_cloud", 90.0), candidate(LOCAL, "local", 60.0)]


def test_cascade_off_is_byte_identical(tmp_path: Path) -> None:
    """The default is the old behaviour: one attempt, ``verify_failed``, no cascade field."""
    ledger = Ledger()
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD},
        [candidate(DRAFTER, "free_cloud", 90.0)],
        verifiers=[Verifier()],
        registry=registry_with(ledger=ledger),
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER]
    assert result.verify is not None
    assert result.verify.problems == (PROBLEM,)
    assert result.verify_status == "fail"
    assert [a.status for a in result.attempts] == ["verify_failed"]
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.status, outcome.verify_result) == ("verify_failed", "fail")
    # The new column is additive and null: nothing about the existing row moved.
    assert (outcome.escalated_to, outcome.cascade_to) == (None, None)
    assert ledger.events[0]["cascade_to"] is None
    assert len(recent_decisions(rig.db)) == 1
    assert not any("cascading" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    ("script", "verifiers", "calls", "model"),
    [
        (BAD, [Verifier()], 2, LOCAL),
        (OK, [Verifier()], 1, DRAFTER),
        (BAD, [], 1, DRAFTER),
        (BAD, [BasicVerifier()], 1, DRAFTER),
    ],
)
def test_cascade_fires_only_on_real_fail(
    tmp_path: Path, script: list[Delta], verifiers: list[Any], calls: int, model: str
) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: script, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=verifiers,
    )
    result = rig.run(verify="always")
    assert len(rig.dispatch.calls) == calls
    assert result.decision.model == model
    # the cascade's answer is the good one, so the run is not reported as failed
    assert not result.verify_failed


def test_cascade_targets_strictly_lower_tier(tmp_path: Path) -> None:
    pool = [
        candidate(DRAFTER, "free_cloud", 90.0),
        candidate(SIBLING, "free_cloud", 85.0),  # same tier: not a legal cascade target
        candidate(LOCAL, "local", 60.0),
    ]
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, SIBLING: OK, LOCAL: OK},
        pool,
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    result = rig.run(verify="always")
    assert result.decision.model == LOCAL
    assert result.decision.tier == "local"
    assert SIBLING not in rig.dispatch.calls
    assert not result.verify_failed


def test_cascade_runs_after_escalation_returns_none(tmp_path: Path) -> None:
    """Upward wins: a plugin escalation is used instead of a cascade."""
    pool = [
        candidate(DRAFTER, "free_cloud", 90.0),
        # A higher score in the tier the walk reaches last, so the draft comes from free_cloud
        # and the escalation can go up (D10's stronger_than) without leaving the free tiers.
        candidate(LOCAL, "local", 95.0),
    ]
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, SIBLING: OK, LOCAL: OK},
        pool,
        rules=cascade_rules(),
        verifiers=[Verifier()],
        registry=registry_with(escalation=Escalator()),
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER, LOCAL]
    assert not result.verify_failed
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.escalated_to, outcome.cascade_to) == (LOCAL, None)


def test_cascade_single_hop(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: BAD_AGAIN},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER, LOCAL]
    assert [a.status for a in result.attempts] == ["verify_failed", "verify_failed"]
    assert result.verify_failed
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.status, outcome.cascade_to) == ("verify_failed", LOCAL)


def test_cascade_respects_max_attempts(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
        config=RouterConfig(max_attempts=1),
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER]
    assert result.verify_failed


@pytest.mark.parametrize("kw", [{"pin": DRAFTER}, {"pin": DRAFTER, "pin_strict": True}])
def test_cascade_refuses_pinned_request(tmp_path: Path, kw: dict[str, Any]) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    result = rig.run(verify="always", **kw)
    assert rig.dispatch.calls == [DRAFTER]
    assert result.verify_failed


def test_cascade_skips_walled_tier(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    record_wall(rig.db, model_subject(LOCAL), "rate_limit", until=time.time() + 600)
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER]
    assert result.verify_failed
    assert any("no lower model" in reason for reason in result.reasons)


def test_cascade_never_repicks_failed_model(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    result = rig.run(verify="always")
    assert [a.model for a in result.attempts] == [DRAFTER, LOCAL]

    alone = tmp_path / "alone"
    alone.mkdir()
    solo = Rig(
        alone,
        {DRAFTER: BAD},
        [candidate(DRAFTER, "free_cloud", 90.0)],
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    assert solo.run(verify="always").verify_failed
    assert solo.dispatch.calls == [DRAFTER]


def test_cascade_not_in_streaming(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    events = rig.stream(verify="always")
    assert rig.dispatch.calls == [DRAFTER]
    final = events[-1].result
    assert final is not None
    assert final.decision.model == DRAFTER
    assert final.first_choice is None
    assert [a.model for a in final.attempts] == [DRAFTER]
    assert not any("cascading" in reason for reason in final.reasons)


def test_cascade_to_logged_separately_from_escalated_to(tmp_path: Path) -> None:
    ledger = Ledger()
    (tmp_path / "cascade").mkdir()
    cascaded = Rig(
        tmp_path / "cascade",
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
        registry=registry_with(ledger=ledger),
    )
    result = cascaded.run(verify="always")
    outcome = get_outcome(cascaded.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.escalated_to, outcome.cascade_to) == (None, LOCAL)
    assert ledger.events[0]["cascade_to"] == LOCAL

    escalated_ledger = Ledger()
    (tmp_path / "escalation").mkdir()
    escalated = Rig(
        tmp_path / "escalation",
        {DRAFTER: BAD, SIBLING: OK, LOCAL: OK},
        [
            candidate(DRAFTER, "free_cloud", 90.0),
            candidate(LOCAL, "local", 95.0),
        ],
        rules=cascade_rules(),
        verifiers=[Verifier()],
        registry=registry_with(escalation=Escalator(), ledger=escalated_ledger),
    )
    result = escalated.run(verify="always")
    outcome = get_outcome(escalated.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.escalated_to, outcome.cascade_to) == (LOCAL, None)
    assert escalated_ledger.events[0]["cascade_to"] is None


def test_cascade_forces_gate_when_verify_off(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    result = rig.run(verify="off")
    assert result.verify is not None
    assert result.verify.ran is True
    assert [a.status for a in result.attempts] == ["verify_failed", "ok"]
    assert result.decision.model == LOCAL


def test_cascade_honours_the_task_list(tmp_path: Path) -> None:
    """A task that opts in cascades; the same request with the default list does not."""
    listed = tmp_path / "listed"
    listed.mkdir()
    rig = Rig(
        listed,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(tasks=["reasoning"]),
        verifiers=[Verifier()],
        reqs=requirements(task="reasoning"),
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER, LOCAL]
    assert not result.verify_failed

    unlisted = tmp_path / "unlisted"
    unlisted.mkdir()
    rig = Rig(
        unlisted,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(tasks=["code"]),
        verifiers=[Verifier()],
        reqs=requirements(task="reasoning"),
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [DRAFTER]
    assert result.verify_failed


def test_cascade_named_model_or_nothing(tmp_path: Path) -> None:
    named = tmp_path / "named"
    named.mkdir()
    rig = Rig(
        named,
        {DRAFTER: BAD, LOCAL: OK, LOCAL_ALT: OK},
        [*draft_and_local(), candidate(LOCAL_ALT, "local", 70.0)],
        rules=cascade_rules(model=LOCAL_ALT),
        verifiers=[Verifier()],
    )
    assert rig.run(verify="always").decision.model == LOCAL_ALT

    walled = tmp_path / "named-walled"
    walled.mkdir()
    rig = Rig(
        walled,
        {DRAFTER: BAD, LOCAL: OK, LOCAL_ALT: OK},
        [*draft_and_local(), candidate(LOCAL_ALT, "local", 70.0)],
        rules=cascade_rules(model=LOCAL_ALT),
        verifiers=[Verifier()],
    )
    record_wall(rig.db, model_subject(LOCAL_ALT), "rate_limit", until=time.time() + 600)
    result = rig.run(verify="always")
    assert result.verify_failed
    assert rig.dispatch.calls == [DRAFTER], "a named target means that model or no cascade"


def test_cascade_reports_the_first_choice(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {DRAFTER: BAD, LOCAL: OK},
        draft_and_local(),
        rules=cascade_rules(),
        verifiers=[Verifier()],
    )
    result = rig.run(verify="always")
    assert result.first_choice == DRAFTER
    assert result.decision.model == LOCAL


def test_cascade_can_only_move_toward_a_tier_that_does_not_charge() -> None:
    """D7: on the only walk a cascade may use, nothing below free_cloud costs money.

    The load-time rule refuses ``verify_cascade`` outside ``cloud_first`` (see
    ``tests/test_rules.py::test_cascade_requires_cloud_first``); this pins the property that
    makes that rule sufficient: every tier below ``free_cloud`` in this walk is free.
    """
    walk = tier_walk("cloud_first")
    floor_rank = walk.index("free_cloud")
    assert not PAID_TIERS & set(walk[floor_rank + 1 :])
    assert set(walk[:floor_rank]) <= PAID_TIERS


def test_no_quality_score_field() -> None:
    """D7.1: what we emit is a check result, never a grade."""
    banned = ("score", "quality", "grade", "confidence", "rating")
    for cls in (VerifyOutcome, Verification):
        for name in (f.name for f in fields(cls)):
            assert not any(word in name for word in banned), f"{cls.__name__}.{name}"
