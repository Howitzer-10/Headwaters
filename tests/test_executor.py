"""HDW-115: executor streaming, reasoning capture, tool calls and wall-classified errors."""

from __future__ import annotations

import pytest

from headwaters.core.executor.errors import ExecutorError, ProviderError
from headwaters.core.executor.types import Delta, ExecOptions, ToolCallDelta, Usage
from headwaters.core.rules import Rules
from tests.executor_support import (
    ScriptedBackend,
    Step,
    decision,
    kinds,
    make_executor,
    run_once,
    stream_events,
)

STREAM: list[Step] = [
    Delta(reasoning="think "),
    Delta(reasoning="hard"),
    Delta(content="Hel"),
    Delta(content="lo"),
    Delta(finish_reason="stop", usage=Usage(7, 3)),
]


def test_stream_events_order_and_reasoning_separate() -> None:
    events = stream_events(make_executor(ScriptedBackend(STREAM)), decision())
    assert kinds(events) == [
        "first_token",
        "reasoning",
        "reasoning",
        "content",
        "content",
        "done",
    ]
    done = events[-1].result
    assert done is not None
    assert done.content == "Hello"
    assert done.reasoning == "think hard"
    assert done.usage == Usage(7, 3)
    assert done.finish_reason == "stop"
    assert done.first_token_s is not None


def test_first_token_event_is_emitted_exactly_once_and_first() -> None:
    events = stream_events(make_executor(ScriptedBackend(STREAM)), decision())
    assert kinds(events).count("first_token") == 1
    assert events[0].kind == "first_token"


def test_non_streaming_collects_result() -> None:
    result = run_once(make_executor(ScriptedBackend(STREAM)), decision())
    assert (result.content, result.reasoning) == ("Hello", "think hard")
    assert result.model == "openrouter/acme/m:free"


def test_tool_calls_are_assembled_from_fragments() -> None:
    script: list[Step] = [
        Delta(tool_calls=(ToolCallDelta(0, "c1", "read_file", '{"pa'),)),
        Delta(tool_calls=(ToolCallDelta(0, None, None, 'th": "a.py"}'),)),
        Delta(finish_reason="tool_calls"),
    ]
    events = stream_events(make_executor(ScriptedBackend(script)), decision())
    assert kinds(events) == ["first_token", "tool_call", "done"]
    call = events[1].tool_call
    assert call is not None
    assert (call.id, call.name, call.arguments) == ("c1", "read_file", '{"path": "a.py"}')


def test_stream_without_output_has_no_first_token() -> None:
    result = run_once(make_executor(ScriptedBackend([Delta(finish_reason="stop")])), decision())
    assert result.content == ""
    assert result.first_token_s is None


def test_backend_is_closed_after_run() -> None:
    backend = ScriptedBackend(STREAM)
    run_once(make_executor(backend), decision())
    assert backend.closed


def test_local_request_carries_keep_alive_num_ctx_and_schema() -> None:
    backend = ScriptedBackend(STREAM)
    opts = ExecOptions(reasoning=True, json_schema={"type": "object"}, max_tokens=64)
    run_once(make_executor(backend), decision("local", "qwen"), opts)
    request = backend.requests[0]
    assert request.keep_alive == Rules().tiers.local.keep_alive
    assert request.target.num_ctx == 16384
    assert request.options.json_schema == {"type": "object"}


def test_error_before_first_token_raises_with_wall() -> None:
    err = ProviderError(429, {"retry-after": "30"}, "slow down")
    with pytest.raises(ExecutorError) as info:
        stream_events(make_executor(ScriptedBackend([err])), decision())
    exc = info.value
    assert exc.code == "rate_limit"
    assert exc.before_first_token is True
    assert exc.wall is not None
    assert exc.wall.is_wall
    assert exc.wall.retry_after == pytest.approx(30, abs=1)
    assert "slow down" not in str(exc)
    assert "slow down" not in repr(exc.as_dict())


def test_error_after_first_token_is_terminal_event_not_exception() -> None:
    script: list[Step] = [Delta(content="par"), ProviderError(503, None, "overloaded")]
    events = stream_events(make_executor(ScriptedBackend(script)), decision())
    assert kinds(events) == ["first_token", "content", "error"]
    error = events[-1].error
    assert error is not None
    assert error.before_first_token is False
    assert error.code == "overloaded"


def test_non_streaming_error_after_tokens_raises() -> None:
    ex = make_executor(ScriptedBackend([Delta(content="x"), ProviderError(500, None, "boom")]))
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision())
    assert info.value.code == "provider_error"
    assert info.value.before_first_token is False


@pytest.mark.parametrize(
    ("status", "code"),
    [(402, "exhausted"), (503, "overloaded"), (418, "provider_error")],
)
def test_errors_map_through_wall_classifier(status: int, code: str) -> None:
    ex = make_executor(ScriptedBackend([ProviderError(status, None, "")]))
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision())
    assert info.value.code == code
    assert info.value.status == status


def test_connection_error_code_names_ollama_for_local() -> None:
    ex = make_executor(ScriptedBackend([ProviderError(0, None, "", kind="connection")]))
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision("local", "qwen"))
    assert info.value.code == "connection_error"
    assert info.value.provider == "ollama"
