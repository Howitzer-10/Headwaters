"""HDW-512 slice D: the opt-in answer footer.

The footer is a mechanism, not a string: ``gateway.footer`` in rules.yaml turns it on and picks
the fields. It must be off by default (a host that parses the body today sees the byte-identical
body tomorrow), never touch a tool-call or an error answer, and never be counted as output.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from starlette.types import ASGIApp

from headwaters.core.executor.types import ExecResult, StreamEvent, ToolCall, Usage
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_types import RouteEvent, RouteRequest, RouteResult
from headwaters.core.rules import Rules
from headwaters.gateway.openai_routes import build_gateway_app
from tests.executor_support import decision
from tests.rebuild_support import ASK, TOKEN

BASE = "http://127.0.0.1:4000"
CHAT = "/v1/chat/completions"
MESSAGES = "/v1/messages"
CALL = ToolCall(id="call_1", name="headwaters_status", arguments="{}")


def on(**footer: Any) -> Rules:
    return Rules.model_validate({"gateway": {"footer": {"enabled": True, **footer}}})


class Canned:
    """A router stand-in: answer once, stream prepared events, or raise."""

    def __init__(
        self,
        answer: RouteResult | None = None,
        events: list[RouteEvent] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.answer, self.events, self.error = answer, events, error

    def handle(self, request: RouteRequest) -> Any:
        if self.error is not None:
            raise self.error
        if self.events is not None:
            return self._events()

        async def go() -> RouteResult:
            assert self.answer is not None
            return self.answer

        return go()

    async def _events(self) -> Any:
        for event in self.events or []:
            yield event


def answered(
    content: str = "ok answer",
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    finish: str = "stop",
    latency_ms: int | None = 812,
    cost_usd: float | None = 0.0,
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
    return RouteResult(
        result=result,
        decision=chosen,
        session_key="session",
        attempts=(),
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )


def streamed(result: RouteResult) -> list[RouteEvent]:
    return [
        RouteEvent("route", decision=result.decision),
        RouteEvent("stream", event=StreamEvent(kind="content", text="ok answer")),
        RouteEvent("final", result=result),
    ]


def app_for(router: Any, rules: Rules) -> ASGIApp:
    return build_gateway_app(token=TOKEN, router=lambda: router, rules=lambda: rules)


def post(app: ASGIApp, body: dict[str, Any], path: str = CHAT) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        sent = {"Authorization": f"Bearer {TOKEN}", "x-api-key": TOKEN}
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.post(path, json=body, headers=sent)

    return asyncio.run(go())


def chat_body(**extra: Any) -> dict[str, Any]:
    return {"model": "headwaters/auto", "messages": ASK, **extra}


def frames(response: httpx.Response) -> list[dict[str, Any]]:
    return [
        json.loads(block[len("data: ") :])
        for block in response.text.split("\n\n")
        if block.startswith("data: ") and block[len("data: ") :] != "[DONE]"
    ]


def deltas(response: httpx.Response) -> list[str]:
    return [
        frame["choices"][0]["delta"].get("content") or ""
        for frame in frames(response)
        if frame.get("choices") and frame["choices"][0].get("delta")
    ]


# ------------------------------------------------------------------------- off by default


def test_the_footer_is_off_by_default() -> None:
    response = post(app_for(Canned(answered()), Rules()), chat_body())
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "ok answer"
    assert "Headwaters:" not in response.text


def test_a_stream_is_unchanged_when_the_footer_is_off() -> None:
    answer = answered()
    response = post(app_for(Canned(events=streamed(answer)), Rules()), chat_body(stream=True))
    assert "Headwaters:" not in response.text
    assert "".join(deltas(response)) == "ok answer"


# ---------------------------------------------------------------------------- on, for real


def test_the_footer_is_one_plain_line_after_a_blank_line() -> None:
    answer = answered()
    response = post(app_for(Canned(answer), on()), chat_body())
    body = response.json()
    content = body["choices"][0]["message"]["content"]
    head, _, footer = content.partition("\n\n")
    assert head == "ok answer"
    assert footer.startswith(f"Headwaters: model={answer.decision.model} tier=")
    assert " tokens=11+7 " in footer
    assert " cost=free " in footer  # a local/free-cloud answer with no price says so honestly
    assert footer.endswith(" latency=812 ms")
    assert "\n" not in footer  # one line, always
    # the footer is not model output and is not part of the reported usage
    assert body["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def test_a_priced_answer_reports_usd_and_a_local_one_reports_free() -> None:
    paid = post(app_for(Canned(answered(cost_usd=0.000421)), on(fields=["cost"])), chat_body())
    assert paid.json()["choices"][0]["message"]["content"].endswith(
        "\n\nHeadwaters: cost=$0.000421"
    )
    local = answered()
    local = RouteResult(
        result=local.result,
        decision=decision(tier="local", model="qwen3:8b"),
        session_key="session",
        attempts=(),
        latency_ms=12,
        cost_usd=None,
    )
    free = post(app_for(Canned(local), on(fields=["cost"])), chat_body())
    assert free.json()["choices"][0]["message"]["content"].endswith("\n\nHeadwaters: cost=free")


def test_field_selection_changes_the_text() -> None:
    answer = answered()
    only = post(app_for(Canned(answer), on(fields=["model"])), chat_body())
    assert only.json()["choices"][0]["message"]["content"] == (
        f"ok answer\n\nHeadwaters: model={answer.decision.model}"
    )
    two = post(app_for(Canned(answer), on(fields=["tier", "tokens"])), chat_body())
    footer = two.json()["choices"][0]["message"]["content"].split("\n\n", 1)[1]
    assert footer.startswith(f"Headwaters: tier={answer.decision.tier} tokens=11+7")
    assert "model=" not in footer
    assert "cost=" not in footer


def test_a_stream_ends_with_the_footer_as_a_content_delta() -> None:
    answer = answered()
    response = post(app_for(Canned(events=streamed(answer)), on()), chat_body(stream=True))
    text = "".join(deltas(response))
    assert text.endswith("\n\nHeadwaters: ") or "Headwaters:" in text
    assert "ok answer\n\nHeadwaters: model=" in text
    assert text.count("Headwaters:") == 1
    # the footer rides in a content delta, before the chunk that carries finish_reason
    sent = frames(response)
    footer_at = next(
        i
        for i, frame in enumerate(sent)
        if "Headwaters:" in (frame["choices"][0]["delta"].get("content") or "")
    )
    finish_at = next(i for i, frame in enumerate(sent) if frame["choices"][0].get("finish_reason"))
    assert footer_at < finish_at


# ------------------------------------------------------------------- answers that stay clean


def test_a_tool_call_answer_is_never_given_a_footer() -> None:
    answer = answered("", tool_calls=(CALL,), finish="tool_calls")
    response = post(app_for(Canned(answer), on()), chat_body(tools=[{"type": "function"}]))
    body = response.json()
    assert body["choices"][0]["message"]["content"] == ""
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert "Headwaters:" not in response.text


def test_a_streamed_tool_call_is_never_given_a_footer() -> None:
    answer = answered("", tool_calls=(CALL,), finish="tool_calls")
    events = [
        RouteEvent("route", decision=answer.decision),
        RouteEvent("stream", event=StreamEvent(kind="tool_call", tool_call=CALL)),
        RouteEvent("final", result=answer),
    ]
    response = post(app_for(Canned(events=events), on()), chat_body(stream=True))
    assert "Headwaters:" not in response.text


def test_an_error_response_stays_clean() -> None:
    refusal = NoRouteError("local_unavailable", "No local model fits this request.")
    response = post(app_for(Canned(error=refusal), on()), chat_body())
    assert response.status_code != 200
    assert "Headwaters:" not in response.text


# --------------------------------------------------------------------------- Anthropic face


def test_the_messages_face_carries_the_footer_in_its_text_block() -> None:
    answer = answered()
    response = post(
        app_for(Canned(answer), on()),
        {"model": "claude-sonnet-4", "messages": ASK, "max_tokens": 64},
        MESSAGES,
    )
    blocks = response.json()["content"]
    assert [block["type"] for block in blocks] == ["text"]
    assert blocks[0]["text"].startswith("ok answer\n\nHeadwaters: model=")


def test_the_messages_face_keeps_a_tool_use_answer_clean() -> None:
    answer = answered("", tool_calls=(CALL,), finish="tool_calls")
    response = post(
        app_for(Canned(answer), on()),
        {"model": "claude-sonnet-4", "messages": ASK, "max_tokens": 64},
        MESSAGES,
    )
    body = response.json()
    assert [block["type"] for block in body["content"]] == ["tool_use"]
    assert "Headwaters:" not in response.text
