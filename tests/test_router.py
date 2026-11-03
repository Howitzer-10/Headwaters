"""HDW-117: router façade cascades and scope, end to end with mocked providers."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.executor.errors import ProviderError
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate
from headwaters.core.router import RouterConfig
from headwaters.core.router_types import RouteFailed
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError
from headwaters.core.state_decisions import recent_decisions
from tests.router_rig import Rig
from tests.router_support import NO_CREDIT, OK, RATE_LIMITED, candidate, registry_with

FREE = "openrouter/free-model:free"
LOCAL = "qwen-local"
PAID = "anthropic/paid-model"


def pool(free: float = 90.0, local: float = 86.0, paid: float = 96.0) -> list[Candidate]:
    return [
        candidate(FREE, "free_cloud", free),
        candidate(LOCAL, "local", local),
        candidate(PAID, "paid", paid, price_in=3.0, price_out=15.0, is_primary=True),
    ]


def test_free_429_cascades_to_local(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    result = rig.run()
    assert result.decision.model == LOCAL
    assert result.first_choice == FREE
    assert [a.status for a in result.attempts] == ["rate_limit", "ok"]
    assert rig.dispatch.calls == [FREE, LOCAL]


def test_free_429_leaves_a_cooldown_for_the_next_request(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    rig.run()
    again = rig.run(messages=[{"role": "user", "content": "another question entirely"}])
    assert again.decision.model == LOCAL  # the free model is cooling down, not retried
    assert rig.dispatch.calls == [FREE, LOCAL, LOCAL]


def test_paid_402_cascades_to_free(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {PAID: [NO_CREDIT], FREE: OK},
        pool(),
        registry=registry_with(paid=True),
        rules=Rules.model_validate({"strategy": "primary_first"}),
    )
    result = rig.run()
    assert result.decision.model == FREE
    assert result.first_choice == PAID
    assert [a.status for a in result.attempts] == ["exhausted", "ok"]


def test_local_scope_never_touches_the_cloud(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool())
    result = rig.run(scope="local")
    assert result.decision.model == LOCAL
    assert rig.cloud_clients == 0
    assert rig.dispatch.calls == [LOCAL]


def test_local_scope_failure_is_structured_not_a_cloud_fallback(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: [ProviderError(500, {}, "", kind="http")]}, pool())
    with pytest.raises(RouteFailed) as info:
        rig.run(scope="local")
    assert info.value.code == "no_route_left"
    assert info.value.as_dict()["attempts"] == [
        {"model": LOCAL, "tier": "local", "status": "provider_error"}
    ]
    assert rig.cloud_clients == 0
    assert rig.dispatch.calls == [LOCAL]


def test_private_lock_keeps_an_auto_request_local(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), rules=Rules(private_lock=True))
    assert rig.run().decision.model == LOCAL
    assert rig.cloud_clients == 0


def test_airplane_mode_removes_every_cloud_candidate(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), rules=Rules(airplane=True))
    assert rig.run().decision.model == LOCAL
    assert rig.cloud_clients == 0


def test_scope_error_is_raised_and_logged(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, pool(), rules=Rules(private_lock=True))
    with pytest.raises(ScopeError) as info:
        rig.run(scope="cloud")
    assert info.value.code == "private_lock_conflict"
    assert [r.chosen for r in recent_decisions(rig.db)] == [None]
    assert rig.dispatch.calls == []


def test_no_route_is_raised_as_is(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, pool())
    with pytest.raises(NoRouteError) as info:
        rig.run(pin="no-such-model")
    assert info.value.code == "pin_rejected"
    assert rig.dispatch.calls == []
    assert [r.chosen for r in recent_decisions(rig.db)] == [None]


def test_attempts_are_bounded(tmp_path: Path) -> None:
    models = [candidate(f"m{i}:free", "free_cloud", 90.0 - i) for i in range(5)]
    scripts = {m.id: [RATE_LIMITED] for m in models}
    rig = Rig(tmp_path, scripts, models, config=RouterConfig(max_attempts=2))
    with pytest.raises(RouteFailed) as info:
        rig.run()
    assert info.value.code == "attempts_exhausted"
    assert len(rig.dispatch.calls) == 2


def test_an_explicit_model_that_fails_still_fails_over(tmp_path: Path) -> None:
    """HDW-304: a pass-through model is a first choice, not a guarantee."""
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    result = rig.run(pin=FREE)
    assert result.decision.model == LOCAL
    assert result.first_choice == FREE
    assert [a.status for a in result.attempts] == ["rate_limit", "ok"]
    assert rig.dispatch.calls == [FREE, LOCAL]


def test_a_strict_pin_answers_from_that_model_or_not_at_all(tmp_path: Path) -> None:
    """HDW-304: ``x-headwaters-pin: true`` turns the failover off."""
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    with pytest.raises(RouteFailed) as info:
        rig.run(pin=FREE, pin_strict=True)
    assert info.value.code == "rate_limit"
    assert rig.dispatch.calls == [FREE]
    assert [r.chosen for r in recent_decisions(rig.db)] == [FREE]
