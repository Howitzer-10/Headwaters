"""HDW-115: first-token timeout is longer for a cold local model; idle timeout after streaming."""

from __future__ import annotations

import pytest

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import Delta, ExecutorConfig
from tests.executor_support import (
    ScriptedBackend,
    Step,
    decision,
    kinds,
    make_executor,
    run_once,
    stream_events,
)

FAST = ExecutorConfig(
    cloud_first_token_s=0.05,
    local_warm_first_token_s=0.05,
    local_cold_first_token_s=1.0,
    stream_idle_s=0.05,
)
SLOW_START: list[Step] = [("sleep", 0.2), Delta(content="late"), Delta(finish_reason="stop")]


def test_cloud_first_token_timeout() -> None:
    ex = make_executor(ScriptedBackend(SLOW_START), config=FAST)
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision())
    assert info.value.code == "timeout"
    assert info.value.before_first_token is True


def test_warm_local_model_uses_short_timeout() -> None:
    ex = make_executor(ScriptedBackend(SLOW_START), config=FAST, cold=False)
    with pytest.raises(ExecutorError) as info:
        run_once(ex, decision("local", "qwen"))
    assert info.value.code == "timeout"


@pytest.mark.parametrize("cold", [True, None])
def test_cold_or_unknown_local_model_waits_longer(cold: bool | None) -> None:
    ex = make_executor(ScriptedBackend(SLOW_START), config=FAST, cold=cold)
    assert run_once(ex, decision("local", "qwen")).content == "late"


def test_default_config_cold_limit_exceeds_warm_limit() -> None:
    cfg = ExecutorConfig()
    assert cfg.local_cold_first_token_s > cfg.local_warm_first_token_s


def test_idle_timeout_after_first_token_is_terminal_event() -> None:
    script: list[Step] = [Delta(content="a"), ("sleep", 0.3), Delta(content="b")]
    events = stream_events(make_executor(ScriptedBackend(script), config=FAST), decision())
    assert kinds(events) == ["first_token", "content", "error"]
    assert events[-1].error is not None
    assert events[-1].error.code == "timeout"
