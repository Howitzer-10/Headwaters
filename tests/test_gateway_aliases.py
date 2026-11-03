"""HDW-304: the alias table, explicit model pass-through and ``x-headwaters-pin``.

Both faces share ``headwaters.gateway.aliases``, so the semantics are checked once through each
face: an alias resolves before the picker syntax, an unmapped name is an explicit model that
passes through, and the pin header only stops the failover for an explicit model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import ModelsRules, Rules
from headwaters.gateway.aliases import PIN_HEADER, requested, resolve_alias
from headwaters.gateway.openai_routes import build_gateway_app
from tests.rebuild_support import ASK, TOKEN, DaemonRig

BASE = "http://127.0.0.1:4000"
AUTH = {"authorization": f"Bearer {TOKEN}"}
ALIASES = {
    "gpt-4o": "headwaters/free+verify",
    "claude-3-5-sonnet": "headwaters/auto",
    "my-model": "qwen:14b",
}


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DaemonRig]:
    made = DaemonRig(tmp_path, monkeypatch)
    try:
        yield made
    finally:
        made.close()


class Recording:
    """A router stand-in: keep the request it was given, then refuse with ``pin_rejected``."""

    def __init__(self) -> None:
        self.seen: RouteRequest | None = None

    def handle(self, request: RouteRequest) -> Any:
        self.seen = request
        raise NoRouteError("pin_rejected", "no route for the test")


def app_for(router: Any, rules: Rules) -> ASGIApp:
    return build_gateway_app(token=TOKEN, router=lambda: router, rules=lambda: rules)


def rig_app(rig: DaemonRig, rules: Rules) -> ASGIApp:
    """The daemon's live router (itself a provider) with the rules under test."""
    return build_gateway_app(token=TOKEN, router=rig.daemon.gateway_router, rules=lambda: rules)


def rules_with(aliases: dict[str, str]) -> Rules:
    return Rules(models=ModelsRules(aliases=aliases))


def post(app: ASGIApp, path: str, body: dict[str, Any], **headers: str) -> httpx.Response:
    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await http.post(path, json=body, headers={**AUTH, **headers})

    return asyncio.run(go())


def chat(app: ASGIApp, model: str, **headers: str) -> httpx.Response:
    return post(app, "/v1/chat/completions", {"model": model, "messages": ASK}, **headers)


def message(app: ASGIApp, model: str, **headers: str) -> httpx.Response:
    body = {"model": model, "max_tokens": 64, "messages": ASK}
    return post(app, "/v1/messages", body, **headers)


def seen_request(app: ASGIApp, model: str, **headers: str) -> RouteRequest:
    """Route true with the recording stand-in, then hand back what the face asked for."""
    router = Recording()
    chat(app_for(router, rules_with(ALIASES)), model, **headers)
    assert router.seen is not None
    return router.seen


# ------------------------------------------------------------------------------- the resolver


def test_an_alias_is_matched_exactly_then_case_insensitively() -> None:
    assert resolve_alias("gpt-4o", ALIASES) == "headwaters/free+verify"
    assert resolve_alias("GPT-4O", ALIASES) == "headwaters/free+verify"
    assert resolve_alias("  claude-3-5-sonnet ", ALIASES) == "headwaters/auto"
    assert resolve_alias("unmapped-model", ALIASES) == "unmapped-model"


def test_a_picker_model_keeps_its_suffixes_after_the_alias() -> None:
    asked = requested("gpt-4o", {}, rules_with(ALIASES))
    assert asked.parsed is not None
    assert (asked.parsed.scope, asked.parsed.verify) == ("free", "always")
    assert (asked.pin, asked.strict) == (None, False)


def test_an_unmapped_name_passes_through_as_the_model_asked_for() -> None:
    asked = requested("some-vendor/model-1", {}, rules_with(ALIASES))
    assert asked.parsed is None
    assert (asked.pin, asked.strict) == ("some-vendor/model-1", False)


def test_the_pin_header_only_changes_an_explicit_model() -> None:
    pinned = requested("some-vendor/model-1", {PIN_HEADER: "true"}, rules_with(ALIASES))
    assert (pinned.pin, pinned.strict) == ("some-vendor/model-1", True)
    # An IDE may set the header globally, so a picker model ignores it instead of failing.
    picker = requested("headwaters/auto", {PIN_HEADER: "true"}, rules_with(ALIASES))
    assert (picker.pin, picker.strict) == (None, False)
    for value in ("0", "false", "", "no"):
        asked = requested("m", {PIN_HEADER: value}, rules_with(ALIASES))
        assert asked.strict is False


# ----------------------------------------------------------------------------- the OpenAI face


def test_chat_completions_resolves_an_alias_to_a_picker_model() -> None:
    seen = seen_request(app_for(Recording(), rules_with(ALIASES)), "GPT-4o")
    assert (seen.scope, seen.strategy, seen.verify) == ("free", None, "always")
    assert (seen.pin, seen.pin_strict) == (None, False)


def test_chat_completions_pins_an_alias_target_the_picker_does_not_know() -> None:
    seen = seen_request(app_for(Recording(), rules_with(ALIASES)), "my-model")
    assert (seen.scope, seen.pin, seen.pin_strict) == (None, "qwen:14b", False)


def test_chat_completions_reads_the_pin_header_for_an_explicit_model() -> None:
    seen = seen_request(
        app_for(Recording(), rules_with(ALIASES)), "gpt-4-turbo", **{PIN_HEADER: "true"}
    )
    assert (seen.pin, seen.pin_strict) == ("gpt-4-turbo", True)


def test_chat_completions_refuses_an_alias_whose_target_is_not_a_model() -> None:
    app = app_for(Recording(), rules_with({"broken": "headwaters/nope"}))
    answer = chat(app, "broken")
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "unknown_model"


# -------------------------------------------------------------------------- the Anthropic face


def test_messages_resolves_the_same_aliases() -> None:
    router = Recording()
    message(app_for(router, rules_with(ALIASES)), "gpt-4o")
    assert router.seen is not None
    assert (router.seen.scope, router.seen.verify) == ("free", "always")
    pinned_router = Recording()
    message(app_for(pinned_router, rules_with(ALIASES)), "my-model", **{PIN_HEADER: "true"})
    assert pinned_router.seen is not None
    assert (pinned_router.seen.pin, pinned_router.seen.pin_strict) == ("qwen:14b", True)


# --------------------------------------------------------------------------- through the router


def test_an_aliased_explicit_model_answers_from_that_model(rig: DaemonRig) -> None:
    app = rig_app(rig, rules_with({"my-model": "qwen:14b"}))
    answer = chat(app, "my-model")
    assert answer.status_code == 200
    assert answer.headers["x-headwaters-model"] == "qwen:14b"
    assert answer.headers["x-headwaters-reason"] == "pin"
    assert rig.backends[-1].requests[-1].target.model_id == "qwen:14b"


def test_an_aliased_picker_model_answers_through_the_router(rig: DaemonRig) -> None:
    app = rig_app(rig, rules_with(ALIASES))
    answer = chat(app, "claude-3-5-sonnet")
    assert answer.status_code == 200
    assert answer.json()["choices"][0]["message"]["content"] == "answer from engine 1"
    # The same alias, with its ``+verify`` suffix, on the Messages face.
    messages = message(app, "gpt-4o")
    assert messages.status_code == 200
    assert messages.json()["type"] == "message"
