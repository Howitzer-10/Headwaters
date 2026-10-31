"""HDW-118: ``Router.plan`` is a dry run of steps 1-3 with no model call and no stored state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_plan import RoutePlan
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError
from headwaters.core.state_decisions import recent_decisions
from tests.router_rig import Rig
from tests.router_support import MESSAGES, OK, candidate


def rig(tmp_path: Path, rules: Rules | None = None) -> Rig:
    return Rig(
        tmp_path,
        {"free/a": OK, "local/b": OK},
        [candidate("free/a", "free_cloud", 90.0), candidate("local/b", "local", 85.0)],
        rules=rules,
    )


def plan(r: Rig, **kw: Any) -> RoutePlan:
    return asyncio.run(r.router.plan(RouteRequest(messages=MESSAGES, **kw)))


def test_plan_picks_without_calling_any_model(tmp_path: Path) -> None:
    r = rig(tmp_path)
    result = plan(r)
    assert result.decision.model == "free/a"
    assert result.decision.fallbacks == ("local/b",)
    assert r.dispatch.calls == []  # nothing was sent anywhere
    assert r.cloud_clients == 0  # and no cloud client was built


def test_plan_stores_nothing(tmp_path: Path) -> None:
    r = rig(tmp_path)
    plan(r)
    assert recent_decisions(r.db) == []


def test_plan_matches_what_run_would_pick(tmp_path: Path) -> None:
    r = rig(tmp_path)
    planned = plan(r)
    ran = r.run()
    assert planned.decision.model == ran.decision.model
    assert planned.decision.fallbacks == ran.decision.fallbacks


def test_plan_lists_what_the_filter_rejected(tmp_path: Path) -> None:
    result = plan(rig(tmp_path), scope="local")
    assert result.decision.model == "local/b"
    assert [(x.model_id, x.reason) for x in result.rejected] == [("free/a", "scope")]
    assert result.scope.tiers == frozenset({"local"})
    assert result.candidate("free/a") is not None
    assert result.candidate("nope") is None


def test_plan_raises_no_route_when_nothing_is_known(tmp_path: Path) -> None:
    r = Rig(tmp_path, {}, [])
    with pytest.raises(NoRouteError):
        plan(r)
    assert recent_decisions(r.db) == []


def test_plan_raises_scope_errors_and_does_not_log_them(tmp_path: Path) -> None:
    r = rig(tmp_path, Rules(private_lock=True))
    with pytest.raises(ScopeError) as caught:
        plan(r, scope="cloud")
    assert caught.value.code == "private_lock_conflict"
    assert recent_decisions(r.db) == []  # only ``handle`` logs early failures
    with pytest.raises(ScopeError):
        asyncio.run(r.router.run(RouteRequest(messages=MESSAGES, scope="cloud")))
    assert len(recent_decisions(r.db)) == 1  # the real path still logs, as before
