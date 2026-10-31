"""HDW-131: rules are read live, per request, by the router, executor and quota tracker."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from headwaters.core.executor.errors import LocalScopeViolation, ScopeViolation
from headwaters.core.executor.guard import enforce_scope
from headwaters.core.policy.filters import Candidate
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Rules
from headwaters.core.state import open_database
from tests.executor_support import decision
from tests.router_rig import Rig
from tests.router_support import MESSAGES, OK, candidate

FREE = "openrouter/free-model:free"
LOCAL = "qwen-local"
PAID = "anthropic/paid-model"


def pool() -> list[Candidate]:
    return [
        candidate(FREE, "free_cloud", 90.0),
        candidate(LOCAL, "local", 86.0),
        candidate(PAID, "paid", 96.0, price_in=3.0, price_out=15.0, is_primary=True),
    ]


class Live:
    """A mutable rules holder, like ``RulesManager.current`` after a file edit."""

    def __init__(self, rules: Rules) -> None:
        self.rules = rules

    def __call__(self) -> Rules:
        return self.rules


def _rig(tmp_path: Path, live: Live) -> Rig:
    return Rig(tmp_path, {FREE: OK, PAID: OK, LOCAL: OK}, pool(), rules_source=live)


def test_private_lock_toggled_between_requests_never_builds_a_cloud_client(
    tmp_path: Path,
) -> None:
    live = Live(Rules())
    rig = _rig(tmp_path, live)
    first = rig.run()
    assert first.decision.tier != "local"
    assert rig.cloud_clients == 1
    live.rules = Rules(private_lock=True)
    second = rig.run(messages=[{"role": "user", "content": "a different question"}])
    assert second.decision.model == LOCAL
    assert rig.cloud_clients == 1  # no new client
    assert rig.dispatch.calls[-1] == LOCAL


def test_lock_on_from_the_start_builds_no_cloud_client_even_after_it_is_lifted_and_set(
    tmp_path: Path,
) -> None:
    live = Live(Rules(private_lock=True))
    rig = _rig(tmp_path, live)
    assert rig.run().decision.model == LOCAL
    assert rig.cloud_clients == 0
    live.rules = Rules()
    live.rules = Rules(private_lock=True)
    assert rig.run(messages=[{"role": "user", "content": "again"}]).decision.model == LOCAL
    assert rig.cloud_clients == 0


def test_airplane_set_mid_run_blocks_the_next_cloud_call(tmp_path: Path) -> None:
    live = Live(Rules())
    rig = _rig(tmp_path, live)
    rig.run()
    cloud_calls = [m for m in rig.dispatch.calls if m != LOCAL]
    assert cloud_calls
    live.rules = Rules(airplane=True)
    again = rig.run(messages=[{"role": "user", "content": "another question"}])
    assert again.decision.model == LOCAL
    assert [m for m in rig.dispatch.calls if m != LOCAL] == cloud_calls


def test_executor_refuses_a_cloud_call_when_the_lock_arrives_after_the_decision(
    tmp_path: Path,
) -> None:
    """The decision was made on old rules; the executor re-checks the live ones."""
    live = Live(Rules())
    rig = _rig(tmp_path, live)
    plan = asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES)))
    assert plan.decision.tier != "local"
    live.rules = Rules(private_lock=True)
    executor = rig.router._executor
    with pytest.raises(LocalScopeViolation):
        asyncio.run(executor.run(plan.decision, [{"role": "user", "content": "x"}]))
    assert rig.cloud_clients == 0


def test_enforce_scope_rechecks_private_lock_and_airplane() -> None:
    cloud = decision("free_cloud", FREE)
    with pytest.raises(ScopeViolation):
        enforce_scope(cloud, Rules(private_lock=True))
    with pytest.raises(ScopeViolation):
        enforce_scope(cloud, Rules(airplane=True))
    enforce_scope(decision("local", LOCAL), Rules(private_lock=True, airplane=True))


def test_quota_tracker_reads_walls_settings_live(tmp_path: Path) -> None:
    live = Live(Rules())
    quotas = QuotaTracker(open_database(tmp_path / "s.db"), live)
    assert quotas._walls.key_poll_minutes == 10
    live.rules = Rules.model_validate({"walls": {"key_poll_minutes": 3}})
    assert quotas._walls.key_poll_minutes == 3
