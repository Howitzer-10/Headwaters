"""HDW-515: OpenCode ships as two providers, two endpoints and one shared account variable."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog import http as catalog_http
from headwaters.core.catalog import sources
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import ProviderEntry
from headwaters.core.engine import TargetBuilder
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import Delta, ExecResult, ExecTarget
from headwaters.core.keys import KeyStore
from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules
from headwaters.core.user_agent import USER_AGENT
from tests.executor_support import MESSAGES, WALLS, ScriptedBackend, decision
from tests.keys_support import FakeKeyring
from tests.router_rig import Rig
from tests.router_support import candidate, registry_with
from tests.test_discovery_sources import FakeHttp
from tests.test_hdw514_registry import catalog_with, entry

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / SEED_RELATIVE_PATH
REGISTRY = Registry.from_catalog(load_catalog(SEED_PATH))
BOTH = ("opencode", "opencode-go")
CONSOLE_BASE = "https://opencode.ai/inference/openai/v1"
GO_BASE = "https://opencode.ai/inference/go/openai/v1"
CONSOLE_LISTING = "https://opencode.ai/inference/v1/models"
GO_LISTING = "https://opencode.ai/inference/go/v1/models"
PAID_MODEL = "deepseek-v4.1-flash"


def provider(name: str) -> ProviderEntry:
    found = REGISTRY.get(name)
    assert found is not None, name
    return found


def store(registry: Registry = REGISTRY, **env: str) -> KeyStore:
    return KeyStore(Rules(), env=env, backend=FakeKeyring(), registry=registry)


def listing(**over: Any) -> dict[str, Any]:
    """One model in the shape OpenCode's listing actually uses."""
    item: dict[str, Any] = {
        "id": PAID_MODEL,
        "canonical_id": "vendor/deepseek-v4.1-flash",
        "family": "deepseek",
        "capabilities": {
            "tools": {"supported": True},
            "structured_output": False,
            "reasoning": {"supported": False},
        },
        "modalities": {"input": ["text", "image"], "output": ["text"]},
        "limit": {"context": 64000, "output": 8192},
        "cost": {"text": {"input": {"price": 0.3}, "output": {"price": 1.2}}},
    }
    item.update(over)
    return item


# ------------------------------------------------------------------ registry rows


def test_both_products_are_registered_with_their_own_endpoints() -> None:
    rows = {name: provider(name) for name in BOTH}
    assert (rows["opencode"].base_url, rows["opencode"].models_url) == (
        CONSOLE_BASE,
        CONSOLE_LISTING,
    )
    assert (rows["opencode-go"].base_url, rows["opencode-go"].models_url) == (GO_BASE, GO_LISTING)
    for name, row in rows.items():
        assert row.base_url != row.models_url, name
        # The listing is not reachable under the chat root (both 404), so it is never derived.
        assert row.models_url != row.base_url.rstrip("/") + "/models", name
        assert (row.auth_style, row.key_required) == ("bearer", True)


def test_default_registry_offers_both_without_a_rules_edit() -> None:
    assert {p.name for p in default_registry()} >= set(BOTH)


@pytest.mark.parametrize("name", BOTH)
def test_the_tier_is_the_verified_one_and_never_free_cloud(name: str) -> None:
    row = provider(name)
    assert (row.tier, row.honor_free_markers, row.key_required) == ("paid", False, True)


def test_no_opencode_row_reaches_the_surfaces_as_free() -> None:
    tiers = {p.name: p.tier for p in store().providers() if p.name in BOTH}
    assert tiers == {"opencode": "paid", "opencode-go": "paid"}


def test_both_names_resolve_and_an_unknown_one_is_refused() -> None:
    keys = store()
    assert keys.provider_name("opencode") == "opencode"
    assert keys.provider_name("opencode-go") == "opencode-go"
    assert keys.provider_name("OPENCODE") == "opencode"
    # One variable, two products: the first row that declares it is the answer, never the last.
    assert keys.provider_name("OPENCODE_API_KEY") == "opencode"
    for bad in ("opencodezen", "opencode-go-go", "OPENCODE_API_KEYS", "opencode go"):
        assert keys.provider_name(bad) is None, bad


def test_one_variable_reaches_both_products_but_a_saved_key_does_not_cross() -> None:
    keys = store(OPENCODE_API_KEY="from-env-456")
    assert keys.api_key_for("opencode") == "from-env-456"
    assert keys.api_key_for("opencode-go") == "from-env-456"
    saved = store()
    assert saved.save("opencode", "console-key-123").env_var == "OPENCODE_API_KEY"
    assert saved.api_key_for("opencode") == "console-key-123"
    assert saved.api_key_for("opencode-go") is None


