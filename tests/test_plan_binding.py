"""HDW-146 (FU-206-8): a yes is bound to the question's model and cost.

``Gate.plan_for`` re-plans when the cached plan is gone (a client that retries after its question).
If that new plan is not the one the user was asked about, the old yes does not cover it: nothing
runs and the user is asked again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from headwaters.mcp.tools import responders
from headwaters.mcp.tools.guarded import Gate, Plan, PlanCache, execute, question_key, verdict
from headwaters.mcp.tools.schemas import ConfirmInfo
from tests.run_support import CODE_TASK, PAID, Rig, paid_rig, structured

REQUEST: dict[str, Any] = {
    "task": CODE_TASK,
    "want": "answer",
    "paths": [],
    "images": [],
    "scope": None,
    "meta": None,
    "verify_always": True,
}


def _info(
    model: str = PAID, cost: float = 2.0, reasons: tuple[str, ...] = ("spend",)
) -> ConfirmInfo:
    return ConfirmInfo(
        reasons=list(reasons),
        model=model,
        tier="paid",
        estimated_cost_usd=cost,
        confirm_above_usd=0.25,
    )


def _ctx() -> Any:
    """A request context of a host that can be asked (it declares elicitation)."""
    caps = SimpleNamespace(elicitation=object())
    return SimpleNamespace(request_context=object(), client_capabilities=caps)


def test_the_question_key_names_model_cost_and_reasons() -> None:
    assert question_key(_info()) == question_key(_info())
    assert question_key(_info(model="other")) != question_key(_info())
    assert question_key(_info(cost=3.0)) != question_key(_info())
    assert question_key(_info(reasons=("spend", "scope_loosening"))) != question_key(_info())
    assert question_key(_info(cost=2.0001)) == question_key(_info())  # the words round to $0.001


def test_a_plan_that_is_still_cached_is_returned_as_it_is(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    gate = Gate(rig.context)
    ctx = _ctx()

    async def go() -> Plan:
        await gate.approve(ctx, responders.DO, force=False, **REQUEST)
        return await gate.plan_for(ctx, responders.DO, **REQUEST)

    plan = asyncio.run(go())
    assert plan.changed is False
    assert plan.confirm is not None


def test_a_replanned_call_that_matches_the_question_is_not_asked_again(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    gate = Gate(rig.context)
    ctx = _ctx()

    async def go() -> Plan:
        await gate.approve(ctx, responders.DO, force=False, **REQUEST)
        gate._plans.take(ctx.request_context)  # the cached plan is gone, the record is not
        return await gate.plan_for(ctx, responders.DO, **REQUEST)

    plan = asyncio.run(go())
    assert plan.changed is False  # same model and cost: the user's yes still covers it
    assert plan.confirm is not None


def test_a_replanned_call_with_another_model_or_cost_is_marked_changed(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    gate = Gate(rig.context)
    ctx = _ctx()

    async def go() -> Plan:
        await gate.approve(ctx, responders.DO, force=False, **REQUEST)
        gate._plans.take(ctx.request_context)
        with rig.db.write() as c:  # the price moves between the question and the retry
            c.execute("UPDATE models SET price_out = price_out * 2 WHERE id = ?", (PAID,))
        return await gate.plan_for(ctx, responders.DO, **REQUEST)

    plan = asyncio.run(go())
    assert plan.changed is True
    assert plan.confirm is not None


def test_a_changed_plan_does_not_run_on_the_old_yes_and_asks_again(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    gate = Gate(rig.context)
    ctx = _ctx()

    async def go() -> Any:
        await gate.approve(ctx, responders.DO, force=False, **REQUEST)
        gate._plans.take(ctx.request_context)
        with rig.db.write() as c:
            c.execute("UPDATE models SET price_out = price_out * 2 WHERE id = ?", (PAID,))
        plan = await gate.plan_for(ctx, responders.DO, **REQUEST)
        agreed = verdict(_accepted())
        return await execute(rig.context, responders.DO, plan, agreed, ctx, run_async=False)

    result = asyncio.run(go())
    data = structured(result)
    assert data["ok"] is False
    assert data["confirmation_required"] is True
    assert data["error"]["code"] == "plan_changed"
    assert result.is_error is True
    assert rig.cloud_calls == 0  # nothing ran and nothing was spent


def test_an_unchanged_plan_runs_on_the_users_yes(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    gate = Gate(rig.context)
    ctx = _ctx()

    async def go() -> Any:
        await gate.approve(ctx, responders.DO, force=False, **REQUEST)
        plan = await gate.plan_for(ctx, responders.DO, **REQUEST)
        return await execute(
            rig.context, responders.DO, plan, verdict(_accepted()), ctx, run_async=False
        )

    data = structured(asyncio.run(go()))
    assert data["ok"] is True
    assert rig.cloud_calls == 1


def test_a_plan_that_needs_no_yes_is_never_marked_changed(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # local only: nothing to confirm
    gate = Gate(rig.context)
    plan = asyncio.run(gate.plan_for(_ctx(), responders.DO, **REQUEST))
    assert plan.confirm is None
    assert plan.changed is False


def test_the_cache_forgets_a_question_once_it_has_been_read(tmp_path: Path) -> None:
    cache = PlanCache()
    owner = object()
    cache.put(owner, Plan(confirm=_info()))
    assert cache.asked(owner) == question_key(_info())
    assert cache.asked(owner) is None  # read once
    other = object()
    cache.put(other, Plan(confirm=None))
    assert cache.asked(other) is None  # a plan with no question records none


def _accepted() -> Any:
    from mcp.server.mcpserver import AcceptedElicitation

    from headwaters.mcp.tools.guarded import Agree

    return AcceptedElicitation(data=Agree(agree=True))
