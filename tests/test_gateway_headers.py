"""HDW-302: routing headers, transparent pre-token retry and the terminal stream event.

The body stays an OpenAI completion (FU-123-2 asked for headers precisely so clients keep
parsing); the only body addition is the ``headwaters`` object on the final SSE chunk, which is
where the outcome of a streamed answer's check has to arrive (FU-130-2).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from headwaters.core.advice import BELOW_FLOOR_WARNING, STREAMED_VERIFY_NOTE, UNCHECKED_NOTE
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import Delta
from headwaters.core.router import Router
from headwaters.core.rules import Rules
from headwaters.core.state_misc import get_session
from headwaters.gateway.openai_routes import (
    ADVICE_HEADER,
    BELOW_FLOOR_HEADER,
    SUGGESTION_HEADER,
    VERIFY_HEADER,
    build_gateway_app,
)
from tests.executor_support import Step
from tests.rebuild_support import TOKEN
from tests.router_rig import Rig
from tests.router_support import NO_CREDIT, OK, RATE_LIMITED, candidate
from tests.test_router import FREE, LOCAL, pool

ASK: list[dict[str, Any]] = [{"role": "user", "content": "write a function that adds two numbers"}]
URL = "/v1/chat/completions"
BASE_URL = "http://127.0.0.1:4000"
UNAVAILABLE = ProviderError(503, {}, "")


def app_for(router: Router | None, rules: Rules | None = None) -> ASGIApp:
    live = rules if rules is not None else Rules()
    return build_gateway_app(token=TOKEN, router=lambda: router, rules=lambda: live)


def post(
    app: ASGIApp, body: dict[str, Any], headers: dict[str, str] | None = None
) -> httpx.Response:
    """One request through the ASGI app, so the response headers are readable."""

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        sent = {"Authorization": f"Bearer {TOKEN}", **(headers or {})}
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            return await client.post(URL, json=body, headers=sent)

    return asyncio.run(go())


def chat(model: str = "headwaters/auto", **extra: Any) -> dict[str, Any]:
    return {"model": model, "messages": ASK, **extra}


def below_floor_rig(tmp_path: Path) -> Rig:
    """A local-only pick under the code floor: the case that carries suggestions (HDW-123)."""
    return Rig(tmp_path, {LOCAL: OK}, [candidate(LOCAL, "local", 60.0)])


def frames(response: httpx.Response) -> list[dict[str, Any]]:
    """Every SSE data frame except the ``[DONE]`` sentinel."""
    sent = []
    for block in response.text.split("\n\n"):
        if not block.startswith("data: "):
            continue
        payload = block[len("data: ") :]
        if payload != "[DONE]":
            sent.append(json.loads(payload))
    return sent


def streamed_text(response: httpx.Response) -> str:
    return "".join(
        (frame["choices"][0]["delta"].get("content") or "")
        for frame in frames(response)
        if frame.get("choices") and frame["choices"][0].get("delta")
    )


def finish_frame(response: httpx.Response) -> dict[str, Any]:
    return next(
        f for f in frames(response) if f.get("choices") and f["choices"][0]["finish_reason"]
    )


def test_the_response_names_the_model_tier_scope_and_rung(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    response = post(app_for(rig.router), chat())
    assert response.status_code == 200
    body = response.json()
    assert response.headers["x-headwaters-model"] == body["model"]
    assert response.headers["x-headwaters-tier"]
    assert response.headers["x-headwaters-scope"]
    assert response.headers["x-headwaters-reason"]
    assert BELOW_FLOOR_HEADER not in response.headers
    assert body["choices"][0]["message"]["content"] == "ok answer"
    assert "headwaters" not in body  # the body keeps its OpenAI shape (FU-123-2)


@pytest.mark.parametrize(
    "failure", [RATE_LIMITED, NO_CREDIT, UNAVAILABLE], ids=["429", "402", "503"]
)
def test_a_failure_before_the_first_token_retries_invisibly(
    tmp_path: Path, failure: ProviderError
) -> None:
    rig = Rig(tmp_path, {FREE: [failure], LOCAL: OK}, pool(local=84.0))
    response = post(app_for(rig.router), chat(stream=True))
    assert response.status_code == 200
    assert rig.dispatch.calls[-1] == LOCAL  # 503 is retried on the same model first
    assert FREE in rig.dispatch.calls
    assert response.headers["x-headwaters-model"] == LOCAL
    assert streamed_text(response) == "ok answer"
    assert "error" not in response.text  # the retry never reaches the client
    assert response.text.rstrip().endswith("data: [DONE]")


def test_a_mid_stream_failure_is_a_clean_terminal_error(tmp_path: Path) -> None:
    broken: list[Step] = [Delta(content="par"), ProviderError(500, {}, "", kind="stream_error")]
    rig = Rig(tmp_path, {FREE: broken, LOCAL: OK}, pool(local=84.0))
    response = post(app_for(rig.router), chat(stream=True))
    assert response.status_code == 200
    assert rig.dispatch.calls == [FREE]  # never switches after the first token
    assert streamed_text(response) == "par"
    error = next(f for f in frames(response) if "error" in f)["error"]
    assert error["message"]
    assert error["type"]
    # The answer ends with the error frame and [DONE], never with a successful finish chunk.
    assert frames(response)[-1] == {"error": error}
    assert not any(f.get("choices") and f["choices"][0]["finish_reason"] for f in frames(response))
    assert response.text.rstrip().endswith("data: [DONE]")


def test_a_below_floor_answer_carries_the_flag_and_the_first_suggestion(tmp_path: Path) -> None:
    rig = below_floor_rig(tmp_path)
    response = post(app_for(rig.router), chat("headwaters/local"))
    assert response.status_code == 200
    assert response.headers[BELOW_FLOOR_HEADER] == "true"
    assert response.headers[SUGGESTION_HEADER] in {"pull_model", "split_task", "use_cloud"}
    assert set(response.json()) == {"id", "object", "created", "model", "choices", "usage"}


def test_an_unchecked_answer_is_never_shown_as_verified(tmp_path: Path) -> None:
    rig = below_floor_rig(tmp_path)
    response = post(app_for(rig.router, Rules(advice="inline")), chat("headwaters/local"))
    assert response.status_code == 200
    assert response.headers[VERIFY_HEADER] == "unchecked"
    assert response.headers[ADVICE_HEADER] == f"{BELOW_FLOOR_WARNING} {UNCHECKED_NOTE}"


def test_a_streamed_answer_reports_pending_then_the_real_outcome(tmp_path: Path) -> None:
    rig = below_floor_rig(tmp_path)
    response = post(
        app_for(rig.router, Rules(advice="inline")), chat("headwaters/local", stream=True)
    )
    assert response.status_code == 200
    # The headers are written before the answer finishes, so the check is still pending...
    assert response.headers[VERIFY_HEADER] == "pending"
    assert STREAMED_VERIFY_NOTE in response.headers[ADVICE_HEADER]
    # ...and the terminal event carries what actually happened, never "verified".
    meta = finish_frame(response)["headwaters"]
    assert meta["verify_status"] == "unchecked"
    assert meta["below_floor"] is True
    assert meta["suggestion"]
    assert UNCHECKED_NOTE in meta["advice"]
    assert BELOW_FLOOR_WARNING in meta["advice"]


def test_the_session_header_is_the_session_key(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    response = post(app_for(rig.router), chat(), headers={"x-session-id": "ide-42"})
    assert response.status_code == 200
    session = get_session(rig.db, "id:ide-42")
    assert session is not None
    assert session.pinned_model == response.headers["x-headwaters-model"]


def test_advice_stays_off_the_wire_when_it_is_off(tmp_path: Path) -> None:
    rig = below_floor_rig(tmp_path)
    response = post(app_for(rig.router), chat("headwaters/local"))
    assert response.status_code == 200
    assert response.headers[BELOW_FLOOR_HEADER] == "true"  # the flag is not advice
    assert ADVICE_HEADER not in response.headers


def test_header_only_advice_reaches_the_header_and_not_the_chunk(tmp_path: Path) -> None:
    rig = below_floor_rig(tmp_path)
    response = post(
        app_for(rig.router, Rules(advice="header_only")), chat("headwaters/local", stream=True)
    )
    assert response.status_code == 200
    assert BELOW_FLOOR_WARNING in response.headers[ADVICE_HEADER]
    assert finish_frame(response)["headwaters"]["advice"] is None
