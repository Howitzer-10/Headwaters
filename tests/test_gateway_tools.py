"""HDW-304: an agentic request keeps its tools, its tool calls and its validation (v3 §5.1).

Everything runs through the OpenAI face over the HDW-117 rig, so the router really scores, the
executor really builds a ``BackendRequest`` and ``BasicVerifier`` really checks the tool calls.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from headwaters.core.executor.types import Delta, ToolCallDelta
from headwaters.core.rules import Rules
from headwaters.core.verify_basic import BasicVerifier
from headwaters.gateway.openai_routes import build_gateway_app
from headwaters.gateway.shared import VERIFY_HEADER
from tests.rebuild_support import TOKEN
from tests.router_rig import Rig
from tests.router_support import candidate, requirements

BASE = "http://127.0.0.1:4000"
AUTH = {"authorization": f"Bearer {TOKEN}"}
MODEL = "m1"
TOOL = {
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
ASK: list[dict[str, Any]] = [{"role": "user", "content": "read a.py and tell me what it does"}]


def call(name: str = "read_file", **arguments: Any) -> Delta:
    """One assistant turn that asks for a tool."""
    fragment = ToolCallDelta(
        index=0, id="call_1", name=name, arguments=json.dumps(arguments) if arguments else "{}"
    )
    return Delta(tool_calls=(fragment,), finish_reason="tool_calls")


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(
        tmp_path,
        {MODEL: [Delta(content="final answer"), Delta(finish_reason="stop")]},
        [candidate(MODEL, "free_cloud", 90.0)],
        reqs=requirements(agentic=True),
        verifiers=[BasicVerifier()],
        rules=Rules(),
    )


def app_for(rig: Rig) -> ASGIApp:
    return build_gateway_app(token=TOKEN, router=lambda: rig.router, rules=lambda: Rules())


def chat(app: ASGIApp, messages: Sequence[Mapping[str, Any]], **body: Any) -> httpx.Response:
    payload = {"model": "headwaters/auto+verify", "messages": list(messages), **body}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.post("/v1/chat/completions", json=payload, headers=AUTH)

    return asyncio.run(go())


def turn(rig: Rig, script: Sequence[Delta]) -> httpx.Response:
    """One tool-bearing turn on the fixture rig, playing ``script`` for the model."""
    rig.dispatch.backends[MODEL].script = list(script)
    return chat(app_for(rig), ASK, tools=[TOOL])


def test_the_tool_schema_reaches_the_backend(rig: Rig) -> None:
    turn(rig, [Delta(content="ok"), Delta(finish_reason="stop")])
    assert rig.dispatch.backends[MODEL].requests[-1].tools == [TOOL]


def test_a_tool_call_comes_back_in_the_openai_shape(rig: Rig) -> None:
    answer = turn(rig, [call(path="a.py")])
    assert answer.status_code == 200
    choice = answer.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "a.py"}',
    }
    # A call the declared schema allows is a pass, never an unchecked answer.
    assert answer.headers[VERIFY_HEADER] == "pass"


def test_the_three_turn_agent_loop_round_trips(rig: Rig) -> None:
    first = turn(rig, [call(path="a.py")])
    assert first.status_code == 200
    answer = first.json()["choices"][0]["message"]
    loop: list[Mapping[str, Any]] = [
        *ASK,
        {"role": "assistant", "content": None, "tool_calls": answer["tool_calls"]},
        {"role": "tool", "tool_call_id": "call_1", "content": "print('hi')"},
    ]
    rig.dispatch.backends[MODEL].script = [
        Delta(content="it prints hi"),
        Delta(finish_reason="stop"),
    ]
    second = chat(app_for(rig), loop, tools=[TOOL])
    assert second.status_code == 200
    assert second.json()["choices"][0]["message"]["content"] == "it prints hi"
    sent = rig.dispatch.backends[MODEL].requests[-1]
    assert sent.tools == [TOOL]
    assert sent.messages[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "print('hi')"}
    assert sent.messages[-2]["tool_calls"][0]["function"]["name"] == "read_file"


def test_a_tool_call_outside_the_declared_schema_fails_verification(rig: Rig) -> None:
    for script in (call(path=3), call("write_file")):
        answer = turn(rig, [script])
        assert answer.status_code == 200
        assert answer.headers[VERIFY_HEADER] == "fail"


def test_an_agentic_request_lands_on_a_model_above_the_tool_floor(tmp_path: Path) -> None:
    strong = candidate("strong", "free_cloud", 95.0)
    weak = replace(
        candidate("weak", "free_cloud", 99.0), scores={**strong.scores, "agentic_tools": 10.0}
    )
    rig = Rig(
        tmp_path,
        {
            "weak": [Delta(content="weak answer"), Delta(finish_reason="stop")],
            "strong": [Delta(content="strong answer"), Delta(finish_reason="stop")],
        },
        [weak, strong],
        reqs=requirements(agentic=True),
        rules=Rules(),
    )
    answer = chat(app_for(rig), ASK, tools=[TOOL])
    assert answer.status_code == 200
    assert answer.headers["x-headwaters-model"] == "strong"
