"""Run scripted tasks for each (host profile, dispatcher) and score three metrics (v3.4 B2).

* ``tool_selection``: the dispatcher picked the expected tool.
* ``argument_validity``: of the correct picks, the arguments validate against the tool's
  ``inputSchema`` and carry every argument the task requires.
* ``relay_compliance``: of the calls that ran, what the dispatcher says it would do with the result
  is allowed by the result's ``relay`` field.

Rates are ``None`` when their denominator is zero. No prompt or answer text goes into a result.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from bench.host_eval.models import (
    ALLOWED_ACTIONS,
    Backend,
    Dispatcher,
    HostProfile,
    Task,
    ToolCall,
    ToolSpec,
)

__all__ = ["Summary", "TaskResult", "run_matrix", "run_profile", "summarize"]


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    chosen_tool: str | None
    selected: bool
    args_valid: bool | None  # None: not scored (wrong tool or no call)
    relay: str | None  # the relay value of the result, when the call ran
    relay_action: str | None
    relay_ok: bool | None  # None: the call did not run
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class Summary:
    host: str
    dispatcher: str
    tasks: int
    tool_selection: float | None
    argument_validity: float | None
    relay_compliance: float | None
    results: tuple[TaskResult, ...] = field(default=())


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def _argument_problems(task: Task, spec: ToolSpec, arguments: dict[str, Any]) -> list[str]:
    problems = [f"missing:{name}" for name in task.required_args if name not in arguments]
    validator = jsonschema.Draft202012Validator(dict(spec.input_schema))
    problems.extend(
        f"schema:{'/'.join(str(p) for p in e.absolute_path) or '$'}"
        for e in validator.iter_errors(arguments)
    )
    return problems


async def _run_task(
    profile: HostProfile,
    dispatcher: Dispatcher,
    backend: Backend,
    task: Task,
    specs: dict[str, ToolSpec],
    tools: Sequence[ToolSpec],
) -> TaskResult:
    call = await dispatcher.choose(profile, task, tools)
    if call is None or call.tool not in specs:
        return TaskResult(
            task.id,
            call.tool if call else None,
            False,
            None,
            None,
            None,
            None,
            ("no_call" if call is None else "unknown_tool",),
        )
    if call.tool != task.expected_tool:
        return TaskResult(task.id, call.tool, False, None, None, None, None, ("wrong_tool",))
    problems = _argument_problems(task, specs[call.tool], dict(call.arguments))
    if problems:
        return TaskResult(task.id, call.tool, True, False, None, None, None, tuple(problems))
    result = await backend.call(profile, ToolCall(call.tool, dict(call.arguments)))
    relay = result.get("relay")
    if not isinstance(relay, str):  # a tool without a relay field (status, scope ...): not scored
        return TaskResult(task.id, call.tool, True, True, None, None, None)
    action = await dispatcher.relay(profile, task, result)
    allowed = action in ALLOWED_ACTIONS.get(relay, frozenset())
    note = () if allowed else (f"relay:{relay}->{action}",)
    return TaskResult(task.id, call.tool, True, True, relay, action, allowed, note)


async def run_profile(
    profile: HostProfile, dispatcher: Dispatcher, backend: Backend, tasks: Sequence[Task]
) -> Summary:
    """Every task for one (host profile, dispatcher) pair, in order."""
    tools = await backend.list_tools(profile)
    specs = {t.name: t for t in tools}
    results = [await _run_task(profile, dispatcher, backend, t, specs, tools) for t in tasks]
    return summarize(profile, dispatcher.id, results)


def summarize(profile: HostProfile, dispatcher_id: str, results: Sequence[TaskResult]) -> Summary:
    selected = sum(r.selected for r in results)
    scored = [r for r in results if r.args_valid is not None]
    ran = [r for r in results if r.relay_ok is not None]
    return Summary(
        host=profile.id,
        dispatcher=dispatcher_id,
        tasks=len(results),
        tool_selection=_rate(selected, len(results)),
        argument_validity=_rate(sum(bool(r.args_valid) for r in scored), len(scored)),
        relay_compliance=_rate(sum(bool(r.relay_ok) for r in ran), len(ran)),
        results=tuple(results),
    )


async def run_matrix(
    profiles: Sequence[HostProfile],
    dispatchers: Sequence[Dispatcher],
    backend: Backend,
    tasks: Sequence[Task],
) -> list[Summary]:
    """The full (host profile x dispatcher) grid."""
    return [await run_profile(p, d, backend, tasks) for p in profiles for d in dispatchers]
