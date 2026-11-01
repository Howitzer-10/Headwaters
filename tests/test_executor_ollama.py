"""HDW-115: Ollama native /api/chat backend against a mocked transport (no real network)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from headwaters.core.executor.errors import ExecutorError, LocalScopeViolation, ProviderError
from headwaters.core.executor.ollama import OllamaBackend
from headwaters.core.executor.ollama_convert import build_payload
from headwaters.core.executor.types import BackendRequest, Delta, ExecOptions, ExecTarget

MESSAGES = [{"role": "user", "content": "hi"}]


def request(options: ExecOptions | None = None, num_ctx: int | None = 24576) -> BackendRequest:
    target = ExecTarget("qwen", "ollama", "qwen2.5-coder:14b", num_ctx=num_ctx)
    return BackendRequest(target, MESSAGES, None, options or ExecOptions(), "30m")


def ndjson(*objects: dict[str, Any]) -> bytes:
    return ("\n".join(json.dumps(o) for o in objects) + "\n").encode()


def collect(handler: Any) -> list[Delta]:
    backend = OllamaBackend("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))

    async def go() -> list[Delta]:
        return [d async for d in backend.stream(request())]

    return asyncio.run(go())


def test_payload_has_num_ctx_keep_alive_format_and_think() -> None:
    opts = ExecOptions(reasoning=True, json_schema={"type": "object"}, max_tokens=50, temperature=0)
    payload = build_payload(request(opts))
    assert payload["model"] == "qwen2.5-coder:14b"
    assert payload["stream"] is True
    assert payload["options"] == {"num_ctx": 24576, "temperature": 0, "num_predict": 50}
    assert payload["keep_alive"] == "30m"
    assert payload["format"] == {"type": "object"}
    assert payload["think"] is True


def test_payload_requires_computed_num_ctx() -> None:
    with pytest.raises(ExecutorError) as info:
        build_payload(request(num_ctx=None))
    assert info.value.code == "missing_num_ctx"


def _image_request(url: str) -> BackendRequest:
    content = [{"type": "image_url", "image_url": {"url": url}}]
    target = ExecTarget("qwen", "ollama", "qwen2.5-vl:7b", num_ctx=8192)
    return BackendRequest(target, [{"role": "user", "content": content}], None, ExecOptions(), "5m")


def test_remote_image_is_refused_and_nothing_is_sent_or_fetched() -> None:
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(str(req.url))
        return httpx.Response(200, content=ndjson({"done": True}))

    backend = OllamaBackend("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))

    async def go() -> list[Delta]:
        return [d async for d in backend.stream(_image_request("https://remote.example/x.png"))]

    with pytest.raises(ExecutorError) as info:
        asyncio.run(go())
    assert info.value.code == "image_url_not_local"
    assert hits == []  # not even the local /api/chat call, and no fetch of the image


def test_data_url_image_is_sent_to_the_local_model() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=ndjson({"message": {"content": "ok"}, "done": True}))

    backend = OllamaBackend("http://127.0.0.1:11434", transport=httpx.MockTransport(handler))

    async def go() -> list[Delta]:
        return [d async for d in backend.stream(_image_request("data:image/png;base64,QUJD"))]

    asyncio.run(go())
    assert len(seen) == 1
    assert seen[0]["messages"][0]["images"] == ["QUJD"]


def test_streaming_separates_thinking_from_content_and_reports_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/chat"
        seen.append(json.loads(req.content))
        done = {"message": {"role": "assistant", "content": ""}, "done": True}
        done |= {"done_reason": "stop", "prompt_eval_count": 11, "eval_count": 4}
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"role": "assistant", "thinking": "hmm"}, "done": False},
                {"message": {"role": "assistant", "content": "Hi"}, "done": False},
                done,
            ),
        )

    deltas = collect(handler)
    assert [d.reasoning for d in deltas] == ["hmm", "", ""]
    assert [d.content for d in deltas] == ["", "Hi", ""]
    assert deltas[-1].finish_reason == "stop"
    assert deltas[-1].usage is not None
    assert (deltas[-1].usage.prompt_tokens, deltas[-1].usage.completion_tokens) == (11, 4)
    assert seen[0]["options"]["num_ctx"] == 24576


def test_tool_calls_are_collected_with_json_arguments() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        call = {"function": {"name": "read_file", "arguments": {"path": "a.py"}}}
        end = {"message": {"role": "assistant", "content": ""}, "done": True}
        return httpx.Response(
            200,
            content=ndjson({"message": {"role": "assistant", "tool_calls": [call]}}, end),
        )

    deltas = collect(handler)
    part = deltas[0].tool_calls[0]
    assert (part.name, json.loads(part.arguments)) == ("read_file", {"path": "a.py"})
    assert deltas[-1].finish_reason == "tool_calls"


def test_http_error_becomes_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"error":"model not found"}')

    with pytest.raises(ProviderError) as info:
        collect(handler)
    assert info.value.status == 404
    assert "model not found" in info.value.body


def test_error_object_in_stream_becomes_provider_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson({"error": "llama runner crashed"}))

    with pytest.raises(ProviderError) as info:
        collect(handler)
    assert info.value.kind == "stream_error"


def test_connection_failure_and_timeout_are_mapped() -> None:
    def refuse(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def slow(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ProviderError) as refused:
        collect(refuse)
    assert refused.value.kind == "connection"
    with pytest.raises(ProviderError) as timed_out:
        collect(slow)
    assert timed_out.value.kind == "timeout"


def test_backend_refuses_non_loopback_host_at_construction() -> None:
    with pytest.raises(LocalScopeViolation):
        OllamaBackend("http://10.1.2.3:11434")
