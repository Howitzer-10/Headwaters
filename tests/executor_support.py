"""Shared fakes for the executor tests (HDW-115). All HTTP and litellm calls are mocked."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import (
    BackendRequest,
    Delta,
    ExecOptions,
    ExecResult,
    ExecTarget,
    StreamEvent,
)
from headwaters.core.policy.scoring import ScoreBreakdown
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import ExecutorRules, Rules
from headwaters.core.scope import ALL_TIERS, LOCAL_TIERS, ScopeDecision, TierName
from headwaters.core.walls import WallClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
WALLS = WallClassifier(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH).wall_patterns)
MESSAGES = [{"role": "user", "content": "hello"}]


def decision(tier: TierName = "free_cloud", model: str = "openrouter/acme/m:free") -> RouteDecision:
    tiers = LOCAL_TIERS if tier == "local" else ALL_TIERS
    scope = ScopeDecision("local" if tier == "local" else "auto", tiers, "cost_saver", "agentic")
    zero = ScoreBreakdown(model, 80, 0, 0, 0, 0, 0, 0, 0, 0, 80, 50, True, 0)
    return RouteDecision(model, tier, 80, 50, False, scope, (), (), (), "cheap", False, zero)


def target_for(d: RouteDecision, **kw: Any) -> ExecTarget:
    local = d.tier == "local"
    base: dict[str, Any] = {
        "model_id": d.model,
        "provider": "ollama" if local else d.model.split("/", 1)[0],
        "route": d.model,
        "num_ctx": 16384 if local else None,
    }
    return ExecTarget(**{**base, **kw})


Step = Delta | ProviderError | tuple[str, float]


class ScriptedBackend:
    """Plays a script of ``Delta``s, ``ProviderError``s and ``("sleep", seconds)`` steps."""

    def __init__(self, script: Sequence[Step]) -> None:
        self.script = list(script)
        self.requests: list[BackendRequest] = []
        self.closed = False

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        try:
            for step in self.script:
                if isinstance(step, ProviderError):
                    raise step
                if isinstance(step, tuple):
                    await asyncio.sleep(step[1])
                    continue
                yield step
        finally:
            self.closed = True


def make_executor(
    backend: ScriptedBackend,
    *,
    rules: Rules | None = None,
    config: ExecutorRules | None = None,
    cold: bool | None = None,
    cloud_calls: list[int] | None = None,
    **kw: Any,
) -> Executor:
    def resolve(d: RouteDecision) -> ExecTarget:
        return target_for(d, cold=cold)

    def cloud_factory() -> ScriptedBackend:
        if cloud_calls is not None:
            cloud_calls.append(1)
        return backend

    local_factory: Callable[[str], ScriptedBackend] = lambda host: backend  # noqa: E731
    return Executor(
        rules or Rules(),
        WALLS,
        resolve,
        config=config,
        cloud_factory=cloud_factory,
        local_factory=local_factory,
        **kw,
    )


def stream_events(executor: Executor, d: RouteDecision) -> list[StreamEvent]:
    async def go() -> list[StreamEvent]:
        return [e async for e in executor.run(d, MESSAGES, None, True)]

    return asyncio.run(go())


def run_once(
    executor: Executor, d: RouteDecision, options: ExecOptions | None = None
) -> ExecResult:
    return asyncio.run(executor.run(d, MESSAGES, None, False, options))


def kinds(events: list[StreamEvent]) -> list[str]:
    return [e.kind for e in events]


def chunk(
    content: str | None = None,
    reasoning: str | None = None,
    finish: str | None = None,
    usage: Any = None,
    tool_calls: Any = None,
) -> Any:
    """A litellm-shaped stream chunk (duck-typed)."""
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, reasoning=None, tool_calls=tool_calls
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)
