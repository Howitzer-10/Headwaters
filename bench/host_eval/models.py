"""Types of the host-eval harness: tasks, host profiles, the dispatcher and backend seams.

A *dispatcher* stands in for the host's model: given a task and the tool list it picks a tool and
arguments, and later says what it would do with the result (the relay contract). Real dispatcher
models plug in behind the same Protocol in HDW-705; this ticket ships only mocks.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

__all__ = [
    "ALLOWED_ACTIONS",
    "Backend",
    "Dispatcher",
    "HostProfile",
    "RelayAction",
    "Task",
    "ToolCall",
    "ToolSpec",
    "load_tasks",
]

RelayAction = Literal["apply_diff", "show_verbatim", "summarize"]

# What a host may do with a result, per the ``relay`` field (HDW-204).
ALLOWED_ACTIONS: dict[str, frozenset[str]] = {
    "apply_diff": frozenset({"apply_diff"}),
    "verbatim": frozenset({"show_verbatim"}),
    "summarize_ok": frozenset({"show_verbatim", "summarize"}),
}

TASKS_PATH = Path(__file__).with_name("tasks.json")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    expected_tool: str
    gold_arguments: Mapping[str, Any]
    required_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class HostProfile:
    """One host as the server sees it: its own id, and whether it asked for the advanced tools."""

    id: str
    label: str
    sees_advanced: bool = False


class Dispatcher(Protocol):
    id: str

    async def choose(
        self, profile: HostProfile, task: Task, tools: Sequence[ToolSpec]
    ) -> ToolCall | None: ...

    async def relay(
        self, profile: HostProfile, task: Task, result: Mapping[str, Any]
    ) -> RelayAction: ...


class Backend(Protocol):
    async def list_tools(self, profile: HostProfile) -> list[ToolSpec]: ...

    async def call(self, profile: HostProfile, call: ToolCall) -> dict[str, Any]: ...


def load_tasks(path: Path | None = None) -> list[Task]:
    """Read the scripted tasks (``tasks.json``); a bad file raises ``ValueError``."""
    raw = json.loads((path or TASKS_PATH).read_text(encoding="utf-8"))
    tasks: list[Task] = []
    for item in raw["tasks"]:
        tasks.append(
            Task(
                id=str(item["id"]),
                prompt=str(item["prompt"]),
                expected_tool=str(item["expected_tool"]),
                gold_arguments=dict(item["gold_arguments"]),
                required_args=tuple(item.get("required_args", ())),
            )
        )
    ids = [t.id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task id in tasks.json")
    return tasks