def test_two_providers_may_share_one_variable_as_data() -> None:
    catalog = catalog_with(
        entry(name="acme", env_var="ACME_KEY"),
        entry(name="acme-go", env_var="ACME_KEY"),
    )
    registry = Registry.from_catalog(catalog)
    assert [p.name for p in registry] == ["acme", "acme-go"]
    keys = KeyStore(
        Rules(), env={"ACME_KEY": "k-abcdef123456"}, backend=FakeKeyring(), registry=registry
    )
    assert keys.provider_name("ACME_KEY") == "acme"
    assert keys.api_key_for("acme-go") == "k-abcdef123456"


# ------------------------------------------------------------------ listing dialect


def test_the_listing_dialect_is_read_and_client_only_free_ids_are_dropped() -> None:
    free = listing(
        id="beam-free",
        capabilities={
            "tools": {"supported": True},
            "structured_output": True,
            "reasoning": {"supported": True},
        },
        cost={"text": {"input": {"price": 0}, "output": {"price": 0}}},
    )
    http = FakeHttp({("GET", CONSOLE_LISTING): {"object": "list", "data": [free, listing()]}})
    models = sources.registry_models(http, provider("opencode"), "key-123")
    assert [m.id for m in models] == [f"opencode/{PAID_MODEL}"]
    paid = models[0]
    assert (paid.tier, paid.ctx, paid.tools_ok) == ("paid", 64000, True)
    assert (paid.json_ok, paid.reasoning_ok) == (False, False)
    assert paid.modalities == ["text", "image"]
    assert http.calls[0][1] == CONSOLE_LISTING
    assert http.calls[0][3] == {"Authorization": "Bearer key-123"}


def test_the_go_listing_keeps_the_models_the_plan_includes() -> None:
    http = FakeHttp({("GET", GO_LISTING): {"data": [listing(id="iceman-contributor-free")]}})
    models = sources.registry_models(http, provider("opencode-go"), "key-123")
    assert [m.id for m in models] == ["opencode-go/iceman-contributor-free"]
    # D23: the Go plan's $0 ids consume the subscription allowance, so they are plan access.
    assert models[0].tier == "free_cloud"


def test_an_unreachable_filter_never_calls_a_model_the_listing_excludes() -> None:
    assert provider("opencode").refuses("nemotron-3.5-lightning-free")
    assert not provider("opencode").refuses(PAID_MODEL)
    assert not provider("opencode-go").refuses("iceman-contributor-free")


# ------------------------------------------------------------------ routing


def test_a_paid_model_routes_to_its_own_endpoint() -> None:
    builder = TargetBuilder(Rules(), {}, registry=REGISTRY)
    console = builder(decision("paid", f"opencode/{PAID_MODEL}"))
    go = builder(decision("paid", f"opencode-go/{PAID_MODEL}"))
    assert (console.route, console.api_base) == (f"openai/{PAID_MODEL}", CONSOLE_BASE)
    assert (go.route, go.api_base) == (f"openai/{PAID_MODEL}", GO_BASE)


@pytest.mark.parametrize("model", ["opencode/beam-free", "opencode/nemotron-3.5-lightning-free"])
def test_a_client_only_free_model_is_refused_instead_of_called(model: str) -> None:
    builder = TargetBuilder(Rules(), {}, registry=REGISTRY)
    with pytest.raises(ExecutorError) as raised:
        builder(decision("paid", model))
    assert raised.value.code == "provider_unsupported"
    assert "outside its own app" in raised.value.message


def test_a_go_model_the_plan_includes_stays_callable() -> None:
    builder = TargetBuilder(Rules(), {}, registry=REGISTRY)
    target = builder(decision("paid", "opencode-go/iceman-contributor-free"))
    assert target.api_base == GO_BASE


def test_one_variable_keys_both_products_for_a_call() -> None:
    builder = TargetBuilder(Rules(), {"OPENCODE_API_KEY": "k-abcdef123456"}, registry=REGISTRY)
    assert builder.api_key_for("opencode") == "k-abcdef123456"
    assert builder.api_key_for("opencode-go") == "k-abcdef123456"


# ------------------------------------------- the session header and the user agent (2026-11-03)


def go_target(model: str = f"opencode-go/{PAID_MODEL}") -> Any:
    return ExecTarget(
        model,
        "opencode-go",
        f"openai/{PAID_MODEL}",
        api_base=GO_BASE,
        session_header="x-opencode-session",
    )


