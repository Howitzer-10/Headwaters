"""HDW-305: the multi-wall chain an IDE sees when the host's own credit runs out (v3 §5.1).

Four scenarios on the HDW-148 rig, through the real OpenAI face, the real router and the real
catalog's wall patterns; only the providers are mocked, one script per model id:

(a) ``headwaters/free`` after the host refused the request: the free model 429s and the local
    model answers, twice, completing an agent-mode tool loop with the tool result sent back;
(b) the same chain in ``headwaters/auto`` with the ★ paid-scopes feature switched on
    (``features: {paid_scopes: true}``): the paid key answers 402 (credit balance too low), then
    the free 429, then the local tool loop;
(c) the request shape a Copilot BYOK client sends: a system prompt, ``tools`` with
    ``tool_choice``, ``temperature``, ``max_tokens``, ``stream`` and ``x-session-id``;
(d) the default (flag-off) ``auto`` behaviour, which must not change: the paid key is never in
    the ladder, so the free 429 cascades straight to local.

The three accept-when chains are (a), (b) and (c); (d) is the regression pin for the default.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import Delta, ToolCallDelta
from headwaters.gateway.openai_routes import build_gateway_app
from tests.cli_support import add_model
from tests.rebuild_support import TOKEN, DaemonRig, ProgrammableProvider

BASE = "http://127.0.0.1:4000"
URL = "/v1/chat/completions"
AUTH = {"authorization": f"Bearer {TOKEN}"}
FREE = "vendor/free:free"
PAID = "anthropic/claude-sonnet-4-5"
LOCAL = "qwen:14b"
ASK: list[dict[str, Any]] = [{"role": "user", "content": "read a.py and tell me what it does"}]
SYSTEM = "You are a coding agent. Use the tools to read files before you answer."
TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read one file from the workspace.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}
# A 60 s retry-after is longer than ``walls.cascade_if_wait_over_s``, so the wall cascades at once
# instead of sleeping (the free tier's 429 always carries a wait like this).
FREE_WALL = ProviderError(429, {"retry-after": "60"}, "Rate limit exceeded")
PAID_WALL = ProviderError(402, {}, "Your credit balance is too low to access the Anthropic API")
FINAL = [Delta(content="it prints hi"), Delta(finish_reason="stop")]
# The user's paid primary; ``primary_first`` then leads with it, which is how a paid 402 can come
# before the free model at all.
PRIMARY = f"""\
strategy: primary_first
tiers:
  paid:
    models: ["{PAID}"]
"""

# HDW-502: the paid tier is a ★ feature, so its switch is what separates (b) from (d). Both are
# written out rather than relying on the beta default: this file is the regression pin for it.
STAR = """
features:
  paid_scopes: true
"""
NON_STAR = """
features:
  paid_scopes: false
"""


def call(path: str = "a.py") -> Delta:
    """The assistant turn that asks for a tool."""
    fragment = ToolCallDelta(
        index=0, id="call_1", name="read_file", arguments=json.dumps({"path": path})
    )
    return Delta(tool_calls=(fragment,), finish_reason="tool_calls")


def make_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, star: bool = False) -> DaemonRig:
    """The HDW-148 rig with a paid primary in the catalog and one programmed provider."""
    provider = ProgrammableProvider()
    rig = DaemonRig(tmp_path, monkeypatch, cloud=True, provider=provider)
    # Real providers, so the catalog's own wall patterns classify these failures as walls.
    add_model(rig.db, FREE, "free_cloud", 98.0, agentic=98.0, ctx=131072, provider="openrouter")
    add_model(
        rig.db,
        PAID,
        "paid",
        95.0,
        agentic=95.0,
        ctx=200_000,
        provider="anthropic",
        price_in=3,
        price_out=15,
    )
    rig.save(PRIMARY + (STAR if star else NON_STAR))
    assert rig.manager.reload() is True
    assert rig.manager.last_error is None
    return rig


def app_for(rig: DaemonRig) -> ASGIApp:
    """The gateway as the daemon serves it: live engine, live rules, the daemon's token."""
    return build_gateway_app(
        token=TOKEN,
        router=rig.daemon.gateway_router,
        rules=lambda: rig.manager.current,
    )


