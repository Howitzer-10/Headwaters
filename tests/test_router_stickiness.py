"""HDW-134: core session stickiness (D11, F15, F16)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from headwaters.core.executor.types import Delta
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.policy.scoring import score_candidate
from headwaters.core.router_prep import with_session_model
from headwaters.core.state_misc import get_session, pin_session
from tests.router_rig import Rig
from tests.router_support import OK, RATE_LIMITED, Verifier, candidate
from tests.test_policy_scoring import RULES, soft

A = "openrouter/a:free"
B = "openrouter/b:free"
BAD = [Delta(content="this is bad output"), Delta(finish_reason="stop")]
KEY = "id:s1"


def _score(c):  # type: ignore[no-untyped-def]
    return score_candidate(c, soft(complexity=1), 1000, "cost_saver", RULES)


def test_a_cloud_session_model_gets_no_warm_bonus() -> None:
    cloud = with_session_model([candidate(A, "free_cloud", 80.0)], A)[0]
    b = _score(cloud)
    assert b.warm_bonus == 0.0
    assert b.session_bonus == RULES.scoring.session_bonus
    assert b.total == 80.0 + RULES.scoring.session_bonus


def test_a_cold_local_session_model_keeps_its_evict_penalty() -> None:
    cold = replace(
        candidate("loc", "local", 80.0), residency=ResidencyInputs(False, 0.0, ("x",), 0.25)
    )
    marked = with_session_model([cold], "loc")[0]
    assert marked.residency == cold.residency
    b = _score(marked)
    assert b.evict_penalty == 25.0
    assert b.warm_bonus == 0.0
    assert _score(cold).evict_penalty == 25.0


def test_other_models_and_no_session_are_untouched() -> None:
    pool = [candidate(A, "free_cloud", 80.0), candidate(B, "free_cloud", 80.0)]
    assert with_session_model(pool, None) == tuple(pool)
    marked = with_session_model(pool, A)
    assert (marked[0].is_session, marked[1].is_session) == (True, False)


def test_three_turns_keep_one_model_then_move_on_a_wall_and_on_verify_failed(
    tmp_path: Path,
) -> None:
    # B scores slightly higher than A, yet the session keeps A (small ranking change).
    pool = [candidate(A, "free_cloud", 90.0), candidate(B, "free_cloud", 92.0)]
    rig = Rig(tmp_path, {A: OK, B: OK}, pool, verifiers=[Verifier()])
    pin_session(rig.db, KEY, A, "free_cloud")
    first = rig.run(session_id="s1")
    second = rig.run(session_id="s1")
    assert (first.decision.model, second.decision.model) == (A, A)
    assert "kept session model " + A in first.decision.reasons[-2]
    # A wall on A: the session moves on, and the new model becomes the session model.
    walled = Rig(tmp_path / "w", {A: [RATE_LIMITED], B: OK}, pool)
    pin_session(walled.db, KEY, A, "free_cloud")
    moved = walled.run(session_id="s1")
    assert moved.decision.model == B
    session = get_session(walled.db, KEY)
    assert session is not None
    assert session.pinned_model == B


def test_verify_failed_does_not_pin_and_the_next_turn_moves_on(tmp_path: Path) -> None:
    pool = [candidate(A, "free_cloud", 90.0), candidate(B, "free_cloud", 92.0)]
    rig = Rig(tmp_path, {A: BAD, B: OK}, pool, verifiers=[Verifier()])
    pin_session(rig.db, KEY, A, "free_cloud")
    failed = rig.run(session_id="s1", verify="always")
    assert failed.decision.model == A
    assert failed.verify_failed
    session = get_session(rig.db, KEY)
    assert session is not None
    assert session.pinned_model is None
    nxt = rig.run(session_id="s1", verify="always")
    assert nxt.decision.model == B
    session = get_session(rig.db, KEY)
    assert session is not None
    assert session.pinned_model == B


def test_a_new_model_is_pinned_only_on_ok(tmp_path: Path) -> None:
    pool = [candidate(A, "free_cloud", 92.0), candidate(B, "free_cloud", 90.0)]
    rig = Rig(tmp_path, {A: BAD, B: OK}, pool, verifiers=[Verifier()])
    rig.run(session_id="fresh", verify="always")
    session = get_session(rig.db, "id:fresh")
    assert session is None or session.pinned_model is None


def test_a_session_model_below_the_floor_does_not_jump_the_queue(tmp_path: Path) -> None:
    pool = [candidate(A, "free_cloud", 60.0), candidate(B, "free_cloud", 90.0)]
    rig = Rig(tmp_path, {A: OK, B: OK}, pool)
    pin_session(rig.db, KEY, A, "free_cloud")
    result = rig.run(session_id="s1")
    assert result.decision.model == B
    assert not any("kept session model" in r for r in result.decision.reasons)