def go_call(conversation_id: str | None) -> tuple[ScriptedBackend, ExecResult]:
    """One cloud call to the Go target, with the given conversation key."""
    backend = ScriptedBackend([Delta(content="ok"), Delta(finish_reason="stop")])
    target = go_target()
    executor = Executor(
        Rules(),
        WALLS,
        lambda d: target,
        cloud_factory=lambda: backend,
        local_factory=lambda host: backend,
        api_key_for=lambda provider: "placeholder-key-123456",
    )
    result = asyncio.run(
        executor.run(
            decision("paid", target.model_id),
            MESSAGES,
            None,
            False,
            None,
            conversation_id=conversation_id,
        )
    )
    return backend, result


def test_only_the_go_row_declares_a_session_header() -> None:
    # Go answers 400 MissingSessionID without it; Console does not ask for one.
    assert provider("opencode-go").session_header == "x-opencode-session"
    assert provider("opencode").session_header is None


def test_the_go_target_carries_the_header_the_catalog_row_names() -> None:
    builder = TargetBuilder(Rules(), {}, registry=REGISTRY)
    assert builder(decision("paid", f"opencode-go/{PAID_MODEL}")).session_header == (
        "x-opencode-session"
    )
    assert builder(decision("paid", f"opencode/{PAID_MODEL}")).session_header is None


def test_a_go_call_sends_the_conversation_key_in_the_session_header() -> None:
    backend, result = go_call("id:ide-42")
    assert result.content == "ok"
    sent = backend.requests[0].headers
    assert sent["x-opencode-session"] == "id:ide-42"
    # The same conversation reuses the same id, so Go can route it efficiently.
    again, _ = go_call("id:ide-42")
    assert again.requests[0].headers["x-opencode-session"] == "id:ide-42"


def test_a_go_call_without_a_conversation_id_is_refused() -> None:
    with pytest.raises(ExecutorError) as raised:
        go_call(None)
    assert raised.value.code == "session_required"
    assert "session id" in raised.value.message


def test_a_console_call_sends_no_session_header() -> None:
    backend = ScriptedBackend([Delta(content="ok"), Delta(finish_reason="stop")])
    target = ExecTarget(
        f"opencode/{PAID_MODEL}", "opencode", f"openai/{PAID_MODEL}", api_base=CONSOLE_BASE
    )
    executor = Executor(
        Rules(),
        WALLS,
        lambda d: target,
        cloud_factory=lambda: backend,
        local_factory=lambda host: backend,
        api_key_for=lambda provider: "placeholder-key-123456",
    )
    asyncio.run(executor.run(decision("paid", target.model_id), MESSAGES, None, False, None))
    assert "x-opencode-session" not in backend.requests[0].headers


GENERIC_AGENTS = (
    "python-urllib",
    "urllib",
    "httpx",
    "openai",
    "asyncopenai",
    "litellm",
    "aiohttp",
)


def test_every_provider_call_names_headwaters_as_its_user_agent() -> None:
    # Cloudflare answers a generic SDK/HTTP-library user agent with 403 "error code: 1010".
    backend, _ = go_call("id:ide-42")
    sent = backend.requests[0].headers["User-Agent"]
    assert sent == USER_AGENT
    assert sent.startswith("headwaters/")
    assert not sent.lower().startswith(GENERIC_AGENTS)


def test_discovery_uses_the_same_product_user_agent() -> None:
    assert catalog_http.USER_AGENT == USER_AGENT
    assert catalog_http.USER_AGENT.startswith("headwaters/")
    assert not catalog_http.USER_AGENT.lower().startswith(GENERIC_AGENTS)


def test_the_router_keys_the_go_session_header_to_the_conversation(tmp_path: Path) -> None:
    model = f"opencode-go/{PAID_MODEL}"
    target = go_target(model)
    rig = Rig(
        tmp_path,
        {model: [Delta(content="ok"), Delta(finish_reason="stop")]},
        [candidate(model, "paid", 96, price_in=3.0, price_out=15.0, is_primary=True)],
        rules=Rules.model_validate({"strategy": "primary_first"}),
        registry=registry_with(paid=True),
        resolve=lambda d: target,
    )
    rig.run(headers={"x-session-id": "ide-42"})
    sent = rig.dispatch.backends[model].requests[0]
    assert sent.headers["x-opencode-session"] == "id:ide-42"
    assert sent.headers["User-Agent"] == USER_AGENT
