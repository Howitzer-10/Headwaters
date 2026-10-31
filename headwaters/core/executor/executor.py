"""The executor (HDW-115, v3 section 4.8, v3.3 section D).

``Executor.run(decision, messages, tools, stream)`` calls the chosen model: cloud through the
litellm SDK, local through Ollama's native ``/api/chat``. Streaming exposes a ``first_token``
event so callers fail over only before it; later failures arrive as an ``error`` event. Provider
errors are classified by the wall classifier. Local scope is enforced here: no cloud backend is
constructed for a local-only request, and the Ollama host must be loopback.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Sequence
from typing import Any, Literal, overload

from headwaters.core.executor.accumulate import Accumulator
from headwaters.core.executor.cloud import LiteLLMBackend
from headwaters.core.executor.cloud_map import OPENROUTER, attribution_headers
from headwaters.core.executor.errors import ExecutorError, ProviderError
from headwaters.core.executor.guard import enforce_scope
from headwaters.core.executor.ollama import OllamaBackend
from headwaters.core.executor.types import (
    Backend,
    BackendRequest,
    Delta,
    ExecOptions,
    ExecResult,
    ExecTarget,
    ExecutorConfig,
    Message,
    StreamEvent,
    ToolSpec,
)
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules
from headwaters.core.walls import WallClassifier

# Maps a decision to what to call (litellm route or Ollama name, ``num_ctx``, cold flag).
# The router supplies it from the catalog, discovery and hardware fit.
TargetResolver = Callable[[RouteDecision], ExecTarget]


def _has_output(delta: Delta) -> bool:
    return bool(delta.content or delta.reasoning or delta.tool_calls)


class Executor:
    def __init__(
        self,
        rules: Rules,
        walls: WallClassifier,
        resolve: TargetResolver,
        *,
        config: ExecutorConfig | None = None,
        cloud_factory: Callable[[], Backend] = LiteLLMBackend,
        local_factory: Callable[[str], Backend] = OllamaBackend,
        api_key_for: Callable[[str], str | None] | None = None,
    ) -> None:
        self._rules = rules
        self._walls = walls
        self._resolve = resolve
        self._config = config or ExecutorConfig()
        self._cloud_factory = cloud_factory
        self._local_factory = local_factory
        self._api_key_for = api_key_for
        self._cloud: Backend | None = None  # built on first cloud call, never for local scope

    @overload
    def run(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = ...,
        stream: Literal[False] = ...,
        options: ExecOptions | None = ...,
    ) -> Coroutine[Any, Any, ExecResult]: ...

    @overload
    def run(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        stream: Literal[True],
        options: ExecOptions | None = ...,
    ) -> AsyncIterator[StreamEvent]: ...

    def run(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        stream: bool = False,
        options: ExecOptions | None = None,
    ) -> Coroutine[Any, Any, ExecResult] | AsyncIterator[StreamEvent]:
        """``stream=False`` -> awaitable ``ExecResult`` (raises ``ExecutorError``).
        ``stream=True`` -> async iterator of ``StreamEvent``: errors before the first token are
        raised (the caller may fail over); later ones arrive as an ``error`` event."""
        opts = options or ExecOptions()
        if stream:
            return self._events(decision, messages, tools, opts)
        return self._collect(decision, messages, tools, opts)

    async def _collect(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        options: ExecOptions,
    ) -> ExecResult:
        final: ExecResult | None = None
        async for event in self._events(decision, messages, tools, options):
            if event.kind == "error" and event.error is not None:
                raise event.error
            if event.kind == "done":
                final = event.result
        if final is None:
            raise ExecutorError("no_result", "The model returned no result.", model=decision.model)
        return final

    def _prepare(
        self, decision: RouteDecision
    ) -> tuple[Backend, ExecTarget, dict[str, str], str | None]:
        enforce_scope(decision, self._rules)  # before any backend or client exists
        target = self._resolve(decision)
        if decision.tier == "local":
            return self._local_factory(self._rules.tiers.local.host), target, {}, None
        if self._cloud is None:
            self._cloud = self._cloud_factory()
        headers = (
            attribution_headers(self._rules.attribution) if target.provider == OPENROUTER else {}
        )
        key = self._api_key_for(target.provider) if self._api_key_for else None
        return self._cloud, target, headers, key

    def _first_token_limit(self, decision: RouteDecision, target: ExecTarget) -> float:
        cfg = self._config
        if decision.tier != "local":
            return cfg.cloud_first_token_s
        if target.cold is False:
            return cfg.local_warm_first_token_s
        return cfg.local_cold_first_token_s  # cold or unknown: the model may still be loading

    def _error(
        self, exc: ProviderError, decision: RouteDecision, target: ExecTarget, *, first: bool
    ) -> ExecutorError:
        provider = "ollama" if decision.tier == "local" else target.provider
        wall = self._walls.classify(exc.status, exc.headers, exc.body, provider)
        if exc.kind == "timeout":
            code = "timeout"
        elif exc.kind == "connection":
            code = "connection_error"
        elif wall.kind != "other":
            code = wall.kind
        else:
            code = "provider_error"
        return ExecutorError(
            code,
            f"Provider call failed ({code}).",
            wall=wall,
            status=exc.status,
            provider=provider,
            model=decision.model,
            before_first_token=first,
        )

    async def _events(
        self,
        decision: RouteDecision,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        options: ExecOptions,
    ) -> AsyncIterator[StreamEvent]:
        backend, target, headers, key = self._prepare(decision)
        keep_alive = self._rules.tiers.local.keep_alive
        request = BackendRequest(target, messages, tools, options, keep_alive, headers, key)
        acc = Accumulator(decision.model, decision.tier)
        started = time.monotonic()
        deadline = started + self._first_token_limit(decision, target)
        first_s: float | None = None
        source = backend.stream(request)
        try:
            while True:
                if first_s is None:
                    limit = deadline - time.monotonic()
                else:
                    limit = self._config.stream_idle_s
                try:
                    async with asyncio.timeout(max(limit, 0.0)):
                        delta = await anext(source)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise ProviderError(408, None, "", kind="timeout") from None
                if first_s is None and _has_output(delta):
                    first_s = time.monotonic() - started
                    yield StreamEvent("first_token", first_token_s=first_s)
                acc.add(delta)
                if delta.reasoning:
                    yield StreamEvent("reasoning", text=delta.reasoning)
                if delta.content:
                    yield StreamEvent("content", text=delta.content)
        except ProviderError as exc:
            error = self._error(exc, decision, target, first=first_s is None)
            if first_s is None:
                raise error from exc
            yield StreamEvent("error", error=error)
            return
        finally:
            aclose = getattr(source, "aclose", None)
            if aclose is not None:
                await aclose()
        for call in acc.tool_calls():
            yield StreamEvent("tool_call", tool_call=call)
        yield StreamEvent("done", result=acc.result(first_s), first_token_s=first_s)
