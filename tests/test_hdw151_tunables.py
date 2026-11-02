"""HDW-151: the remaining tunables live in rules.yaml and the catalog, not in core code."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import AnalyzerHints, Endpoints, GpuProfile
from headwaters.core.hardware.residency import default_max_loaded
from headwaters.core.hardware.state import compute_reserve, select_profile
from headwaters.core.rules import ExecutorRules, HardwareRules, RouterRules, Rules, WallsRules

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE = REPO_ROOT / "headwaters" / "core"
CATALOG = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)

MOVED_NAMES = [
    "RESERVE_DISPLAY_GB",
    "RESERVE_UNKNOWN_GB",
    "RESERVE_NO_DISPLAY_GB",
    "DEFAULT_BUSY_THRESHOLD_PCT",
    "_LAPTOP_RANGE_GB",
    "_DESKTOP_RANGE_GB",
    "SLOW_RATIO",
    "SPILL_TOLERANCE",
    "_STALE_AFTER_S",
    "DEFAULT_EFFORT",
    "DEFAULT_CONNECT_S",
    "OPENROUTER_BASE",
    "OPENROUTER_KEY_URL",
    "_KEYWORD_SCAN_HEAD",
    "_KEYWORD_SCAN_TAIL",
    "_SHORT_PROMPT_TOKENS",
    "_IMAGE_BONUS",
    "_JSON_BONUS",
    "_FENCE_BONUS",
    "_MAX_PROMPT_CHARS",
    "_MIN_CONFIDENCE",
    "ExecutorConfig",
    "https://openrouter.ai",
]
ALLOWED = {CORE / "rules.py", CORE / "catalog" / "schema.py"}


def test_no_moved_constant_is_left_in_core_code() -> None:
    hits: list[str] = []
    for path in CORE.rglob("*.py"):
        if path in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        hits += [f"{path.name}: {n}" for n in MOVED_NAMES if re.search(re.escape(n), text)]
    assert hits == []


def test_defaults_are_the_old_hardcoded_values() -> None:
    hw = HardwareRules()
    assert (hw.reserve_display_gb, hw.reserve_unknown_gb, hw.reserve_no_display_gb) == (
        2.0,
        2.5,
        1.0,
    )
    assert (hw.busy_threshold_pct, hw.warm_bonus, hw.eviction_penalty_per_model) == (50, 0.15, 0.25)
    assert (hw.sysmem_slow_ratio, hw.sysmem_spill_tolerance) == (0.5, 0.02)
    ex = ExecutorRules()
    assert (ex.cloud_first_token_s, ex.local_warm_first_token_s) == (30, 60)
    assert (ex.local_cold_first_token_s, ex.stream_idle_s, ex.connect_s) == (240, 60, 5)
    assert ex.default_effort == "medium"
    assert RouterRules().max_attempts == 4
    assert WallsRules().streak_reset_s == 3600
    h = AnalyzerHints()
    assert (h.keyword_scan_head_chars, h.keyword_scan_tail_chars, h.short_prompt_tokens) == (
        4000,
        4000,
        100,
    )
    assert (h.image_bonus, h.json_bonus, h.fence_bonus) == (3, 2, 2)
    assert (h.classifier_max_prompt_chars, h.classifier_min_confidence) == (2000, 0.75)
    assert Endpoints().openrouter_base_url == "https://openrouter.ai/api/v1"


def test_seed_profiles_keep_the_old_behaviour() -> None:
    profiles = CATALOG.gpu_profiles
    assert profiles["laptop_12gb"].vram_range_gb == (10, 13.5)
    assert profiles["desktop_16gb"].vram_range_gb == (14, 17.5)
    assert default_max_loaded("laptop_12gb", profiles) == 1
    assert default_max_loaded("desktop_16gb", profiles) == 2
    assert select_profile(12.0, "auto", profiles) == "laptop_12gb"
    assert select_profile(16.0, "auto", profiles) == "desktop_16gb"
    assert select_profile(24.0, "auto", profiles) == "custom"
    assert select_profile(12.0, "auto", None) == "custom"


def test_reserve_follows_the_rules() -> None:
    assert compute_reserve(True, None, HardwareRules())[0] == 2.0
    assert compute_reserve(True, None, HardwareRules(reserve_display_gb=3.0))[0] == 3.0
    assert compute_reserve(False, None, HardwareRules(reserve_no_display_gb=0.5))[0] == 0.5
    assert compute_reserve(None, None, HardwareRules(reserve_unknown_gb=4.0))[0] == 4.0


@pytest.mark.parametrize(
    ("model", "key", "bad"),
    [
        (HardwareRules, "reserve_display_gb", -1),
        (HardwareRules, "busy_threshold_pct", 101),
        (HardwareRules, "warm_bonus", -0.1),
        (HardwareRules, "sysmem_slow_ratio", 0),
        (ExecutorRules, "cloud_first_token_s", 0),
        (ExecutorRules, "stream_idle_s", -5),
        (ExecutorRules, "connect_s", 0),
        (ExecutorRules, "default_effort", ""),
        (RouterRules, "max_attempts", 0),
        (WallsRules, "streak_reset_s", 0),
    ],
)
def test_invalid_rules_values_are_rejected(model: type, key: str, bad: Any) -> None:
    with pytest.raises(ValidationError):
        model(**{key: bad})


def test_rules_yaml_round_trip_of_new_keys() -> None:
    rules = Rules.model_validate(
        {
            "executor": {"stream_idle_s": 7, "default_effort": "high"},
            "router": {"max_attempts": 2},
            "tiers": {"local": {"hardware": {"warm_bonus": 0.2}}},
        }
    )
    assert rules.executor.stream_idle_s == 7
    assert rules.executor.default_effort == "high"
    assert rules.router.max_attempts == 2
    assert rules.tiers.local.hardware.warm_bonus == 0.2


@pytest.mark.parametrize(
    ("model", "key", "bad"),
    [
        (AnalyzerHints, "keyword_scan_head_chars", -1),
        (AnalyzerHints, "short_prompt_tokens", -1),
        (AnalyzerHints, "image_bonus", -1),
        (AnalyzerHints, "classifier_max_prompt_chars", 0),
        (AnalyzerHints, "classifier_min_confidence", 1.5),
        (Endpoints, "openrouter_base_url", ""),
    ],
)
def test_invalid_catalog_values_are_rejected(model: type, key: str, bad: Any) -> None:
    with pytest.raises(ValidationError):
        model(**{key: bad})


def test_gpu_profile_range_is_validated() -> None:
    base = CATALOG.gpu_profiles["laptop_12gb"].model_dump()
    base["vram_range_gb"] = (14, 10)
    with pytest.raises(ValidationError):
        GpuProfile(**base)
    base["vram_range_gb"] = (10, 13.5)
    base["max_loaded"] = 0
    with pytest.raises(ValidationError):
        GpuProfile(**base)
