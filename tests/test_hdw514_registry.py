"""HDW-514: the provider registry is data, and key handling reads it (no rules.yaml edit)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from headwaters.control import keys as key_checks
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import Catalog, ProviderEntry
from headwaters.core.keys import KeyStore
from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules
from tests.keys_support import FakeKeyring
from tests.test_discovery_sources import FakeHttp

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / SEED_RELATIVE_PATH
EXPECTED = {
    "nvidia": ("NVIDIA_API_KEY", "free_cloud"),
    "huggingface": ("HF_TOKEN", "paid"),
    "mistral": ("MISTRAL_API_KEY", "paid"),
    "pollinations": ("POLLINATIONS_API_KEY", "paid"),
    "kilo": ("KILO_API_KEY", "paid"),
    # HDW-515: two products of one company, one variable, two endpoints, both paid.
    "opencode": ("OPENCODE_API_KEY", "paid"),
    "opencode-go": ("OPENCODE_API_KEY", "paid"),
}


def entry(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "acme",
        "title": "Acme",
        "env_var": "ACME_API_KEY",
        "base_url": "https://api.acme.test/v1",
        "tier": "paid",
        "source_url": "https://acme.test/docs",
        "checked": "2026-11-03",
    }
    base.update(over)
    return base


def catalog_with(*providers: dict[str, Any]) -> Catalog:
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    data["providers"] = list(providers)
    return Catalog.model_validate(data)


def store(registry: Registry, **env: str) -> KeyStore:
    return KeyStore(Rules(), env=env, backend=FakeKeyring(), registry=registry)


def test_seed_registers_every_ticket_provider_with_a_tier() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    assert {p.name: (p.env_var, p.tier) for p in registry} == EXPECTED
    assert all(p.base_url.startswith("https://") and p.verify for p in registry)


def test_no_paid_provider_is_declared_free_cloud_by_accident() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    mistral = registry.get("mistral")
    assert mistral is not None
    assert (mistral.tier, mistral.honor_free_markers) == ("paid", False)
    pollinations = registry.get("pollinations")
    assert pollinations is not None
    assert (pollinations.tier, pollinations.key_required) == ("paid", True)


def test_default_registry_reads_the_seed_offline() -> None:
    assert {p.name for p in default_registry()} >= set(EXPECTED)


@pytest.mark.parametrize(
    "providers",
    [
        (entry(), entry(env_var="OTHER_KEY")),
        (entry(name="openrouter"),),
        (entry(name="Bad Name"),),
        (entry(env_var="lower"),),
        (entry(base_url="http://insecure.test/v1"),),
        (entry(tier="free"),),
        (entry(skip_id_pattern="(unclosed"),),
        (entry(checked="yesterday"),),
    ],
)
def test_a_bad_registry_is_rejected_whole(providers: tuple[dict[str, Any], ...]) -> None:
    with pytest.raises(ValidationError):
        catalog_with(*providers)


def test_provider_entry_builds_its_models_url() -> None:
    parsed = ProviderEntry.model_validate(entry(base_url="https://api.acme.test/v1/"))
    assert parsed.models_url == "https://api.acme.test/v1/models"


def test_keys_list_every_registered_provider_without_a_rules_edit() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    rows = {row.alias: row for row in store(registry).entries()}
    for name, (env_var, _tier) in EXPECTED.items():
        assert rows[name].provider == name
        assert rows[name].source == "not set"
        assert rows[name].env_var == env_var


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_keys_set_works_for_every_provider_and_the_secret_resolves(name: str) -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    keys = store(registry)
    saved = keys.save(name, "secret-value-123")
    assert saved.provider == name
    assert saved.env_var == EXPECTED[name][0]
    assert keys.api_key_for(name) == "secret-value-123"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_environment_variable_is_a_fallback(name: str) -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    keys = store(registry, **{EXPECTED[name][0]: "from-env-456"})
    assert keys.api_key_for(name) == "from-env-456"


def test_a_provider_added_as_data_needs_no_code_anywhere() -> None:
    registry = Registry.from_catalog(catalog_with(entry(metadata_url="https://api.acme.test/me")))
    keys = store(registry)
    assert keys.save("acme", "k-1234567890").env_var == "ACME_API_KEY"
    assert key_checks.target_for("acme", Rules(), registry=registry) == (
        "https://api.acme.test/me",
        "bearer",
    )


def test_a_keystore_without_a_registry_keeps_the_built_in_providers() -> None:
    keys = KeyStore(Rules(), env={"OPENROUTER_API_KEY": "or-key-1"}, backend=FakeKeyring())
    assert keys.api_key_for("openrouter") == "or-key-1"


def check(registry: Registry, http: FakeHttp, provider: str) -> key_checks.PingResult:
    keys = store(registry, **{EXPECTED[provider][0]: "k-abcdef123456"})
    results = key_checks.check_keys(keys, Rules(), http=http, registry=registry)
    return next(r for r in results if r.provider == provider)


def test_keys_test_uses_the_registered_check_url_with_a_bearer_header() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    url = "https://huggingface.co/api/whoami-v2"
    http = FakeHttp({("GET", url): {"name": "someone"}})
    assert check(registry, http, "huggingface").status == key_checks.OK
    assert http.calls[0][1] == url
    assert http.calls[0][3] == {"Authorization": "Bearer k-abcdef123456"}


def test_keys_test_reports_a_rejected_key() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    url = "https://api.mistral.ai/v1/models"
    http = FakeHttp({("GET", url): key_checks.SourceError("HTTP 401 from api.mistral.ai")})
    assert check(registry, http, "mistral").status == key_checks.UNAUTHORIZED


def test_keys_test_reports_unreachable_and_http_errors() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    url = "https://api.mistral.ai/v1/models"
    down = FakeHttp({("GET", url): key_checks.SourceError("cannot reach api.mistral.ai")})
    assert check(registry, down, "mistral").status == key_checks.UNREACHABLE
    broken = FakeHttp({("GET", url): key_checks.SourceError("HTTP 500 from api.mistral.ai")})
    assert check(registry, broken, "mistral").status == key_checks.HTTP_ERROR


@pytest.mark.parametrize("name", ["nvidia", "pollinations", "kilo"])
def test_a_provider_with_no_safe_check_is_unsupported_and_never_called(name: str) -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    http = FakeHttp({})
    assert check(registry, http, name).status == key_checks.UNSUPPORTED
    assert http.calls == []


def test_keys_test_sends_nothing_in_airplane_mode() -> None:
    registry = Registry.from_catalog(load_catalog(SEED_PATH))
    keys = store(registry, MISTRAL_API_KEY="k-abcdef123456")
    http = FakeHttp({})
    results = key_checks.check_keys(keys, Rules(airplane=True), http=http, registry=registry)
    assert [r.status for r in results] == [key_checks.BLOCKED]
    assert http.calls == []
