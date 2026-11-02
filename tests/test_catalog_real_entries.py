"""HDW-149: real seed entries, sourced wall patterns, vision pull hint, unknown-context merge."""

from __future__ import annotations

from pathlib import Path

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.merge import merge_models
from headwaters.core.catalog.sources import LiveModel
from headwaters.core.policy.filters import Rejection
from headwaters.core.policy.ladder import no_route
from headwaters.core.rules import Rules

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


def test_seed_has_no_placeholder_ids() -> None:
    ids = {m.id for m in CATALOG.models}
    ids |= {i for p in CATALOG.gpu_profiles.values() for r in p.recommended.values() for i in r}
    ids |= {f.provider for f in CATALOG.free_limits} | {r.plan for r in CATALOG.subscription_rates}
    assert not [i for i in ids if i.startswith("example")]


def test_every_gpu_profile_names_a_vision_model_that_takes_images() -> None:
    by_id = {m.id: m for m in CATALOG.models}
    for profile in CATALOG.gpu_profiles.values():
        vision = profile.recommended["vision"]
        assert vision
        assert all("image" in by_id[i].modalities for i in vision)


def test_every_wall_pattern_has_a_source_and_a_date() -> None:
    missing = [p for p in CATALOG.wall_patterns if not p.source or not p.checked]
    assert missing == []


def test_catalog_reasoning_flags_exist_for_catalog_only_tiers() -> None:
    cloud = [m for m in CATALOG.models if "paid" in m.tier_hints or "free" in m.tier_hints]
    assert cloud
    assert all(m.reasoning_ok is True for m in cloud)


def test_unknown_context_is_not_filled_by_merge() -> None:
    item = LiveModel(id="vendor/x", tier="free_cloud", provider="p", source="openai_compat")
    records = {r.row.id: r for r in merge_models([item], CATALOG, Rules(), 1.0)}
    assert records["vendor/x"].row.ctx is None


class _Req:
    class scope:
        tiers = ("local", "free_cloud")
        local_only = False
        active_locks = ()


def test_no_vision_in_local_scope_suggests_the_catalog_vision_model() -> None:
    err = no_route(
        "no_model",
        "m",
        (Rejection("a", "no_vision"),),
        [],
        _Req(),  # type: ignore[arg-type]
        "pull qwen2.5vl:7b",
    )
    tips = {s.code: s.text for s in err.suggestions}
    assert "qwen2.5vl:7b" in tips["pull_model"]
    assert "vision" in tips["pull_model"]
