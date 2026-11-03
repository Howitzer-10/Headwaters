"""HDW-301: the OpenAI-compatible gateway speaks ``/v1/models`` and ``/v1/chat/completions``.

The client is the real ``openai`` SDK over ``httpx.ASGITransport``, so the SSE shape, the error
envelope and the model cards are checked the way an IDE sees them. The happy paths run through
the HDW-148 rig's real router and scripted provider; the refusals use a router that raises.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from openai import AsyncOpenAI, BadRequestError, InternalServerError, NotFoundError
from starlette.types import ASGIApp

from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Rejection
from headwaters.core.router_types import RouteFailed, RouteRequest
from headwaters.core.rules import PickerRules, Rules
from headwaters.gateway.openai_routes import build_gateway_app
from tests.rebuild_support import ASK, TOKEN, DaemonRig

BASE = "http://127.0.0.1:4000"
CORE_IDS = ["headwaters/auto", "headwaters/free", "headwaters/local", "headwaters/cloud"]
IMAGE_ASK: list[dict[str, Any]] = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is in this picture?"},
            {"type": "image_url", "image_url": {"url": "https://example.com/cat.png"}},
        ],
    }
]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DaemonRig]:
    made = DaemonRig(tmp_path, monkeypatch)
    try:
        yield made
    finally:
        made.close()


class Refusing:
    """A router stand-in: keep the request it was given, then raise the error under test."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.seen: RouteRequest | None = None

    def handle(self, request: RouteRequest) -> Any:
        self.seen = request
        raise self.error


def with_rig(rig: DaemonRig) -> ASGIApp:
    """The gateway as the daemon serves it: live engine, live rules, the daemon's token."""
    return build_gateway_app(
        token=TOKEN,
        router=rig.daemon.gateway_router,
        rules=lambda: rig.manager.current,
    )


def plain_app(router: Any, rules: Rules | None = None) -> ASGIApp:
    return build_gateway_app(
        token=TOKEN,
        router=lambda: router,
        rules=lambda: rules if rules is not None else Rules(),
    )


def client_for(app: ASGIApp, token: str = TOKEN) -> AsyncOpenAI:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"{BASE}/v1")
    return AsyncOpenAI(api_key=token, base_url=f"{BASE}/v1", http_client=http)


