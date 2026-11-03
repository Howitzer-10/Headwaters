"""HDW-129: escalation and the core verified retry go to a stronger model (F10, D10)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from headwaters.core.executor.types import Delta
from headwaters.core.policy.filters import Candidate
from headwaters.core.router import RouterConfig
from tests.router_rig import Rig
from tests.router_support import OK, Escalator, Verifier, candidate, registry_with, requirements

BAD = [Delta(content="this is bad output"), Delta(finish_reason="stop")]
CHEAP = "openrouter/cheap:free"
STRONGER = "openrouter/stronger:free"
WEAKER = "openrouter/weaker:free"
PAID = "anthropic/paid-model"
LOC_A = "local-a"
LOC_B = "local-b"
LOC_C = "local-c"


def _agentic(mid: str, code: float, agentic: float, **kw: float) -> Candidate:
    c = candidate(mid, "local", code, **kw)
    return replace(c, scores={"code": code, "agentic_tools": agentic})


def test_cheap_fails_then_a_stronger_model_passes(tmp_path: Path) -> None:
    # Floor 80 (code, complexity 2): both are below it, so the verification is mandatory.
    # STRONGER is slower, so CHEAP ranks first, yet STRONGER's task score is higher.
    pool = [
        candidate(CHEAP, "free_cloud", 75.0),
        candidate(STRONGER, "free_cloud", 78.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, STRONGER: OK}, pool, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP, STRONGER]
    assert result.decision.model == STRONGER
    assert not result.verify_failed
    assert [a.status for a in result.attempts] == ["verify_failed", "ok"]


def test_no_stronger_candidate_returns_the_original_answer_flagged(tmp_path: Path) -> None:
    pool = [candidate(CHEAP, "free_cloud", 75.0), candidate(WEAKER, "free_cloud", 60.0)]
    rig = Rig(tmp_path, {CHEAP: BAD, WEAKER: OK}, pool, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP]  # the weaker model is never a "retry"
    assert result.decision.model == CHEAP
    assert result.verify_failed
    assert result.result.content == "this is bad output"


def test_the_retry_is_counted_in_max_attempts(tmp_path: Path) -> None:
    pool = [
        candidate(CHEAP, "free_cloud", 75.0),
        candidate(STRONGER, "free_cloud", 78.0, expected_tps=10.0),
    ]
    rig = Rig(
        tmp_path,
        {CHEAP: BAD, STRONGER: OK},
        pool,
        verifiers=[Verifier()],
        config=RouterConfig(max_attempts=1),
    )
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP]
    assert result.verify_failed


def test_the_core_retry_happens_once_only(tmp_path: Path) -> None:
    pool = [
        candidate(CHEAP, "free_cloud", 70.0),
        candidate(STRONGER, "free_cloud", 75.0, expected_tps=10.0),
        candidate(WEAKER, "free_cloud", 79.0, expected_tps=1.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, STRONGER: BAD, WEAKER: OK}, pool, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP, STRONGER]
    assert result.verify_failed
    assert result.decision.model == STRONGER


def test_a_met_floor_has_no_core_retry_without_a_plugin(tmp_path: Path) -> None:
    pool = [
        candidate(CHEAP, "free_cloud", 90.0),
        candidate(STRONGER, "free_cloud", 95.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, STRONGER: OK}, pool, verifiers=[Verifier()])
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [CHEAP]  # verify was optional, so no retry (D10)
    assert result.verify_failed


def test_the_plugin_escalation_cannot_pick_a_weaker_model(tmp_path: Path) -> None:
    plugin = Escalator()
    pool = [candidate(CHEAP, "free_cloud", 90.0), candidate(WEAKER, "free_cloud", 85.0)]
    rig = Rig(
        tmp_path,
        {CHEAP: BAD, WEAKER: OK},
        pool,
        verifiers=[Verifier()],
        registry=registry_with(escalation=plugin),
    )
    result = rig.run(verify="always")
    assert rig.dispatch.calls == [CHEAP]
    assert result.verify_failed


def test_below_floor_local_retries_on_a_stronger_local_model(tmp_path: Path) -> None:
    # The agentic-local-scope-p2 shape: floor 70 + 5 + 15 = 90, agentic local models score below.
    reqs = requirements("code", complexity=1, agentic=True)
    pool = [
        _agentic(LOC_A, 88.0, 85.0),
        _agentic(LOC_B, 89.0, 86.0, expected_tps=10.0),
        candidate(PAID, "paid", 99.0, price_in=3.0, price_out=15.0, is_primary=True),
    ]
    rig = Rig(tmp_path, {LOC_A: BAD, LOC_B: OK}, pool, reqs=reqs, verifiers=[Verifier()])
    result = rig.run(scope="local")
    assert rig.dispatch.calls == [LOC_A, LOC_B]
    assert result.decision.model == LOC_B
    assert not result.verify_failed
    assert rig.cloud_clients == 0


def test_below_floor_local_with_nothing_stronger_is_flagged_verify_failed(tmp_path: Path) -> None:
    reqs = requirements("code", complexity=1, agentic=True)
    pool = [
        _agentic(LOC_A, 88.0, 85.0),
        _agentic(LOC_B, 80.0, 80.0, expected_tps=10.0),
        candidate(PAID, "paid", 99.0, price_in=3.0, price_out=15.0, is_primary=True),
    ]
    rig = Rig(tmp_path, {LOC_A: BAD}, pool, reqs=reqs, verifiers=[Verifier()])
    result = rig.run(scope="local")
    assert rig.dispatch.calls == [LOC_A]
    assert result.decision.model == LOC_A
    assert result.verify_failed
    assert rig.cloud_clients == 0


# HDW-154 (D10 refinement, FU-129-2): an agentic escalation also keeps tool-call strength.
AGENTIC = requirements("code", complexity=2, agentic=True)  # floor 80 + 15 agentic = 95
NO_TOOL_NOTE = "no stronger model keeps tool strength"


def _tool_strength(mid: str, tier: str, code: float, tools: float, **kw: Any) -> Candidate:
    c = candidate(mid, tier, code, **kw)
    return replace(c, scores={**c.scores, "agentic_tools": tools})


def test_agentic_escalation_keeps_an_equal_tool_score(tmp_path: Path) -> None:
    pool = [
        _tool_strength(CHEAP, "free_cloud", 75.0, 90.0),
        _tool_strength(STRONGER, "free_cloud", 78.0, 90.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, STRONGER: OK}, pool, reqs=AGENTIC, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP, STRONGER]
    assert result.decision.model == STRONGER
    assert not result.verify_failed


def test_agentic_escalation_skips_a_higher_task_score_with_weaker_tools(tmp_path: Path) -> None:
    pool = [
        _tool_strength(CHEAP, "free_cloud", 75.0, 90.0),
        _tool_strength(WEAKER, "free_cloud", 80.0, 60.0, expected_tps=10.0),
        _tool_strength(STRONGER, "free_cloud", 78.0, 95.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, STRONGER: OK}, pool, reqs=AGENTIC, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP, STRONGER]
    assert result.decision.model == STRONGER
    assert not result.verify_failed


def test_agentic_with_nothing_as_strong_at_tools_says_so(tmp_path: Path) -> None:
    pool = [
        _tool_strength(CHEAP, "free_cloud", 75.0, 90.0),
        _tool_strength(WEAKER, "free_cloud", 80.0, 60.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, WEAKER: OK}, pool, reqs=AGENTIC, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP]  # the weaker-tools model is never a retry
    assert result.decision.model == CHEAP
    assert result.verify_failed
    assert any(NO_TOOL_NOTE in r for r in result.reasons)


def test_a_non_agentic_request_still_escalates_on_a_weaker_tool_score(tmp_path: Path) -> None:
    pool = [
        _tool_strength(CHEAP, "free_cloud", 75.0, 75.0),
        _tool_strength(STRONGER, "free_cloud", 78.0, 10.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {CHEAP: BAD, STRONGER: OK}, pool, verifiers=[Verifier()])
    result = rig.run()
    assert rig.dispatch.calls == [CHEAP, STRONGER]
    assert not result.verify_failed


def test_local_scope_agentic_escalation_stays_local_and_keeps_tool_strength(
    tmp_path: Path,
) -> None:
    pool = [
        _tool_strength(LOC_A, "local", 88.0, 85.0),
        _tool_strength(LOC_B, "local", 90.0, 70.0, expected_tps=10.0),  # higher task, worse tools
        _tool_strength(LOC_C, "local", 89.0, 86.0, expected_tps=10.0),
    ]
    rig = Rig(tmp_path, {LOC_A: BAD, LOC_C: OK}, pool, reqs=AGENTIC, verifiers=[Verifier()])
    result = rig.run(scope="local")
    assert rig.dispatch.calls == [LOC_A, LOC_C]
    assert result.decision.model == LOC_C
    assert not result.verify_failed
    assert rig.cloud_clients == 0
