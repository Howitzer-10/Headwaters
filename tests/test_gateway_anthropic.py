"""HDW-303: the same gateway speaks the Anthropic Messages API at ``/v1/messages``.

The client is the real ``anthropic`` SDK over ``httpx.ASGITransport``, so the event frames, the
error envelope and the tool-use blocks are checked the way Claude Code sees them. The happy
paths run through the HDW-148 rig's real daemon router and scripted provider; the translation
and refusal cases use a router stand-in that records the request it was given.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any, cast

import anthropic
import httpx
import httpx2
import pytest
from anthropic import AsyncAnthropic
from starlette.types import ASGIApp

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import ExecResult, StreamEvent, ToolCall, Usage
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Rejection
from headwaters.core.router_types import RouteEvent, RouteRequest, RouteResult
from headwaters.core.rules import Rules
from headwaters.gateway.openai_routes import build_gateway_app
from tests.executor_support import decision
from tests.rebuild_support import ASK, TOKEN, DaemonRig

BASE = "http://127.0.0.1:4000"
HELLO = [{"role": "user", "content": "Hello, Claude"}]
COUNTING = "/v1/messages/count_tokens"
TOOL = {
    "name": "get_weather",
    "description": "the weather in a city",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
}


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DaemonRig]:
    made = DaemonRig(tmp_path, monkeypatch)
    try:
        yield made
    finally:
        made.close()


class Recording:
    """A router stand-in: keep the request it was given, then answer, stream or raise."""

    def __init__(
        self,
        answer: RouteResult | None = None,
        events: Sequence[RouteEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.events = None if events is None else list(events)
        self.error = error
        self.seen: RouteRequest | None = None

    def handle(self, request: RouteRequest) -> Any:
        self.seen = request
        if self.error is not None:
            raise self.error
        if self.events is not None:
            return self._events()

        async def go() -> RouteResult:
            assert self.answer is not None
            return self.answer

        return go()

    async def _events(self) -> AsyncIterator[RouteEvent]:
        assert self.events is not None
        for event in self.events:
            yield event


def answered(
    content: str = "ok answer",
    tool_calls: tuple[ToolCall, ...] = (),
    finish: str = "stop",
) -> RouteResult:
    chosen = decision()
    result = ExecResult(
        model=chosen.model,
        tier=chosen.tier,
        content=content,
        reasoning="",
        tool_calls=tool_calls,
        finish_reason=finish,
        usage=Usage(prompt_tokens=11, completion_tokens=7),
        first_token_s=0.1,
    )
    return RouteResult(result=result, decision=chosen, session_key="session", attempts=())


def streamed(
    *items: StreamEvent,
    result: RouteResult | None = None,
    error: ExecutorError | None = None,
) -> list[RouteEvent]:
    events = [RouteEvent("route", decision=decision())]
    events += [RouteEvent("stream", event=item) for item in items]
    events.append(RouteEvent("final", result=result, error=error))
    return events


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


def client_for(app: ASGIApp, key: str = TOKEN) -> AsyncAnthropic:
    """The SDK sends ``x-api-key`` and appends ``/v1/messages`` to ``base_url``.

    ``anthropic`` 2.4 speaks ``httpx2`` and refuses an ``httpx`` client, so the ASGI transport
    under test is the ``httpx2`` one.
    """
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE)
    return AsyncAnthropic(api_key=key, base_url=BASE, http_client=http, max_retries=0)


def run(router: Any, model: str = "headwaters/auto", **kwargs: Any) -> Any:
    """One non-streamed ``messages.create`` through a router stand-in."""

    async def go() -> Any:
        client = client_for(plain_app(router))
        try:
            return await client.messages.create(
                model=model,
                max_tokens=64,
                messages=cast("Any", ASK),
                **kwargs,
            )
        finally:
            await client.close()

    return asyncio.run(go())


def failure(router: Any) -> str:
    """The message text the SDK raises for a router stand-in that refuses."""

    async def go() -> str:
        client = client_for(plain_app(router))
        try:
            with pytest.raises(anthropic.APIStatusError) as caught:
                await client.messages.create(
                    model="headwaters/auto",
                    max_tokens=64,
                    messages=cast("Any", ASK),
                )
            return str(caught.value)
        finally:
            await client.close()

    return asyncio.run(go())


def counted(router: Any, messages: Sequence[Any] | None = None, **kwargs: Any) -> int:
    """One ``messages.count_tokens`` through a router stand-in, as the SDK sends it."""

    async def go() -> int:
        client = client_for(plain_app(router))
        try:
            asked = await client.messages.count_tokens(
                model="headwaters/auto",
                messages=cast("Any", HELLO if messages is None else list(messages)),
                **kwargs,
            )
            return asked.input_tokens
        finally:
            await client.close()

    return asyncio.run(go())


def reply(
    app: ASGIApp,
    body: Any = None,
    *,
    raw: bytes | None = None,
    key: str | None = TOKEN,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """One raw ``POST`` to the counting face: the bodies and headers the SDK will not send."""

    async def go() -> tuple[int, dict[str, Any]]:
        sent_headers = dict(headers or {})
        if key is not None:
            sent_headers["x-api-key"] = key
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=BASE, headers=sent_headers
        ) as http:
            if raw is not None:
                answer = await http.post(COUNTING, content=raw)
            else:
                answer = await http.post(COUNTING, json=body)
            return answer.status_code, answer.json()

    return asyncio.run(go())


def test_a_token_count_answers_with_the_documented_shape() -> None:
    router = Recording(answer=answered())
    status, body = reply(plain_app(router), {"model": "headwaters/auto", "messages": HELLO})
    assert status == 200
    # One key and one key only, as MessageTokensCount defines; 13 chars of prose at 4 per token.
    assert body == {"input_tokens": 4}
    assert router.seen is None  # counting never reaches the router


def test_the_sdk_counts_through_the_same_face() -> None:
    assert counted(Recording(answer=answered())) == 4


def test_a_count_answers_when_no_model_can_serve_anything() -> None:
    """No router at all: there is nothing to route, so the count still comes back."""
    assert counted(None) == 4


def test_the_system_prompt_and_tools_are_in_the_count() -> None:
    """The documented count spans messages, system prompt and tools, so both must add up."""
    router = Recording(answer=answered())
    prose_only = counted(router)
    with_system = counted(router, system="You are a terse and precise assistant.")
    with_tools = counted(router, tools=[TOOL])
    assert prose_only == 4
    assert with_system > prose_only
    assert with_tools > prose_only
    assert router.seen is None


def test_an_image_is_counted_by_the_hint_not_by_its_bytes() -> None:
    """A base64 payload must cost one image hint, not four thousand characters of text."""
    image = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "A" * 4000},
    }
    _, body = reply(
        plain_app(Recording(answer=answered())),
        {"model": "headwaters/auto", "messages": [{"role": "user", "content": [image]}]},
    )
    assert body == {"input_tokens": 800}


def test_a_count_body_the_messages_face_refuses_is_a_400_here_too() -> None:
    router = Recording(answer=answered())
    status, body = reply(
        plain_app(router),
        {
            "model": "headwaters/auto",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "document", "source": {"type": "text", "data": "hi"}}],
                }
            ],
        },
    )
    assert status == 400
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "document" in body["error"]["message"]
    assert router.seen is None


def test_a_count_body_that_is_not_json_is_an_anthropic_400() -> None:
    status, body = reply(plain_app(Recording(answer=answered())), raw=b"not json")
    assert status == 400
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"


def test_a_count_body_without_a_model_still_counts() -> None:
    """``model`` is carried for shape only: counting never picks, checks or routes a model."""
    app = plain_app(Recording(answer=answered()))
    assert reply(app, {"messages": HELLO}) == (200, {"input_tokens": 4})
    assert reply(app, {"model": "some/unknown-model", "messages": HELLO}) == (
        200,
        {"input_tokens": 4},
    )


def test_a_count_request_keeps_the_token_and_the_loopback_guards() -> None:
    app = plain_app(Recording(answer=answered()))
    body = {"model": "headwaters/auto", "messages": HELLO}
    assert reply(app, body, key="not-the-token")[0] == 401
    assert reply(app, body, key=None)[0] == 401
    assert reply(app, body, headers={"host": "example.com"})[0] == 421
    assert reply(app, body, headers={"origin": "https://example.com"})[0] == 403
    assert reply(app, body, key=None, headers={"authorization": f"Bearer {TOKEN}"})[0] == 200


def test_a_messages_request_answers_with_one_text_block(rig: DaemonRig) -> None:
    async def go() -> Any:
        client = client_for(with_rig(rig))
        try:
            return await client.messages.create(
                model="headwaters/auto",
                max_tokens=64,
                messages=cast("Any", ASK),
            )
        finally:
            await client.close()

    message = asyncio.run(go())
    assert message.type == "message"
    assert message.role == "assistant"
    assert [block.type for block in message.content] == ["text"]
    assert message.content[0].text.startswith("answer from engine")
    assert message.stop_reason == "end_turn"
    assert message.stop_sequence is None
    assert message.usage.input_tokens is not None
    assert message.usage.output_tokens is not None
    # ``model`` is the model that answered, not the virtual name that was asked for (v3.3 §B).
    assert message.model
    assert not message.model.startswith("headwaters/")


def test_the_routing_headers_reach_the_client(rig: DaemonRig) -> None:
    async def go() -> dict[str, str]:
        client = client_for(with_rig(rig))
        try:
            raw = await client.messages.with_raw_response.create(
                model="headwaters/auto",
                max_tokens=64,
                messages=cast("Any", ASK),
            )
            assert (await raw.parse()).content[0].type == "text"
            return dict(raw.headers.items())
        finally:
            await client.close()

    headers = asyncio.run(go())
    assert headers["x-headwaters-model"]
    assert headers["x-headwaters-tier"]
    assert headers["x-headwaters-scope"]
    assert headers["x-headwaters-reason"]


def test_a_streamed_answer_follows_the_messages_event_sequence(rig: DaemonRig) -> None:
    async def go() -> tuple[str, Any]:
        client = client_for(with_rig(rig))
        try:
            stream = client.messages.stream(
                model="headwaters/auto",
                max_tokens=64,
                messages=cast("Any", ASK),
            )
            async with stream as live:
                text = "".join([chunk async for chunk in live.text_stream])
                return text, await live.get_final_message()
        finally:
            await client.close()

    text, final = asyncio.run(go())
    assert text.startswith("answer from engine")
    assert final.content[0].text == text
    assert final.stop_reason == "end_turn"


def test_every_stream_frame_names_its_event(rig: DaemonRig) -> None:
    """A data-only frame is skipped by the SDK (``_streaming.Stream`` reads ``sse.event``)."""

    def go() -> list[str]:
        transport = httpx.ASGITransport(app=with_rig(rig))
        body = {"model": "headwaters/auto", "max_tokens": 64, "stream": True, "messages": ASK}
        headers = {"x-api-key": TOKEN}

        async def post() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=transport, base_url=BASE, headers=headers
            ) as http:
                return await http.post("/v1/messages", json=body)

        answer = asyncio.run(post())
        assert answer.status_code == 200
        return [
            line[len("event: ") :]
            for line in answer.text.splitlines()
            if line.startswith("event: ")
        ]

    events = go()
    assert events[0] == "message_start"
    assert events[-1] == "message_stop"
    assert "content_block_delta" in events
    assert "message_delta" in events


def test_the_request_reaches_the_router_in_the_openai_shape() -> None:
    """System, tool use, tool result and an image translate to what the executor speaks."""
    router = Recording(answer=answered())
    body: dict[str, Any] = {
        "model": "claude-haiku-4-5",
        "max_tokens": 128,
        "system": [{"type": "text", "text": "be brief"}],
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "checking"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "get_weather",
                        "input": {"city": "Paris"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "18C"}],
                    },
                    {"type": "text", "text": "thanks"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "AAA"},
                    },
                ],
            },
        ],
        "tools": [TOOL],
    }

    async def go() -> None:
        client = client_for(plain_app(router))
        try:
            await client.messages.create(**cast("Any", body))
        finally:
            await client.close()

    asyncio.run(go())
    seen = router.seen
    assert seen is not None
    # A name the picker does not know is a pin, the same as on the OpenAI face (HDW-302).
    assert seen.pin == "claude-haiku-4-5"
    assert seen.max_output_tokens == 128
    sent = cast("list[dict[str, Any]]", list(seen.messages))
    assert [message["role"] for message in sent] == ["system", "user", "assistant", "tool", "user"]
    assert sent[0]["content"] == "be brief"
    assert sent[1]["content"] == "weather?"
    assert sent[2]["content"] == "checking"
    assert sent[2]["tool_calls"][0]["id"] == "toolu_1"
    assert sent[2]["tool_calls"][0]["function"] == {
        "name": "get_weather",
        "arguments": '{"city": "Paris"}',
    }
    assert sent[3]["tool_call_id"] == "toolu_1"
    assert sent[3]["content"] == "18C"
    assert sent[4]["content"][0] == {"type": "text", "text": "thanks"}
    assert sent[4]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AAA"},
    }
    assert seen.tools == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": TOOL["description"],
                "parameters": TOOL["input_schema"],
            },
        }
    ]


def test_a_tool_call_comes_back_as_a_tool_use_block() -> None:
    call = ToolCall(id="toolu_9", name="get_weather", arguments='{"city": "Paris"}')
    message = run(Recording(answer=answered("", (call,), "tool_calls")), tools=[TOOL])
    assert [block.type for block in message.content] == ["tool_use"]
    block = message.content[0]
    assert (block.id, block.name) == ("toolu_9", "get_weather")
    assert block.input == {"city": "Paris"}
    assert message.stop_reason == "tool_use"


def test_a_streamed_tool_call_opens_a_tool_use_block() -> None:
    call = ToolCall(id="toolu_7", name="get_weather", arguments='{"city": "Paris"}')
    answer = answered("one moment", (call,), "tool_calls")
    router = Recording(
        events=streamed(
            StreamEvent("first_token", first_token_s=0.1),
            StreamEvent("content", text="one moment"),
            StreamEvent("tool_call", tool_call=call),
            StreamEvent("done", result=answer.result),
            result=answer,
        )
    )

    async def go() -> Any:
        client = client_for(plain_app(router))
        try:
            stream = client.messages.stream(
                model="headwaters/auto",
                max_tokens=64,
                messages=cast("Any", ASK),
                tools=cast("Any", [TOOL]),
            )
            async with stream as live:
                return await live.get_final_message()
        finally:
            await client.close()

    final = asyncio.run(go())
    assert [block.type for block in final.content] == ["text", "tool_use"]
    assert final.content[0].text == "one moment"
    assert final.content[1].input == {"city": "Paris"}
    assert final.stop_reason == "tool_use"
    assert final.usage.output_tokens is not None


@pytest.mark.parametrize(
    ("finish", "expected"),
    [("length", "max_tokens"), ("content_filter", "refusal"), ("stop", "end_turn")],
)
def test_the_stop_reason_is_translated(finish: str, expected: str) -> None:
    message = run(Recording(answer=answered("x", (), finish)))
    assert message.stop_reason == expected


def test_a_mid_stream_failure_is_an_error_event_without_message_stop() -> None:
    broken = ExecutorError("rate_limited", "The provider is rate limiting.", status=429)
    router = Recording(
        events=streamed(
            StreamEvent("first_token", first_token_s=0.1),
            StreamEvent("content", text="par"),
            error=broken,
        )
    )

    async def go() -> list[str]:
        seen: list[str] = []
        client = client_for(plain_app(router))
        try:
            stream = await client.messages.create(
                model="headwaters/auto",
                max_tokens=64,
                messages=cast("Any", ASK),
                stream=True,
            )

            async def drain() -> None:
                async for event in stream:
                    seen.append(event.type)

            with pytest.raises(anthropic.APIStatusError) as caught:
                await drain()
            assert caught.value.body["type"] == "error"  # type: ignore[index]
        finally:
            await client.close()
        return seen

    seen = asyncio.run(go())
    assert seen[0] == "message_start"
    assert "content_block_delta" in seen
    assert "message_stop" not in seen


def test_a_refusal_is_an_invalid_request_error_with_the_advice() -> None:
    error = NoRouteError(
        "no_candidates",
        "No model can serve this request: remote image: local models cannot fetch it.",
        rejected=(Rejection("local/qwen2.5-vl", "image_url_not_local"),),
    )

    async def go() -> dict[str, Any]:
        client = client_for(plain_app(Recording(error=error)))
        try:
            with pytest.raises(anthropic.BadRequestError) as caught:
                await client.messages.create(
                    model="headwaters/local",
                    max_tokens=64,
                    messages=cast("Any", ASK),
                )
            assert caught.value.status_code == 400
            return cast("dict[str, Any]", caught.value.body)
        finally:
            await client.close()

    body = asyncio.run(go())
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "data: URL" in body["error"]["message"]


def test_a_block_the_face_cannot_map_is_a_400() -> None:
    body = {
        "model": "headwaters/auto",
        "max_tokens": 64,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "document", "source": {"type": "text", "data": "hi"}}],
            }
        ],
    }

    async def go() -> None:
        client = client_for(plain_app(Recording(answer=answered())))
        try:
            with pytest.raises(anthropic.BadRequestError) as caught:
                await client.messages.create(**cast("Any", body))
            assert "document" in str(caught.value)
        finally:
            await client.close()

    asyncio.run(go())


def test_a_router_that_is_not_ready_yet_is_a_529() -> None:
    async def go() -> int:
        client = client_for(plain_app(None))
        try:
            with pytest.raises(anthropic.APIStatusError) as caught:
                await client.messages.create(
                    model="headwaters/auto",
                    max_tokens=64,
                    messages=cast("Any", ASK),
                )
            return caught.value.status_code
        finally:
            await client.close()

    assert asyncio.run(go()) == 529


def test_a_wrong_api_key_is_a_401() -> None:
    async def go() -> None:
        client = client_for(plain_app(Recording(answer=answered())), key="not-the-token")
        try:
            with pytest.raises(anthropic.AuthenticationError):
                await client.messages.create(
                    model="headwaters/auto",
                    max_tokens=64,
                    messages=cast("Any", ASK),
                )
        finally:
            await client.close()

    asyncio.run(go())


def test_a_bearer_token_is_accepted_too(rig: DaemonRig) -> None:
    """The OpenAI face's credential keeps working on ``/v1/messages``."""

    def go() -> int:
        transport = httpx.ASGITransport(app=with_rig(rig))
        body = {"model": "headwaters/auto", "max_tokens": 64, "messages": ASK}
        headers = {"authorization": f"Bearer {TOKEN}"}

        async def post() -> int:
            async with httpx.AsyncClient(
                transport=transport, base_url=BASE, headers=headers
            ) as http:
                return (await http.post("/v1/messages", json=body)).status_code

        return asyncio.run(post())

    assert go() == 200
