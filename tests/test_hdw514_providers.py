"""HDW-514: provider listings become models, tiers stay honest, and calls route to the base URL."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog import discovery, sources
from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.merge import merge_models
from headwaters.core.catalog.schema import Catalog, ProviderEntry
from headwaters.core.engine import ANONYMOUS_KEY, TargetBuilder
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.keys import KeyStore
from headwaters.core.providers import Registry
from headwaters.core.rules import Rules
from headwaters.core.state import open_database
from headwaters.core.state_models import get_model, list_models
from tests.executor_support import decision
from tests.keys_support import FakeKeyring
from tests.test_discovery_sources import FakeHttp
from tests.test_hdw514_registry import catalog_with, entry

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
REGISTRY = Registry.from_catalog(SEED)
URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1/models",
    "huggingface": "https://router.huggingface.co/v1/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "pollinations": "https://gen.pollinations.ai/v1/models",
    "kilo": "https://ai-gateway.kilo.ai/api/v1/models",
}
TEXT = {"input_modalities": ["text"], "output_modalities": ["text"]}
LISTINGS: dict[str, dict[str, Any]] = {
    "nvidia": {
        "data": [
            {"id": "meta/llama-3.1-8b-instruct"},
            {"id": "nvidia/nv-embedqa-e5-v5"},
            {"id": "nvidia/llama-3.1-nemoguard-8b-content-safety"},
        ]
    },
    "huggingface": {
        "data": [
            {
                "id": "org/free-model",
                "architecture": TEXT,
                "providers": [
                    {
                        "provider": "p",
                        "context_length": 8192,
                        "is_free": True,
                        "supports_tools": True,
                    }
                ],
            },
            {
                "id": "org/paid-model",
                "architecture": TEXT,
                "providers": [{"provider": "p", "context_length": 4096, "is_free": False}],
            },
        ]
    },
    "mistral": {
        "data": [
            {
                "id": "mistral-small-latest",
                "capabilities": {
                    "completion_chat": True,
                    "function_calling": True,
                    "vision": False,
                },
            },
            {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
        ]
    },
    "pollinations": {
        "data": [
            {
                "id": "openai/gpt-5.4-nano",
                "input_modalities": ["text"],
                "output_modalities": ["text"],
                "tools": True,
                "context_length": 128000,
            },
            {"id": "flux", "output_modalities": ["image"]},
        ]
    },
    "kilo": {
        "data": [
            {"id": "vendor/a:free", "architecture": TEXT, "context_length": 32000},
            {"id": "vendor/b", "isFree": True, "architecture": TEXT},
            {"id": "vendor/c", "architecture": TEXT, "supported_parameters": ["tools"]},
        ]
    },
}


def provider(name: str) -> ProviderEntry:
    found = REGISTRY.get(name)
    assert found is not None
    return found


def models(name: str, key: str | None = "k-abcdef123456") -> dict[str, sources.LiveModel]:
    http = FakeHttp({("GET", URLS[name]): LISTINGS[name]})
    found = {m.id: m for m in sources.registry_models(http, provider(name), key)}
    assert http.calls[0][1] == URLS[name]
    return found


def test_nvidia_models_are_all_free_and_non_chat_ids_are_skipped() -> None:
    found = models("nvidia")
    assert list(found) == ["nvidia/meta/llama-3.1-8b-instruct"]
    model = found["nvidia/meta/llama-3.1-8b-instruct"]
    assert (model.tier, model.price_in, model.price_out) == ("free_cloud", 0.0, 0.0)
    assert (model.provider, model.source, model.retains_data) == ("nvidia", "provider:nvidia", True)


def test_huggingface_is_paid_unless_the_listing_marks_a_provider_free() -> None:
    found = models("huggingface")
    free = found["huggingface/org/free-model"]
    assert (free.tier, free.ctx, free.tools_ok) == ("free_cloud", 8192, True)
    assert found["huggingface/org/paid-model"].tier == "paid"
    assert found["huggingface/org/paid-model"].price_in is None


def test_mistral_is_paid_and_embedding_models_are_skipped() -> None:
    found = models("mistral")
    assert list(found) == ["mistral/mistral-small-latest"]
    model = found["mistral/mistral-small-latest"]
    assert (model.tier, model.tools_ok) == ("paid", True)


def test_pollinations_is_paid_and_image_generators_are_skipped() -> None:
    found = models("pollinations")
    assert list(found) == ["pollinations/openai/gpt-5.4-nano"]
    model = found["pollinations/openai/gpt-5.4-nano"]
    assert (model.tier, model.ctx, model.tools_ok) == ("paid", 128000, True)


def test_kilo_honours_free_markers_and_keyless_offers_only_free_models() -> None:
    keyed = models("kilo")
    assert {i: m.tier for i, m in keyed.items()} == {
        "kilo/vendor/a:free": "free_cloud",
        "kilo/vendor/b": "free_cloud",
        "kilo/vendor/c": "paid",
    }
    assert keyed["kilo/vendor/c"].tools_ok is True
    keyless = models("kilo", key=None)
    assert set(keyless) == {"kilo/vendor/a:free", "kilo/vendor/b"}


def test_the_key_goes_out_as_a_bearer_header_and_only_when_there_is_one() -> None:
    http = FakeHttp({("GET", URLS["mistral"]): LISTINGS["mistral"]})
    sources.registry_models(http, provider("mistral"), "k-abcdef123456")
    assert http.calls[0][3] == {"Authorization": "Bearer k-abcdef123456"}
    http = FakeHttp({("GET", URLS["kilo"]): LISTINGS["kilo"]})
    sources.registry_models(http, provider("kilo"), None)
    assert http.calls[0][3] is None


def test_a_listing_that_is_not_json_data_yields_no_models() -> None:
    http = FakeHttp({("GET", URLS["nvidia"]): ["not", "a", "dict"]})
    assert sources.registry_models(http, provider("nvidia"), "k-abcdef123456") == []


def test_free_cloud_tier_survives_the_merge_and_paid_stays_paid() -> None:
    flat = [*models("nvidia").values(), *models("mistral").values()]
    rows = {r.row.id: r.row for r in merge_models(flat, SEED, Rules(), 100.0)}
    assert rows["nvidia/meta/llama-3.1-8b-instruct"].tier == "free_cloud"
    assert rows["mistral/mistral-small-latest"].tier == "paid"


# ---------------------------------------------------------------- discovery


def refresh(
    catalog: Catalog,
    routes: dict[tuple[str, str], Any],
    tmp_path: Path,
    rules: Rules | None = None,
    **env: str,
) -> tuple[discovery.RefreshResult, Any]:
    db = open_database(tmp_path / "s" / "t.db")
    result = discovery.refresh_models(
        db,
        rules or Rules(),
        catalog,
        http=FakeHttp(routes),
        env=env,
        home=Path("/nohome"),
        now=100.0,
    )
    return result, db


def test_discovery_lists_a_keyed_provider_and_persists_its_models(tmp_path: Path) -> None:
    result, db = refresh(
        SEED, {("GET", URLS["nvidia"]): LISTINGS["nvidia"]}, tmp_path, NVIDIA_API_KEY="k-nv-123456"
    )
    assert "provider:nvidia" in result.sources_ok
    row = get_model(db, "nvidia/meta/llama-3.1-8b-instruct")
    assert row is not None
    assert (row.tier, row.provider) == ("free_cloud", "nvidia")


def test_a_provider_that_needs_a_key_is_not_called_without_one(tmp_path: Path) -> None:
    result, db = refresh(SEED, {("GET", URLS["nvidia"]): LISTINGS["nvidia"]}, tmp_path)
    assert "provider:nvidia" not in result.sources_ok
    assert not any(w.startswith("nvidia:") for w in result.warnings)
    assert get_model(db, "nvidia/meta/llama-3.1-8b-instruct") is None


def test_keyless_kilo_is_fetched_and_offers_only_free_models(tmp_path: Path) -> None:
    result, db = refresh(SEED, {("GET", URLS["kilo"]): LISTINGS["kilo"]}, tmp_path)
    assert "provider:kilo" in result.sources_ok
    ids = {m.id for m in list_models(db) if m.provider == "kilo"}
    assert ids == {"kilo/vendor/a:free", "kilo/vendor/b"}


def test_a_provider_failure_is_a_warning_and_deletes_nothing(tmp_path: Path) -> None:
    routes = {("GET", URLS["nvidia"]): LISTINGS["nvidia"]}
    db = open_database(tmp_path / "s" / "t.db")
    kwargs: dict[str, Any] = {"home": Path("/nohome"), "env": {"NVIDIA_API_KEY": "k-nv-123456"}}
    discovery.refresh_models(db, Rules(), SEED, http=FakeHttp(routes), now=100.0, **kwargs)
    down = {("GET", URLS["nvidia"]): SourceError("cannot reach integrate.api.nvidia.com")}
    result = discovery.refresh_models(db, Rules(), SEED, http=FakeHttp(down), now=200.0, **kwargs)
    assert "provider:nvidia" not in result.sources_ok
    assert any(w.startswith("nvidia: cannot reach") for w in result.warnings)
    assert get_model(db, "nvidia/meta/llama-3.1-8b-instruct") is not None


def test_a_provider_that_stops_listing_a_model_loses_it(tmp_path: Path) -> None:
    db = open_database(tmp_path / "s" / "t.db")
    kwargs: dict[str, Any] = {"home": Path("/nohome"), "env": {"NVIDIA_API_KEY": "k-nv-123456"}}
    first = FakeHttp({("GET", URLS["nvidia"]): LISTINGS["nvidia"]})
    discovery.refresh_models(db, Rules(), SEED, http=first, now=100.0, **kwargs)
    emptied = FakeHttp({("GET", URLS["nvidia"]): {"data": []}})
    result = discovery.refresh_models(db, Rules(), SEED, http=emptied, now=200.0, **kwargs)
    assert result.removed >= 1
    assert get_model(db, "nvidia/meta/llama-3.1-8b-instruct") is None


def test_airplane_mode_sends_no_provider_request(tmp_path: Path) -> None:
    http = FakeHttp({("GET", URLS["kilo"]): LISTINGS["kilo"]})
    db = open_database(tmp_path / "s" / "t.db")
    result = discovery.refresh_models(
        db,
        Rules(airplane=True),
        SEED,
        http=http,
        env={"NVIDIA_API_KEY": "k-nv-123456"},
        home=Path("/nohome"),
        now=100.0,
    )
    assert not any(call[1].startswith("https://") for call in http.calls)
    assert "provider:kilo" not in result.sources_ok


def test_a_stored_key_reaches_discovery_through_the_key_store(tmp_path: Path) -> None:
    keys = KeyStore(Rules(), env={}, backend=FakeKeyring(), registry=REGISTRY)
    keys.save("nvidia", "k-stored-123456")
    http = FakeHttp({("GET", URLS["nvidia"]): LISTINGS["nvidia"]})
    db = open_database(tmp_path / "s" / "t.db")
    result = discovery.refresh_models(
        db,
        Rules(),
        SEED,
        http=http,
        env={},
        home=Path("/nohome"),
        now=100.0,
        key_for=keys.api_key_for,
    )
    assert "provider:nvidia" in result.sources_ok
    nvidia_call = next(c for c in http.calls if c[1] == URLS["nvidia"])
    assert nvidia_call[3] == {"Authorization": "Bearer k-stored-123456"}


def test_a_provider_added_as_data_is_discovered_and_routed(tmp_path: Path) -> None:
    catalog = catalog_with(entry(tier="free_cloud"))
    listing = {("GET", "https://api.acme.test/v1/models"): {"data": [{"id": "m1"}]}}
    result, db = refresh(catalog, listing, tmp_path, ACME_API_KEY="acme-key-123")
    assert "provider:acme" in result.sources_ok
    assert get_model(db, "acme/m1") is not None
    builder = TargetBuilder(
        Rules(),
        {"ACME_API_KEY": "acme-key-123"},
        registry=Registry.from_catalog(catalog),
    )
    target = builder(decision("free_cloud", "acme/m1"))
    assert (target.provider, target.route, target.api_base) == (
        "acme",
        "openai/m1",
        "https://api.acme.test/v1",
    )
    assert builder.api_key_for("acme") == "acme-key-123"


# ---------------------------------------------------------------- routing


@pytest.mark.parametrize("name", sorted(URLS))
def test_every_provider_routes_to_its_own_base_url(name: str) -> None:
    builder = TargetBuilder(Rules(), {}, registry=REGISTRY)
    target = builder(decision("paid", f"{name}/some/model"))
    assert (target.provider, target.route) == (name, "openai/some/model")
    assert target.api_base == provider(name).base_url.rstrip("/")


def test_a_missing_key_never_falls_back_to_the_openai_key() -> None:
    builder = TargetBuilder(Rules(), {"OPENAI_API_KEY": "sk-should-not-leak"}, registry=REGISTRY)
    assert builder.api_key_for("kilo") == ANONYMOUS_KEY
    assert builder.api_key_for("openai") is None  # built-in providers are unchanged


def test_a_key_in_the_environment_is_used_without_a_key_store() -> None:
    builder = TargetBuilder(Rules(), {"MISTRAL_API_KEY": "m-key-123456"}, registry=REGISTRY)
    assert builder.api_key_for("mistral") == "m-key-123456"


def test_openrouter_and_opencode_behaviour_is_unchanged() -> None:
    builder = TargetBuilder(Rules(), {}, registry=REGISTRY)
    target = builder(decision("free_cloud", "acme/m:free"))
    assert (target.provider, target.route, target.api_base) == ("acme", "acme/m:free", None)
    with pytest.raises(ExecutorError) as raised:
        builder(decision("free_cloud", "opencode/some-free"))
    assert raised.value.code == "provider_unsupported"