def post(app: ASGIApp, body: dict[str, Any], **extra: Any) -> httpx.Response:
    """One request through the ASGI app, so the routing headers are readable."""
    headers = {**AUTH, **{k.replace("_", "-"): v for k, v in extra.items()}}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.post(URL, json=body, headers=headers)

    return asyncio.run(go())


def chat(model: str = "headwaters/free", **extra: Any) -> dict[str, Any]:
    return {"model": model, "messages": ASK, "tools": [TOOL], **extra}


def frames(response: httpx.Response) -> list[dict[str, Any]]:
    """Every SSE data frame except the ``[DONE]`` sentinel."""
    sent: list[dict[str, Any]] = []
    for block in response.text.split("\n\n"):
        if block.startswith("data: "):
            payload = block[len("data: ") :]
            if payload != "[DONE]":
                sent.append(json.loads(payload))
    return sent


def follow_up(
    tool_calls: list[dict[str, Any]], result: str = "print('hi')"
) -> list[dict[str, Any]]:
    """The second turn an agent client sends: the tool result for the call it just got."""
    return [
        *ASK,
        {"role": "assistant", "content": None, "tool_calls": tool_calls},
        {"role": "tool", "tool_call_id": tool_calls[0]["id"], "content": result},
    ]


TOOL_CALL_JSON: dict[str, Any] = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
}


def test_a_free_scope_request_walls_free_and_local_answers_the_whole_tool_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(a) After the host refused, the user picks ``headwaters/free``: free 429, then local."""
    rig = make_rig(tmp_path, monkeypatch)
    try:
        provider = rig.backends[-1]
        assert isinstance(provider, ProgrammableProvider)
        provider.on(FREE, [FREE_WALL]).on(LOCAL, [call()], FINAL)
        app = app_for(rig)

        first = post(app, chat(), x_session_id="agent-loop")
        assert first.status_code == 200, first.text
        assert first.headers["x-headwaters-model"] == LOCAL
        assert first.headers["x-headwaters-scope"] == "free"
        choice = first.json()["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        assert choice["message"]["tool_calls"][0]["function"]["name"] == "read_file"
        assert provider.calls == [FREE, LOCAL]  # one wall, one answer

        second = post(
            app,
            {**chat(), "messages": follow_up(choice["message"]["tool_calls"])},
            x_session_id="agent-loop",
        )
        assert second.status_code == 200, second.text
        assert second.json()["choices"][0]["message"]["content"] == "it prints hi"
        assert provider.calls == [FREE, LOCAL, LOCAL]  # the wall is not retried on turn 2
        local = [sent for sent in provider.requests if sent.target.model_id == LOCAL]
        assert [sent.tools for sent in local] == [[TOOL], [TOOL]]  # tools survive the failover
        assert local[-1].messages[-1]["role"] == "tool"
        assert local[-1].messages[-1]["tool_call_id"] == "call_1"
    finally:
        rig.close()


def test_the_paid_then_free_then_local_chain_runs_behind_the_star_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) Same chain in ``auto`` with ★ paid scopes: paid 402, free 429, local tool loop."""
    rig = make_rig(tmp_path, monkeypatch, star=True)
    try:
        provider = rig.backends[-1]
        assert isinstance(provider, ProgrammableProvider)
        provider.on(PAID, [PAID_WALL]).on(FREE, [FREE_WALL]).on(LOCAL, [call()], FINAL)
        app = app_for(rig)

        first = post(app, chat("headwaters/auto"), x_session_id="agent-loop")
        assert first.status_code == 200, first.text
        assert first.headers["x-headwaters-model"] == LOCAL
        assert first.headers["x-headwaters-scope"] == "auto"  # paid tiers are in scope
        assert provider.calls == [PAID, FREE, LOCAL]

        second = post(
            app,
            {
                **chat("headwaters/auto"),
                "messages": follow_up(first.json()["choices"][0]["message"]["tool_calls"]),
            },
            x_session_id="agent-loop",
        )
        assert second.status_code == 200, second.text
        assert second.json()["choices"][0]["message"]["content"] == "it prints hi"
        # The spent key and the rate-limited model are walled, so the loop finishes locally.
        assert provider.calls == [PAID, FREE, LOCAL, LOCAL]
    finally:
        rig.close()


