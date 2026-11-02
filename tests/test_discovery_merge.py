from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.merge import CatalogLookup, ModelRecord, merge_models
from headwaters.core.catalog.schema import Catalog
from headwaters.core.catalog.sources import LiveModel
from headwaters.core.rules import Rules

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = 1_000.0


@pytest.fixture
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


def live(model_id: str, **kw: Any) -> LiveModel:
    base: dict[str, Any] = {"id": model_id, "tier": "local", "provider": "ollama"}
    base["source"] = "ollama"
    return LiveModel(**{**base, **kw})


def one(item: LiveModel, catalog: Catalog, rules: Rules) -> ModelRecord:
    """Merge a single live item and return its record (curated cloud entries are ignored)."""
    records = merge_models([item], catalog, rules, NOW)
    return next(r for r in records if r.row.id == item.id)


def rules_with(overrides: dict[str, Any]) -> Rules:
    return Rules.model_validate({"models": {"overrides": overrides}})


def test_alias_and_latest_matching(catalog: Catalog) -> None:
    lookup = CatalogLookup(catalog)
    local = lookup.find(["qwen2.5-coder:14b-instruct"])
    assert local is not None
    assert local.id == "qwen2.5-coder:14b"
    upper = lookup.find(["QWEN2.5-CODER:14B"])
    assert upper is not None
    assert upper.id == "qwen2.5-coder:14b"
    latest = lookup.find(["claude-sonnet-4-5:latest"])
    assert latest is not None
    assert latest.id == "claude-sonnet-4-5"
    assert lookup.find(["nothing"]) is None
    assert lookup.get("nothing") is None


def test_known_model_takes_live_facts_and_catalog_scores(catalog: Catalog) -> None:
    item = live("qwen2.5-coder:14b-instruct", ctx=16384, tools_ok=False, size_gb=8.5, warm=True)
    record = one(item, catalog, Rules())
    row = record.row
    assert row.id == "qwen2.5-coder:14b-instruct"  # the id the live source reported
    assert record.catalog_id == "qwen2.5-coder:14b"
    assert (row.ctx, row.tools_ok, row.size_gb, row.warm) == (16384, False, 8.5, True)
    assert row.json_ok is True  # live said nothing, so the catalog fills the gap
    assert row.arch == {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128}
    assert row.unscored is False
    assert record.seed_scores["code"] == 70.0  # 0.7 on the catalog scale -> 0..100
    assert row.last_seen == NOW


def test_unknown_model_gets_conservative_defaults(catalog: Catalog) -> None:
    record = one(live("mystery:7b", size_gb=4.0), catalog, Rules())
    row = record.row
    assert row.unscored is True
    assert row.tools_ok is False
    assert row.json_ok is False
    assert row.ctx is None  # not presented as known (FU-136-1); CandidateBuilder flags the default
    assert max(record.seed_scores.values()) <= 40.0
    assert record.seed_scores["agentic_tools"] == 20.0


def test_deprecation_is_carried(catalog: Catalog) -> None:
    data = json.loads(catalog.model_dump_json())
    data["models"][0]["deprecated_by"] = data["models"][2]["id"]
    deprecated = Catalog.model_validate(data)
    item = live("claude-sonnet-4-5", tier="paid", provider="x", source="openrouter")
    record = one(item, deprecated, Rules())
    assert record.row.deprecated_by == "qwen2.5-coder:14b"


def test_retains_data_comes_from_catalog_or_source(catalog: Catalog) -> None:
    free = live("google/gemma-4-31b-it:free", tier="free_cloud", source="openrouter")
    unknown = live("opencode/x", tier="free_cloud", source="opencode", retains_data=True)
    records = {r.row.id: r.row for r in merge_models([free, unknown], catalog, Rules(), NOW)}
    assert records["google/gemma-4-31b-it:free"].retains_data is True  # catalog flag
    assert records["opencode/x"].retains_data is True  # source flag


def test_source_supplied_names_match_catalog(catalog: Catalog) -> None:
    item = live(
        "vendor/odd-id",
        tier="paid",
        source="openrouter",
        match_names=["claude-sonnet-4-5-20250929"],
    )
    record = one(item, catalog, Rules())
    assert record.catalog_id == "claude-sonnet-4-5"
    assert record.row.unscored is False


def test_catalog_only_cloud_entries_are_added_but_local_need_install(catalog: Catalog) -> None:
    records = {r.row.id: r for r in merge_models([], catalog, Rules(), NOW)}
    assert set(records) == {"claude-sonnet-4-5", "google/gemma-4-31b-it:free"}
    paid = records["claude-sonnet-4-5"].row
    assert (paid.tier, paid.source) == ("paid", "catalog")
    assert paid.reasoning_ok is True  # catalog-only models can serve think (FU-140-1)
    assert records["google/gemma-4-31b-it:free"].row.tier == "free_cloud"


def test_catalog_reasoning_fills_a_source_with_no_capability_data(catalog: Catalog) -> None:
    item = live("qwen3:14b")
    assert item.reasoning_ok is None
    assert one(item, catalog, Rules()).row.reasoning_ok is True
    live_says_no = live("qwen3:14b", reasoning_ok=False)
    assert one(live_says_no, catalog, Rules()).row.reasoning_ok is False


def test_user_overrides_win_last(catalog: Catalog) -> None:
    rules = rules_with(
        {
            "qwen2.5-coder:14b-instruct": {
                "scores": {"code": 95},
                "tools_ok": False,
                "ctx": 4096,
                "tier": "free_cloud",
            }
        }
    )
    item = live("qwen2.5-coder:14b", tools_ok=True, ctx=32768)
    record = one(item, catalog, rules)
    assert (record.row.tools_ok, record.row.ctx, record.row.tier) == (False, 4096, "free_cloud")
    scores = {s.task: s for s in record.score_rows()}
    assert scores["code"].override == 95.0
    assert scores["code"].seed == 70.0
    assert scores["chat"].override is None


def test_override_matches_unknown_model_by_id_case_insensitively(catalog: Catalog) -> None:
    rules = rules_with({"MYSTERY:7B": {"scores": {"chat": 80}}})
    record = one(live("mystery:7b"), catalog, rules)
    assert record.row.unscored is True  # an override does not make it curated
    assert record.override_scores == {"chat": 80.0}


@pytest.mark.parametrize(
    "overrides",
    [{"x": {"scores": {"nope": 5}}}, {"x": {"scores": {"code": 101}}}, {"x": {"ctx": 0}}],
)
def test_bad_override_rules_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        rules_with(overrides)
