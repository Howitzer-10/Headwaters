"""Plan a model call, ask when it needs a yes, then run it now or as a background job (HDW-206).

``headwaters_do``, ``_ask`` and ``_think`` share this path:

1. ``build_plan`` prepares the request and estimates it (``Router.plan``: no model call). A request
   that has no route fails here, before anything is asked.
2. The call needs the user's yes when the expected spend is over ``budget.confirm_above_usd`` or
   it asks for a wider scope than ``default_scope``. The tool's resolver puts the question through
   MCP elicitation. A host that cannot be asked gets a ``confirmation_required`` result instead,
   and nothing runs or is spent until the call is repeated with ``force: true`` (D9). ``force`` is
   only honoured when the host cannot be asked: a host that can be asked is always asked.
3. A call whose expected time is over ``mcp.async_threshold_s`` (or that asked for ``async``)
   becomes a job: the row is in SQLite, the work is a task, and the answer is the ``job_id``.
   Everything else runs now and reports progress on its own request.

Local scope is unchanged by any of this: the plan is a dry run, a refusal is an error and nothing
here builds a cloud client. No request text is stored, logged or put in a question.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from mcp import types
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit
from pydantic import BaseModel, Field

from headwaters.core.policy.errors import NoRouteError
from headwaters.core.rules import Rules, Scope
from headwaters.core.scope import ScopeError
from headwaters.mcp.tools.background import Shaper, start_job
from headwaters.mcp.tools.context import ToolContext, ToolFailure
from headwaters.mcp.tools.estimate import Estimate, estimate_call
from headwaters.mcp.tools.failures import Failure
from headwaters.mcp.tools.flow import Prepared, prepare_request, run_prepared
from headwaters.mcp.tools.frontdoor import classify
from headwaters.mcp.tools.progress import Progress, Update
from headwaters.mcp.tools.render_run import render_confirmation, render_declined, render_job_started
from headwaters.mcp.tools.results import envelope
from headwaters.mcp.tools.schemas import ConfirmInfo, ErrorInfo
from headwaters.mcp.tools.scope_tool import loosens_scope

__all__ = [
    "FORCED",
    "NO_ASK",
    "NO_CHANNEL",
    "Agree",
    "Gate",
    "Plan",
    "PlanCache",
    "Responder",
    "build_plan",
    "confirm_info",
    "confirm_question",
    "execute",
    "verdict",
]

NO_ASK = "none"  # nothing needs the user's yes
FORCED = "forced"  # the host cannot be asked, and the call carries force: true
NO_CHANNEL = "no_channel"  # something needs the user's yes and the host cannot be asked

Verdict = Literal["agreed", "forced", "declined", "ask_user", "unasked"]


class Agree(BaseModel):
    agree: bool = Field(description="Tick to allow Headwaters to go ahead with this call.")


@dataclass(frozen=True)
class Plan:
    """The prepared request and its estimate, or the failure that stopped planning."""

    prepared: Prepared | None = None
    estimate: Estimate | None = None
    failure: Failure | None = None
    confirm: ConfirmInfo | None = None


def confirm_info(rules: Rules, estimate: Estimate, requested: Scope | None) -> ConfirmInfo | None:
    """What the user must agree to, or None when the call needs nobody's yes."""
    reasons: list[str] = []
    limit = rules.budget.confirm_above_usd
    if estimate.cost_usd > limit:
        reasons.append("spend")
    if requested is not None and loosens_scope(rules.default_scope, requested):
        reasons.append("scope_loosening")
    if not reasons:
        return None
    return ConfirmInfo(
        reasons=reasons,
        model=estimate.model,
        tier=estimate.tier,
        estimated_cost_usd=round(estimate.cost_usd, 6),
        confirm_above_usd=limit,
        requested_scope=requested,
        default_scope=rules.default_scope,
        retry_with={"force": True},
    )


