"""HDW-115: local-scope guard (v3.3 section D) and cloud attribution headers."""

from __future__ import annotations

from dataclasses import replace

import pytest

from headwaters.core.executor.errors import LocalScopeViolation, ScopeViolation
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.guard import assert_loopback_url
from headwaters.core.executor.types import Delta, ExecResult
from headwaters.core.policy.types import RouteDecision
from headwaters.core.rules import Rules
from headwaters.core.scope import FREE_TIERS, ScopeDecision
from tests.executor_support import ScriptedBackend, decision, make_executor, run_once

SCRIPT = [Delta(content="Hello"), Delta(finish_reason="stop")]


def run(executor: Executor, d: RouteDecision) -> ExecResult:
    return run_once(executor, d)


def rules_with_host(host: str) -> Rules:
    return Rules.model_validate({"tiers": {"local": {"host": host}}})


def test_local_scope_never_constructs_cloud_backend() -> None:
    cloud_calls: list[int] = []
    ex = make_executor(ScriptedBackend(SCRIPT), cloud_calls=cloud_calls)
    run(ex, decision("local", "qwen"))
    assert cloud_calls == []


def test_cloud_tier_in_local_scope_is_refused_before_any_backend() -> None:
    cloud_calls: list[int] = []
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, cloud_calls=cloud_calls)
    bad = replace(decision("free_cloud"), scope=decision("local").scope)
    with pytest.raises(LocalScopeViolation):
        run(ex, bad)
    assert cloud_calls == []
    assert backend.requests == []


@pytest.mark.parametrize(
    "host",
    [
        "http://example.com:11434",
        "http://192.168.1.20:11434",
        "http://10.0.0.5:11434",
        "https://localhost:11434",
        "http://localhost.evil.com:11434",
        "http://0.0.0.0:11434",
        "http://[::2]:11434",
        "ftp://127.0.0.1",
        "http://127.0.0.1:notaport",
        "localhost:11434",
        "",
    ],
)
def test_non_loopback_ollama_host_raises_in_local_scope(host: str) -> None:
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, rules=rules_with_host(host))
    with pytest.raises(LocalScopeViolation):
        run(ex, decision("local", "qwen"))
    assert backend.requests == []


@pytest.mark.parametrize(
    "host",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://127.5.5.5:11434",
        "http://LOCALHOST:11434/",
    ],
)
def test_loopback_ollama_hosts_are_accepted(host: str) -> None:
    ex = make_executor(ScriptedBackend(SCRIPT), rules=rules_with_host(host))
    assert run(ex, decision("local", "qwen")).content == "Hello"


def test_assert_loopback_url_strips_trailing_slash() -> None:
    assert assert_loopback_url("http://localhost:11434/") == "http://localhost:11434"


def test_airplane_mode_blocks_cloud_but_allows_local() -> None:
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, rules=Rules(airplane=True))
    with pytest.raises(ScopeViolation):
        run(ex, decision("free_cloud"))
    assert backend.requests == []
    assert run(ex, decision("local", "qwen")).content == "Hello"


def test_tier_outside_scope_is_refused() -> None:
    free = ScopeDecision("free", FREE_TIERS, "cost_saver", "agentic")
    bad = replace(decision("paid", "anthropic/claude-x"), scope=free)
    with pytest.raises(ScopeViolation):
        run(make_executor(ScriptedBackend(SCRIPT)), bad)


def test_attribution_is_off_by_default_and_openrouter_only() -> None:
    backend = ScriptedBackend(SCRIPT)
    run(make_executor(backend), decision())
    assert backend.requests[0].headers == {}  # no telemetry by default

    rules = Rules.model_validate({"attribution": {"referer": "https://hw.example", "title": "HW"}})
    on = ScriptedBackend(SCRIPT)
    run(make_executor(on, rules=rules), decision())
    assert on.requests[0].headers == {
        "HTTP-Referer": "https://hw.example",
        "X-OpenRouter-Title": "HW",
    }
    other = ScriptedBackend(SCRIPT)
    run(make_executor(other, rules=rules), decision("paid", "anthropic/claude-x"))
    assert other.requests[0].headers == {}


def test_api_key_comes_from_the_key_resolver() -> None:
    backend = ScriptedBackend(SCRIPT)
    ex = make_executor(backend, api_key_for=lambda provider: f"key-for-{provider}")
    run(ex, decision())
    assert backend.requests[0].api_key == "key-for-openrouter"
