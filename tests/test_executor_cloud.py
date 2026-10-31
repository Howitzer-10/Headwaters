"""HDW-115: litellm backend and mapping helpers, with litellm fully mocked."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.executor.cloud import LiteLLMBackend, build_kwargs
from headwaters.core.executor.cloud_map import (
    attribution_headers,
    delta_from_chunk,
    map_exception,
    reasoning_kwargs,
)
from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import BackendRequest, Delta, ExecOptions, ExecTarget
from headwaters.core.rules import AttributionRules
from tests.executor_support import chunk

TARGET = ExecTarget("m", "openrouter", "openrouter/acme/m:free")
MESSAGES = [{"role": "user", "content": "hi"}]


def req(options: ExecOptions | None = None, **kw: Any) -> BackendRequest:
    return BackendRequest(TARGET, MESSAGES, None, options or ExecOptions(), "30m", **kw)


class FakeLiteLLM:
    def __init__(self, chunks: list[Any], fail: Exception | None = None) -> None:
        self.chunks = chunks
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.fail is not None:
            raise self.fail

        async def gen() -> Any:
            for c in self.chunks:
                yield c

        return gen()


def collect(
    module: FakeLiteLLM | LiteLLMBackend, request: BackendRequest | None = None
) -> list[Delta]:
    backend = module if isinstance(module, LiteLLMBackend) else LiteLLMBackend(module)

    async def go() -> list[Delta]:
        return [d async for d in backend.stream(request or req())]

    return asyncio.run(go())


def test_reasoning_parameter_per_provider() -> None:
    assert reasoning_kwargs("openrouter", None) == {}
    assert reasoning_kwargs("openrouter", True) == {
        "extra_body": {"reasoning": {"effort": "medium"}}
    }
    assert reasoning_kwargs("openrouter", "high") == {
        "extra_body": {"reasoning": {"effort": "high"}}
    }
    assert reasoning_kwargs("openrouter", False) == {
        "extra_body": {"reasoning": {"enabled": False}}
    }
    assert reasoning_kwargs("anthropic", "low") == {"reasoning_effort": "low"}
    assert reasoning_kwargs("anthropic", True) == {"reasoning_effort": "medium"}
    assert reasoning_kwargs("anthropic", False) == {}


def test_attribution_is_opt_in() -> None:
    assert attribution_headers(AttributionRules()) == {}
    on = AttributionRules(referer="https://hw.example", title="HW")
    assert attribution_headers(on) == {
        "HTTP-Referer": "https://hw.example",
        "X-OpenRouter-Title": "HW",
    }


def test_build_kwargs_streams_without_retries_and_passes_options() -> None:
    options = ExecOptions(reasoning="high", json_schema={"type": "object"}, max_tokens=99)
    kwargs = build_kwargs(req(options, headers={"HTTP-Referer": "x"}, api_key="k"))
    assert kwargs["model"] == "openrouter/acme/m:free"
    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["num_retries"] == 0
    assert kwargs["max_retries"] == 0
    assert kwargs["api_key"] == "k"
    assert kwargs["extra_headers"] == {"HTTP-Referer": "x"}
    assert kwargs["max_tokens"] == 99
    assert kwargs["extra_body"] == {"reasoning": {"effort": "high"}}
    assert kwargs["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_stream_captures_reasoning_separately_from_content_and_usage() -> None:
    usage = SimpleNamespace(prompt_tokens=9, completion_tokens=2)
    module = FakeLiteLLM(
        [
            chunk(reasoning="because"),
            chunk(content="ok"),
            chunk(finish="stop"),
            SimpleNamespace(choices=[], usage=usage),
        ]
    )
    deltas = collect(module)
    assert [d.reasoning for d in deltas][:2] == ["because", ""]
    assert [d.content for d in deltas][:2] == ["", "ok"]
    assert deltas[2].finish_reason == "stop"
    assert deltas[3].usage is not None
    assert deltas[3].usage.prompt_tokens == 9
    assert module.calls[0]["stream"] is True


def test_delta_reads_openrouter_reasoning_field_as_fallback() -> None:
    delta = SimpleNamespace(content=None, reasoning_content=None, reasoning="why", tool_calls=None)
    c = SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
    assert delta_from_chunk(c).reasoning == "why"


def test_tool_call_fragments_are_mapped() -> None:
    function = SimpleNamespace(name="read_file", arguments='{"a":')
    call = SimpleNamespace(index=0, id="c1", function=function)
    delta = delta_from_chunk(chunk(tool_calls=[call]))
    part = delta.tool_calls[0]
    assert (part.index, part.id, part.name, part.arguments) == (0, "c1", "read_file", '{"a":')


def test_mid_stream_error_chunk_raises_provider_error() -> None:
    with pytest.raises(ProviderError) as info:
        delta_from_chunk(chunk(finish="error"))
    assert info.value.kind == "stream_error"


class FakeRateLimit(Exception):
    status_code = 429
    message = "rate limited"
    response = SimpleNamespace(headers={"Retry-After": "12"})


class Timeout(Exception):
    pass


class APIConnectionError(Exception):
    pass


def test_exception_mapping_keeps_status_headers_and_body() -> None:
    mapped = map_exception(FakeRateLimit())
    assert (mapped.status, mapped.body) == (429, "rate limited")
    assert mapped.headers["Retry-After"] == "12"
    assert map_exception(Timeout()).kind == "timeout"
    assert map_exception(APIConnectionError()).kind == "connection"
    assert map_exception(ValueError("x")).status == 500


def test_backend_maps_litellm_failures() -> None:
    with pytest.raises(ProviderError) as info:
        collect(FakeLiteLLM([], fail=FakeRateLimit()))
    assert info.value.status == 429


def test_backend_loads_litellm_lazily_with_local_cost_map_and_no_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeLiteLLM([chunk(content="x"), chunk(finish="stop")])
    imported: list[str] = []

    def fake_import(name: str) -> Any:
        imported.append(name)
        return fake

    monkeypatch.delenv("LITELLM_LOCAL_MODEL_COST_MAP", raising=False)
    monkeypatch.setattr("headwaters.core.executor.cloud.importlib.import_module", fake_import)
    backend = LiteLLMBackend()
    assert imported == []  # nothing imported until the first cloud call
    deltas = collect(backend)
    assert imported == ["litellm"]
    assert deltas[0].content == "x"
    assert os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] == "True"
    assert fake.telemetry is False  # type: ignore[attr-defined]