def confirm_question(info: ConfirmInfo) -> str:
    """The question put to the user. Same state gives the same words (the SDK matches the answer
    to the question it asked)."""
    parts: list[str] = []
    if "spend" in info.reasons:
        parts.append(
            f"use {info.model} ({info.tier}) for about ${info.estimated_cost_usd or 0.0:.3f}, "
            f"above your ${info.confirm_above_usd or 0.0:.3f} limit"
        )
    if "scope_loosening" in info.reasons:
        parts.append(f"widen the scope from {info.default_scope} to {info.requested_scope}")
    return "A tool asks Headwaters to " + " and ".join(parts) + ". Allow it?"


async def build_plan(tc: ToolContext, responder: Responder, **request: Any) -> Plan:
    """Prepare and estimate. A failure (a bad path, no workspace, ...) is returned, not raised.

    A request the router refuses (a scope error, no candidate) keeps its prepared form and has no
    estimate: it goes on to ``router.run``, which raises the same refusal and logs it as the
    router always has (``Router.plan`` writes nothing).
    """
    try:
        prepared = await prepare_request(tc, responder.pipeline, **request)
    except Exception as exc:
        return Plan(failure=classify(exc, responder.tool))
    try:
        estimate = await estimate_call(prepared, tc.settings)
    except (ScopeError, NoRouteError):
        return Plan(prepared)
    except Exception as exc:
        return Plan(failure=classify(exc, responder.tool))
    confirm = confirm_info(tc.rules(), estimate, prepared.request.scope)
    return Plan(prepared, estimate, None, confirm)


def verdict(approval: object) -> Verdict:
    """Read the answer the tool's approval resolver produced.

    An ``AcceptedElicitation`` holds either a marker string (nobody needed to be asked, or the host
    could not be) or the user's own ``Agree``; a decline or a cancel is a no.
    """
    if not isinstance(approval, AcceptedElicitation):
        return "declined"
    data = approval.data
    if isinstance(data, Agree):
        return "agreed" if data.agree else "declined"
    if data == FORCED:
        return "forced"
    if data == NO_CHANNEL:
        return "ask_user"
    return "unasked"


@dataclass(frozen=True)
class Responder:
    """What differs between ``do``, ``ask`` and ``think``: the pipeline, the result model, how a
    finished outcome becomes a result, and how a failure is shown."""

    tool: str  # do | ask | think
    pipeline: str  # do | ask (think reads like ask)
    model: type[BaseModel]
    shape: Shaper
    fail: Callable[[Failure], types.CallToolResult]

    def needs_user(self, info: ConfirmInfo) -> types.CallToolResult:
        error = ErrorInfo(
            code="confirmation_required",
            message="This call needs the user's OK before it runs; nothing ran and nothing was "
            "spent.",
            fix="Ask the user. Only if they agree, repeat the call with force: true.",
        )
        fields = {"ok": False, "confirmation_required": True, "confirm": info, "error": error}
        return envelope(self.model.model_validate(fields), render_confirmation(info), is_error=True)

    def declined(self) -> types.CallToolResult:
        error = ErrorInfo(
            code="declined",
            message="The user said no, so nothing ran and nothing was spent.",
            fix="Do not repeat the call unless the user asks for it.",
        )
        fields = {"ok": False, "error": error}
        return envelope(self.model.model_validate(fields), render_declined(), is_error=True)

    def started(self, job_id: str, estimate: Estimate, notes: list[str]) -> types.CallToolResult:
        fields = {
            "ok": True,
            "job_id": job_id,
            "eta_s": round(estimate.duration_s, 1),
            "model": estimate.model,
            "tier": estimate.tier,
            "notes": notes,
        }
        text = render_job_started(job_id, estimate.duration_s)
        return envelope(self.model.model_validate(fields), text)


