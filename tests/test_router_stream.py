"""HDW-117: streaming failover and the handle() entry point."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import Delta
from headwaters.core.router_types import RouteEvent, RouteRequest
from tests.executor_support import Step
from tests.router_rig import Rig
from tests.router_support import OK, RATE_LIMITED
from tests.test_router import FREE, LOCAL, pool


def kinds(events: list[RouteEvent]) -> list[str]:
    return [e.kind for e in events]


def test_stream_fails_over_before_the_first_token(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    events = rig.stream()
    assert rig.dispatch.calls == [FREE, LOCAL]
    assert kinds(events).count("route") == 1
    route = next(e for e in events if e.kind == "route")
    assert route.decision is not None
    assert route.decision.model == LOCAL
    final = events[-1]
    assert final.kind == "final"
    assert final.result is not None
    assert final.result.result.content == "ok answer"
    assert [a.status for a in final.result.attempts] == ["rate_limit", "ok"]


def test_stream_never_switches_models_after_the_first_token(tmp_path: Path) -> None:
    broken: list[Step] = [Delta(content="par"), ProviderError(500, {}, "", kind="stream_error")]
    rig = Rig(tmp_path, {FREE: broken, LOCAL: OK}, pool(local=84.0))
    events = rig.stream()
    assert rig.dispatch.calls == [FREE]
    final = events[-1]
    assert final.kind == "final"
    assert final.error is not None
    assert final.result is None
    assert final.error.before_first_token is False


def test_stream_success_yields_content_and_one_decision_row(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    events = rig.stream()
    final = events[-1]
    assert final.result is not None
    assert final.result.decision_id is not None
    texts = [e.event.text for e in events if e.event is not None and e.event.kind == "content"]
    assert texts == ["ok answer"]


def test_handle_returns_an_awaitable_result_or_an_event_stream(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    messages = [{"role": "user", "content": "hello there"}]

    async def one_shot() -> str:
        pending = rig.router.handle(RouteRequest(messages))
        assert asyncio.iscoroutine(pending)
        result = await pending
        return result.decision.model

    async def streamed() -> list[str]:
        events = rig.router.handle(RouteRequest(messages, stream=True))
        assert not asyncio.iscoroutine(events)
        assert isinstance(events, AsyncIterator)
        return [e.kind async for e in events]

    assert asyncio.run(one_shot()) == FREE
    assert asyncio.run(streamed())[-1] == "final"
