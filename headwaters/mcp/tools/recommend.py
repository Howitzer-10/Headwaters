"""``headwaters_recommend_model`` and ``headwaters_should_switch_host`` (HDW-205).

Both are dry runs on ``Router.plan()``: no model is called, no decision row or session pin is
written, and the task summary is analysed but never logged or stored. The switch commands use only
formats verified in the OpenCode docs (A-MCP-14): ``opencode --model provider/model`` and the
``/models`` picker; model ids are ``opencode/<name>`` (its free models), ``openrouter/<id>`` and
the id of a user-defined Ollama provider (the docs' example id is ``ollama``). Other hosts get the
Headwaters picker names (``headwaters/free`` ...), which any OpenAI-compatible host can use once
the gateway is configured. The user runs every command; nothing is executed here.
"""

from __future__ import annotations

from headwaters.core.engine import Engine
from headwaters.core.executor.types import Message, ToolSpec
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_plan import RoutePlan
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Scope
from headwaters.core.scope import ScopeError
from headwaters.core.state_limits import active_walls
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.schemas import ErrorInfo, SuggestionOut
from headwaters.mcp.tools.schemas_more import HostSwitch, RecommendResult, SwitchHostResult

__all__ = ["OLLAMA_PROVIDER_ID", "recommend", "should_switch", "switch_commands"]

OLLAMA_PROVIDER_ID = "ollama"  # the id the OpenCode docs use for a user-defined Ollama provider
_PICKER = {"auto": "headwaters/auto", "free": "headwaters/free", "local": "headwaters/local"}
_PROBE_TOOL: ToolSpec = {
    "type": "function",
    "function": {
        "name": "headwaters_probe",
        "description": "Stands in for the tools the host would send.",
        "parameters": {"type": "object", "properties": {}},
    },
}
_IMAGE_PART = {"type": "image_url", "image_url": {"url": "data:,"}}


def _opencode_model(candidate: Candidate) -> str:
    """The model id as OpenCode names it, from the model's source provider."""
    row = candidate.row
    if candidate.is_local:
        return f"{OLLAMA_PROVIDER_ID}/{row.id}"
    if row.id.startswith("opencode/"):
        return row.id
    if row.provider == "opencode":
        return f"opencode/{row.id}"
    if row.id.startswith("openrouter/"):
        return row.id
    if row.provider == "openrouter":
        return f"openrouter/{row.id}"
    return row.id if "/" in row.id else f"{row.provider}/{row.id}"


def switch_commands(candidate: Candidate, scope: str) -> list[HostSwitch]:
    model = _opencode_model(candidate)
    out = [
        HostSwitch(host="opencode", command=f"opencode --model {model}", model_id=model),
        HostSwitch(
            host="opencode (running)", command=f"/models  (then pick {model})", model_id=model
        ),
    ]
    picker = _PICKER.get(scope)
    if picker is not None:
        out.append(
            HostSwitch(
                host="any host using the Headwaters gateway",
                command=f"set the model to {picker}",
                model_id=picker,
            )
        )
    return out


def _messages(task: str, vision: bool) -> list[Message]:
    if not vision:
        return [{"role": "user", "content": task}]
    return [{"role": "user", "content": [{"type": "text", "text": task}, _IMAGE_PART]}]


async def plan_for(
    engine: Engine, tc: ToolContext, task: str, scope: Scope | None, *, vision: bool, tools: bool
) -> RoutePlan:
    request = RouteRequest(
        messages=_messages(task, vision),
        surface="mcp",
        scope=scope,
        tools=[_PROBE_TOOL] if tools else None,
    )
    return await engine.router.plan(request)


def _failure(exc: Exception) -> RecommendResult | None:
    if isinstance(exc, ScopeError):
        error = ErrorInfo(code=exc.code, message=exc.message, fix=exc.fix_hint)
        return RecommendResult(ok=False, error=error)
    if isinstance(exc, NoRouteError):
        tips = [SuggestionOut(code=s.code, text=s.text) for s in exc.suggestions]
        fix = tips[0].text if tips else "Run `headwaters status` to see what is blocking."
        error = ErrorInfo(code=exc.code, message=exc.message, fix=fix)
        return RecommendResult(ok=False, suggestions=tips, error=error)
    return None


async def recommend(
    engine: Engine,
    tc: ToolContext,
    task: str,
    scope: Scope | None,
    *,
    vision: bool = False,
    tools: bool = False,
) -> RecommendResult:
    try:
        plan = await plan_for(engine, tc, task, scope, vision=vision, tools=tools)
    except (ScopeError, NoRouteError) as exc:
        failed = _failure(exc)
        assert failed is not None
        return failed
    d = plan.decision
    chosen = plan.candidate(d.model)
    notes = ["Nothing was called or changed: this is a dry run."]
    if d.below_floor:
        notes.append("No model in scope reaches the quality floor; this is the best available.")
    return RecommendResult(
        ok=True,
        model=d.model,
        tier=d.tier,
        scope=plan.scope.scope,
        below_floor=d.below_floor,
        reasons=list(d.reasons),
        alternatives=list(d.fallbacks[:3]),
        switch=switch_commands(chosen, plan.scope.scope) if chosen is not None else [],
        suggestions=[SuggestionOut(code=s.code, text=s.text) for s in d.suggestions],
        notes=notes,
    )


async def should_switch(
    engine: Engine,
    tc: ToolContext,
    current_model: str,
    task: str | None,
    scope: Scope | None,
) -> SwitchHostResult:
    """Warn when the host's model is not the one Headwaters would pick (or cannot be used).

    ``current_model`` is compared by its last path segment, so ``openrouter/vendor/x:free`` and
    ``vendor/x:free`` are the same model. Without a task, a generic coding request is assumed.
    """
    summary = task.strip() if task and task.strip() else "general coding help"
    best = await recommend(engine, tc, summary, scope)
    if not best.ok:
        return SwitchHostResult(ok=False, recommended=best, error=best.error)
    same = _same_model(current_model, best.model or "")
    if same:
        return SwitchHostResult(
            ok=True, reason="The host is already on the model Headwaters would pick."
        )
    walled = _blocked(tc, current_model)
    severity = "warn" if walled else "info"
    reason = (
        f"{current_model} has a wall (out of credit or rate limited); "
        if walled
        else f"Headwaters would pick {best.model} for this work; "
    ) + "switching keeps you coding."
    return SwitchHostResult(
        ok=True,
        switch=True,
        severity=severity,
        reason=reason,
        recommended=best,
        notes=["Ask for a handoff (headwaters_handoff) first so the next model keeps the goal."],
    )


def _same_model(current: str, picked: str) -> bool:
    def tail(name: str) -> str:
        return name.strip().lower().removeprefix("opencode/").removeprefix("openrouter/")

    return tail(current) == tail(picked)


def _blocked(tc: ToolContext, current_model: str) -> bool:
    needle = current_model.strip().lower()
    return any(
        needle.endswith(w.subject.split(":", 1)[-1].lower()) for w in active_walls(tc.state())
    )