class PlanCache:
    """Hands a plan from a tool's resolver to the tool body of the same request.

    Keyed by the request's own context object and checked by identity, so a reused ``id`` never
    matches. Bounded, because a request that stops after its question never reaches its body.
    """

    def __init__(self, limit: int = 32) -> None:
        self._limit = limit
        self._items: OrderedDict[int, tuple[object, Plan]] = OrderedDict()

    def put(self, owner: object, plan: Plan) -> None:
        self._items[id(owner)] = (owner, plan)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def take(self, owner: object) -> Plan | None:
        entry = self._items.pop(id(owner), None)
        return entry[1] if entry is not None and entry[0] is owner else None


class Gate:
    """The approval step of the model tools: plan the call, and ask when it needs a yes."""

    def __init__(self, tc: ToolContext) -> None:
        self._tc = tc
        self._plans = PlanCache()

    async def approve(
        self, ctx: Context, responder: Responder, *, force: bool, **request: Any
    ) -> str | Elicit[Agree]:
        """The value of a tool's ``approval`` parameter (see ``verdict``).

        A host that can be asked is always asked, ``force`` or not: the flag only stands in for an
        answer when there is nobody to ask.
        """
        plan = await build_plan(self._tc, responder, **request)
        self._plans.put(ctx.request_context, plan)
        if plan.confirm is None:
            return NO_ASK
        caps = ctx.client_capabilities
        if caps is not None and caps.elicitation is not None:
            return Elicit(confirm_question(plan.confirm), Agree)
        return FORCED if force else NO_CHANNEL

    async def plan_for(self, ctx: Context, responder: Responder, **request: Any) -> Plan:
        """The plan the resolver made for this request (or a new one if it is gone)."""
        found = self._plans.take(ctx.request_context)
        return found if found is not None else await build_plan(self._tc, responder, **request)


def _go_ahead(plan: Plan, answer: Verdict) -> bool:
    """A call that needs a yes runs only on the user's own yes, or on ``force`` when nobody can
    be asked. A plan that needs nothing runs whatever the verdict says it was."""
    if plan.confirm is None:
        return answer not in ("declined",)
    return answer in ("agreed", "forced")


async def execute(
    tc: ToolContext,
    responder: Responder,
    plan: Plan,
    answer: Verdict,
    ctx: Context,
    *,
    run_async: bool,
) -> types.CallToolResult:
    """Run the planned call: refuse, start a background job, or run now with progress."""
    if plan.failure is not None:
        return responder.fail(plan.failure)
    if answer == "declined":
        return responder.declined()
    if plan.prepared is None:  # cannot happen without a failure
        raise ToolFailure("internal_error", "No request was prepared.", "Try again.")
    if not _go_ahead(plan, answer):
        assert plan.confirm is not None
        return responder.needs_user(plan.confirm)
    estimate = plan.estimate
    threshold = tc.rules().mcp.async_threshold_s
    slow = estimate is not None and estimate.duration_s > threshold
    if estimate is not None and tc.db is not None and (run_async or slow):
        try:
            job_id = await start_job(tc, responder.tool, plan.prepared, estimate, responder.shape)
        except Exception as exc:
            return responder.fail(classify(exc, responder.tool))
        return responder.started(job_id, estimate, list(plan.prepared.notes))
    return await _run_now(tc, responder, plan, ctx, estimate)


async def _run_now(
    tc: ToolContext, responder: Responder, plan: Plan, ctx: Context, estimate: Estimate | None
) -> types.CallToolResult:
    assert plan.prepared is not None

    async def to_host(update: Update) -> None:
        # ``progress`` must rise with every notification: the step counter does. A host that did
        # not ask for progress gets nothing (the SDK makes this a no-op).
        await ctx.report_progress(float(update.step), None, update.message)

    cold = estimate.model if estimate is not None and estimate.cold else None
    heartbeat = tc.settings.progress_heartbeat_s
    try:
        async with Progress([to_host], heartbeat_s=heartbeat, cold_model=cold) as progress:
            outcome = await run_prepared(tc, plan.prepared, progress)
    except Exception as exc:
        return responder.fail(classify(exc, responder.tool))
    model, text = responder.shape(outcome)
    return envelope(model, text)
