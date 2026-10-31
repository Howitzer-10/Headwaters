"""HDW-121: Ollama cloud-proxied models are never local (A-OLL-9, D13)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.sources import ollama_models
from headwaters.core.ollama_remote import (
    compile_remote_patterns,
    has_remote_fields,
    is_remote_name,
    remote_patterns_from,
)
from tests.test_discovery_sources import OLLAMA, SHOW_QWEN, FakeHttp

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
PATTERNS = remote_patterns_from(CATALOG)
TAGS = json.loads((REPO_ROOT / "docs" / "ollama_tags_sample_cloud.json").read_text("utf-8"))


class Http(FakeHttp):
    def request_json(self, method: str, url: str, **kw: Any) -> Any:
        if url.endswith("/api/show"):
            return SHOW_QWEN
        return super().request_json(method, url, **kw)


def routes(models: list[dict[str, Any]]) -> dict[tuple[str, str], Any]:
    return {("GET", f"{OLLAMA}/api/tags"): {"models": models}, ("GET", f"{OLLAMA}/api/ps"): {}}


def test_live_capture_cloud_model_never_yields_a_local_candidate() -> None:
    names = [m["name"] for m in TAGS["models"]]
    assert "gpt-oss:120b-cloud" in names
    for patterns in ((), PATTERNS):  # the remote fields alone are enough
        found, warnings = ollama_models(Http(routes(TAGS["models"])), OLLAMA, patterns)
        assert "gpt-oss:120b-cloud" not in {m.id for m in found}
        assert all(m.tier == "local" for m in found)
        assert any("gpt-oss:120b-cloud" in w for w in warnings)
        assert len(found) == len(names) - 1


@pytest.mark.parametrize(
    "item",
    [
        {"name": "x:cloud"},  # name pattern only
        {"name": "y-cloud"},
        {"name": "z:Cloud"},
        {"name": "plain:8b", "remote_host": "https://ollama.com"},  # field only
        {"name": "plain:8b", "remote_model": "plain:8b"},
    ],
)
def test_each_signal_drops_the_model(item: dict[str, Any]) -> None:
    found, _ = ollama_models(Http(routes([item])), OLLAMA, PATTERNS)
    assert found == []


def test_remote_fields_in_api_show_drop_the_model() -> None:
    class ShowRemote(FakeHttp):
        def request_json(self, method: str, url: str, **kw: Any) -> Any:
            if url.endswith("/api/show"):
                return {**SHOW_QWEN, "remote_host": "https://ollama.com"}
            return super().request_json(method, url, **kw)

    found, warnings = ollama_models(ShowRemote(routes([{"name": "odd:8b"}])), OLLAMA, ())
    assert found == []
    assert any("odd:8b" in w for w in warnings)


def test_names_with_cloud_elsewhere_are_kept() -> None:
    items = [{"name": "cloudy:8b"}, {"name": "x:cloud-y"}]
    found, _ = ollama_models(Http(routes(items)), OLLAMA, PATTERNS)
    assert {m.id for m in found} == {"cloudy:8b", "x:cloud-y"}


def test_empty_remote_fields_are_not_remote() -> None:
    assert not has_remote_fields({"remote_model": "", "remote_host": None})
    assert has_remote_fields({"remote_host": "https://ollama.com"})


def test_patterns_are_catalog_data() -> None:
    assert [p.name for p in CATALOG.ollama_remote_patterns] == ["ollama-cloud-tag"]
    assert is_remote_name("gpt-oss:120b-cloud", PATTERNS)
    assert not is_remote_name("qwen3:8b", PATTERNS)
    assert not is_remote_name("gpt-oss:120b-cloud", compile_remote_patterns([]))