def send(client: AsyncOpenAI, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
    """``chat.completions.create`` with the SDK's message TypedDicts relaxed to plain dicts."""
    return client.chat.completions.create(model=model, messages=cast("Any", messages), **kwargs)


async def model_ids(app: ASGIApp) -> list[str]:
    client = client_for(app)
    try:
        return [model.id for model in (await client.models.list()).data]
    finally:
        await client.close()


def test_models_lists_the_core_four(rig: DaemonRig) -> None:
    assert asyncio.run(model_ids(with_rig(rig))) == CORE_IDS


def test_models_lists_the_extras_only_when_the_picker_allows_them(rig: DaemonRig) -> None:
    shown = asyncio.run(model_ids(plain_app(None, Rules(picker=PickerRules(show_extras=True)))))
    assert shown == [*CORE_IDS, "headwaters/quality", "headwaters/duck"]
    hidden = asyncio.run(model_ids(plain_app(None, Rules(picker=PickerRules(show_extras=False)))))
    assert hidden == CORE_IDS


def test_an_unknown_model_id_is_a_404() -> None:
    async def go() -> None:
        client = client_for(plain_app(None))
        try:
            with pytest.raises(NotFoundError):
                await client.models.retrieve("headwaters/nope")
        finally:
            await client.close()

    asyncio.run(go())


def test_chat_completion_answers_through_the_router(rig: DaemonRig) -> None:
    async def go() -> Any:
        client = client_for(with_rig(rig))
        try:
            return await send(client, "headwaters/auto", ASK)
        finally:
            await client.close()

    completion = asyncio.run(go())
    assert completion.object == "chat.completion"
    assert completion.choices[0].message.content
    assert completion.choices[0].message.content.startswith("answer from engine")
    assert completion.choices[0].finish_reason == "stop"
    # ``model`` is the model that answered, not the virtual name that was asked for (v3.3 §B).
    assert completion.model
    assert not completion.model.startswith("headwaters/")


def test_chat_completion_streams_as_sse(rig: DaemonRig) -> None:
    async def go() -> str:
        client = client_for(with_rig(rig))
        try:
            stream = await send(client, "headwaters/auto", ASK, stream=True)
            seen, text = 0, ""
            async for chunk in stream:
                seen += 1
                assert chunk.object == "chat.completion.chunk"
                if chunk.choices:
                    text += chunk.choices[0].delta.content or ""
            assert seen > 1  # a role chunk, the content and a finish chunk at least
            return text
        finally:
            await client.close()

    assert asyncio.run(go()).startswith("answer from engine")


def test_a_streamed_answer_can_ask_for_usage(rig: DaemonRig) -> None:
    async def go() -> Any:
        client = client_for(with_rig(rig))
        try:
            stream = await send(
                client,
                "headwaters/auto",
                ASK,
                stream=True,
                stream_options={"include_usage": True},
            )
            usage = None
            async for chunk in stream:
                usage = chunk.usage or usage
            return usage
        finally:
            await client.close()

    usage = asyncio.run(go())
    assert usage is not None
    assert usage.total_tokens is not None


def test_a_request_without_the_token_is_a_401(rig: DaemonRig) -> None:
    async def go() -> int:
        transport = httpx.ASGITransport(app=with_rig(rig))
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return (await http.get("/v1/models")).status_code

    assert asyncio.run(go()) == 401


def test_a_remote_image_says_to_send_a_data_url(rig: DaemonRig) -> None:
    """FU-135-3: a local model cannot fetch the network, so the error names the fix."""
    error = NoRouteError(
        "no_candidates",
        "No model can serve this request: remote image: local models cannot fetch it.",
        rejected=(Rejection("local/qwen2.5-vl", "image_url_not_local"),),
    )

    async def go() -> str:
        client = client_for(plain_app(Refusing(error)))
        try:
            with pytest.raises(BadRequestError) as caught:
                await send(client, "headwaters/local", IMAGE_ASK)
            return str(caught.value)
        finally:
            await client.close()

    assert "data: URL" in asyncio.run(go())


def test_an_executor_image_failure_is_a_502_with_the_same_advice() -> None:
    error = RouteFailed(
        "all_attempts_failed",
        "Every attempt failed.",
        last_error={"code": "image_url_not_local", "message": "a remote image URL"},
    )

    async def go() -> str:
        client = client_for(plain_app(Refusing(error)))
        try:
            with pytest.raises(InternalServerError) as caught:
                await send(client, "headwaters/auto", IMAGE_ASK)
            return str(caught.value)
        finally:
            await client.close()

    assert "data: URL" in asyncio.run(go())


def test_a_model_the_picker_does_not_know_is_pinned() -> None:
    """HDW-302 adds the alias table; today any other name is a pin, as v3.3 §B says."""
    router = Refusing(NoRouteError("pin_rejected", "That model is not allowed."))

    async def go() -> None:
        client = client_for(plain_app(router))
        try:
            with pytest.raises(BadRequestError):
                await send(client, "gpt-4o", ASK)
        finally:
            await client.close()

    asyncio.run(go())
    assert router.seen is not None
    assert router.seen.pin == "gpt-4o"
    assert router.seen.surface == "gateway"
    assert "authorization" not in (router.seen.headers or {})


def test_the_bearer_token_is_not_a_routing_hint(rig: DaemonRig) -> None:
    class Recording:
        def __init__(self) -> None:
            self.seen: RouteRequest | None = None

        def handle(self, request: RouteRequest) -> Any:
            self.seen = request
            raise NoRouteError("pin_rejected", "stop here")

    router = Recording()

    async def go() -> None:
        client = client_for(plain_app(router))
        try:
            with pytest.raises(BadRequestError):
                await send(
                    client,
                    "headwaters/auto",
                    ASK,
                    extra_headers={"x-session-id": "s-1"},
                )
        finally:
            await client.close()

    asyncio.run(go())
    headers = (router.seen.headers if router.seen is not None else None) or {}
    assert router.seen is not None
    assert router.seen.session_id == "s-1"
    assert headers.get("x-session-id") == "s-1"
    assert not [key for key in headers if key.lower() == "authorization"]