def test_the_star_flag_off_keeps_auto_off_the_paid_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d) The default must not change: with no ★ paid scopes, ``auto`` never tries the paid key."""
    rig = make_rig(tmp_path, monkeypatch)
    try:
        assert rig.manager.current.tiers.paid.models == [PAID]  # configured, and the primary
        provider = rig.backends[-1]
        assert isinstance(provider, ProgrammableProvider)
        provider.on(FREE, [FREE_WALL]).on(LOCAL, [call()], FINAL)
        app = app_for(rig)

        first = post(app, chat("headwaters/auto"), x_session_id="agent-loop")
        assert first.status_code == 200, first.text
        assert first.headers["x-headwaters-model"] == LOCAL
        # ``free`` is the narrowest name for the tiers that are left once the paid ones go.
        assert first.headers["x-headwaters-scope"] == "free"
        assert provider.calls == [FREE, LOCAL]
        assert PAID not in provider.calls
    finally:
        rig.close()


def test_a_copilot_byok_shaped_stream_completes_the_tool_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) What a Copilot BYOK client sends: system prompt, tools, temperature, stream, session."""
    rig = make_rig(tmp_path, monkeypatch)
    try:
        provider = rig.backends[-1]
        assert isinstance(provider, ProgrammableProvider)
        provider.on(FREE, [FREE_WALL]).on(LOCAL, [call()], FINAL, FINAL)
        app = app_for(rig)
        body: dict[str, Any] = {
            "model": "headwaters/free",
            "messages": [{"role": "system", "content": SYSTEM}, *ASK],
            "tools": [TOOL],
            "tool_choice": "auto",
            "temperature": 0.2,
            "max_tokens": 256,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        reply = post(app, body, x_session_id="copilot-byok", user_agent="GitHubCopilotChat/0.26")
        assert reply.status_code == 200, reply.text
        assert reply.headers["content-type"].startswith("text/event-stream")
        assert reply.headers["x-headwaters-model"] == LOCAL
        shipped = frames(reply)
        assert shipped[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
        tool_frames = [
            chunk
            for chunk in shipped
            if chunk.get("choices") and chunk["choices"][0]["delta"].get("tool_calls")
        ]
        assert tool_frames[0]["choices"][0]["delta"]["tool_calls"][0] == {
            "index": 0,
            **TOOL_CALL_JSON,
        }
        last = next(c for c in shipped if c.get("choices") and c["choices"][0]["finish_reason"])
        assert last["choices"][0]["finish_reason"] == "tool_calls"
        assert last["headwaters"]["model"] == LOCAL  # HDW-302: the outcome rides that chunk

        sent = provider.requests[-1]  # the local call that answered, after the free 429
        assert sent.tools == [TOOL]
        assert sent.messages[0] == {"role": "system", "content": SYSTEM}
        assert sent.options.temperature == 0.2
        assert sent.options.max_tokens == 256
        assert "authorization" not in sent.headers  # the daemon token is never a routing hint

        loop = [
            {"role": "system", "content": SYSTEM},
            *follow_up([TOOL_CALL_JSON]),
        ]
        second = post(
            app,
            {**body, "messages": loop},
            x_session_id="copilot-byok",
            user_agent="GitHubCopilotChat/0.26",
        )
        assert second.status_code == 200, second.text
        text = "".join(
            chunk["choices"][0]["delta"].get("content") or ""
            for chunk in frames(second)
            if chunk.get("choices")
        )
        assert text == "it prints hi"
        assert provider.calls == [FREE, LOCAL, LOCAL]
        assert provider.requests[-1].messages[-1]["role"] == "tool"
    finally:
        rig.close()
