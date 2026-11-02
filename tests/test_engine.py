"""HDW-201: the production wiring (``build_engine``) and the target resolver.

Covers FU-121-2 / recheck R3: every executor and Ollama backend the daemon builds must carry the
catalog's cloud-proxied-model patterns (D13).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from headwaters.core.catalog.loader import load_catalog
from headwaters.core.catalog.schema import Catalog
from headwaters.core.engine import TargetBuilder, build_engine
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import BackendRequest, Delta
from headwaters.core.ollama_remote import is_remote_name
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_types import RouteFailed, RouteRequest
from headwaters.core.rules import OpenAICompatEndpoint, Rules
from headwaters.core.state import open_database
from tests.executor_support import decision
from tests.test_ollama_remote_exec import PATTERNS

REPO_ROOT = Path(__file__).resolve().parent.parent


def _catalog() -> Catalog:
    return load_catalog(REPO_ROOT / "catalog" / "seed_catalog.json")


def _rules(*endpoints: OpenAICompatEndpoint) -> Rules:
    return Rules.model_validate(
        {"models": {"openai_compatible": [e.model_dump() for e in endpoints]}}
    )


def test_openrouter_model_routes_through_litellm_with_provider_prefix() -> None:
    target = TargetBuilder(Rules())(decision("free_cloud", "acme/m:free"))
    assert (target.provider, target.route) == ("acme", "acme/m:free")
    assert target.api_base is None


def test_user_endpoint_gets_openai_route_base_url_and_env_key() -> None:
    endpoint = OpenAICompatEndpoint(
        name="myfree", base_url="https://api.example.com/v1/", api_key_env="MYFREE_KEY"
    )
    builder = TargetBuilder(_rules(endpoint), {"MYFREE_KEY": "secret-value"})
    target = builder(decision("free_cloud", "myfree/some-model"))
    assert target.route == "openai/some-model"
    assert target.api_base == "https://api.example.com/v1"
    assert builder.api_key_for("myfree") == "secret-value"
    assert builder.api_key_for("openrouter") is None


def test_missing_env_key_is_none_not_an_error() -> None:
    endpoint = OpenAICompatEndpoint(
        name="myfree", base_url="https://x.example/v1", api_key_env="NOPE"
    )
    assert TargetBuilder(_rules(endpoint), {}).api_key_for("myfree") is None


def test_opencode_free_models_are_refused_with_a_clear_error() -> None:
    with pytest.raises(ExecutorError) as raised:
        TargetBuilder(Rules())(decision("free_cloud", "opencode/some-free"))
    assert raised.value.code == "provider_unsupported"
    assert "inside OpenCode" in str(raised.value)


def test_local_target_without_a_fit_has_no_num_ctx() -> None:
    target = TargetBuilder(Rules())(decision("local", "qwen:14b"))
    assert (target.provider, target.route, target.num_ctx) == ("ollama", "qwen:14b", None)


class _Scripted:
    def __init__(self) -> None:
        self.requests: list[BackendRequest] = []

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        yield Delta(content="hello there")
        yield Delta(finish_reason="stop")


def _seeded(tmp_path: Path) -> object:
    from tests.cli_support import add_model

    db = open_database(tmp_path / "state.db")
    add_model(db, "qwen:14b", "local", 90.0, agentic=90.0, warm=True, size_vram_gb=8.0)
    return db


def test_engine_serves_a_local_request_with_the_computed_num_ctx(tmp_path: Path) -> None:
    from tests.cli_support import gpu_state

    db = _seeded(tmp_path)
    backend = _Scripted()
    engine = build_engine(
        Rules(),
        _catalog(),
        "seed",
        db,  # type: ignore[arg-type]
        hardware=lambda: gpu_state(16),
        local_factory=lambda host: backend,
        enable_classifier=False,
    )
    request = RouteRequest(
        messages=[{"role": "user", "content": "write a function that adds two numbers"}],
        scope="local",
        surface="mcp",
    )
    result = asyncio.run(engine.router.run(request))  # type: ignore[arg-type]
    assert result.result.content == "hello there"
    (sent,) = backend.requests
    assert sent.target.route == "qwen:14b"
    assert sent.target.num_ctx is not None
    assert sent.target.num_ctx >= 8192
    assert sent.target.cold is False


def test_engine_executor_carries_the_catalog_remote_patterns(tmp_path: Path) -> None:
    engine = build_engine(Rules(), _catalog(), "seed", open_database(tmp_path / "s.db"))
    assert engine.remote_patterns == PATTERNS
    assert is_remote_name("gpt-oss:120b-cloud", engine.remote_patterns)
    executor = engine.router._executor
    assert executor._remote_patterns == engine.remote_patterns
    backend = executor._local_factory("http://127.0.0.1:11434")
    assert backend._remote_patterns == engine.remote_patterns  # type: ignore[attr-defined]


def test_engine_refuses_a_cloud_proxied_model_even_if_a_stale_row_says_local(
    tmp_path: Path,
) -> None:
    from tests.cli_support import add_model, gpu_state

    db = open_database(tmp_path / "state.db")
    add_model(db, "gpt-oss:120b-cloud", "local", 99.0, agentic=99.0, warm=True, size_vram_gb=8.0)
    backend = _Scripted()
    engine = build_engine(
        Rules(),
        _catalog(),
        "seed",
        db,
        hardware=lambda: gpu_state(16),
        local_factory=lambda host: backend,
        enable_classifier=False,
    )
    request = RouteRequest(
        messages=[{"role": "user", "content": "write a function that adds two numbers"}],
        scope="local",
    )
    with pytest.raises(RouteFailed) as raised:
        asyncio.run(engine.router.run(request))  # type: ignore[arg-type]
    assert raised.value.code == "local_scope_violation"
    assert backend.requests == []  # refused before any backend was called


def test_decision_helper_is_a_route_decision() -> None:
    assert isinstance(decision("local", "x"), RouteDecision)
