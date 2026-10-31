"""HDW-121: executor, backend and layer-3 classifier refuse Ollama cloud-proxied models."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer.classifier import TinyLlmClassifier
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.executor.errors import LocalScopeViolation
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.guard import enforce_scope
from headwaters.core.executor.ollama import OllamaBackend
from headwaters.core.executor.types import BackendRequest, ExecOptions
from headwaters.core.ollama_remote import remote_patterns_from
from headwaters.core.rules import Rules
from tests.executor_support import (
    MESSAGES,
    WALLS,
    ScriptedBackend,
    decision,
    make_executor,
    target_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PATTERNS = remote_patterns_from(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))


def test_private_lock_routing_to_a_cloud_model_raises_before_any_backend() -> None:
    backend = ScriptedBackend([])
    cloud_calls: list[int] = []
    ex = make_executor(
        backend,
        rules=Rules(private_lock=True),
        cloud_calls=cloud_calls,
        remote_patterns=PATTERNS,
    )
    bad = decision("local", "gpt-oss:120b-cloud")
    with pytest.raises(LocalScopeViolation) as info:
        asyncio.run(ex.run(bad, MESSAGES, None, False))
    assert info.value.code == "local_scope_violation"
    assert backend.requests == []
    assert cloud_calls == []


def test_enforce_scope_refuses_a_remote_model_in_a_local_decision() -> None:
    d = decision("local", "qwen:cloud")
    enforce_scope(d, Rules())  # no patterns known: nothing to refuse
    with pytest.raises(LocalScopeViolation):
        enforce_scope(d, Rules(), PATTERNS)
    enforce_scope(replace(d, model="qwen:8b"), Rules(), PATTERNS)


def test_executor_refuses_when_the_resolved_route_is_remote() -> None:
    backend = ScriptedBackend([])
    d = decision("local", "friendly-name")  # the model id looks fine, the route is remote
    ex = Executor(
        Rules(),
        WALLS,
        lambda dec: target_for(dec, route="qwen3:480b-cloud"),
        local_factory=lambda host: backend,
        remote_patterns=PATTERNS,
    )
    with pytest.raises(LocalScopeViolation):
        asyncio.run(ex.run(d, MESSAGES, None, False))
    assert backend.requests == []


def test_ollama_backend_refuses_a_remote_route() -> None:
    backend = OllamaBackend("http://127.0.0.1:11434", remote_patterns=PATTERNS)
    request = BackendRequest(
        target_for(decision("local", "m-cloud"), route="m-cloud"),
        MESSAGES,
        None,
        ExecOptions(),
        "5m",
    )

    async def go() -> None:
        async for _ in backend.stream(request):
            pass

    with pytest.raises(LocalScopeViolation):
        asyncio.run(go())


def test_default_local_backend_carries_the_patterns() -> None:
    ex = Executor(Rules(), WALLS, lambda d: target_for(d), remote_patterns=PATTERNS)
    backend = ex._local_factory("http://127.0.0.1:11434")
    assert isinstance(backend, OllamaBackend)
    assert backend._remote_patterns == PATTERNS


class CountingClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, payload: Any, timeout_s: float) -> Any:
        self.calls += 1
        return {"message": {"content": '{"task":"code","complexity":2,"risk":"low"}'}}


def test_layer3_classifier_refuses_a_remote_model() -> None:
    client = CountingClient()
    refused = TinyLlmClassifier("tiny:cloud", client=client, remote_patterns=PATTERNS)
    assert refused.classify("hello") is None
    assert client.calls == 0
    allowed = TinyLlmClassifier("tiny:1b", client=client, remote_patterns=PATTERNS)
    assert allowed.classify("hello") is not None
    assert client.calls == 1
