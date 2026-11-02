"""HDW-109: sysmem-fallback detection and residency (warm bonus / eviction) inputs."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import ModelEntry
from headwaters.core.hardware.residency import (
    LoadedModel,
    ResidencyConfig,
    default_max_loaded,
    residency_inputs,
)
from headwaters.core.hardware.sysmem import (
    SysmemTracker,
    detect_sysmem_fallback,
    expected_tps_for,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH).gpu_profiles


def test_sysmem_detection_spilled() -> None:
    v = detect_sysmem_fallback(8.0, 35.0, size_bytes=10_000, size_vram_bytes=6_000)
    assert v.detected
    assert v.evidence == "spilled"
    assert v.ratio == pytest.approx(8 / 35)
    assert v.hint is not None
    assert "Sysmem Fallback Policy" in v.hint


def test_sysmem_detection_slow_but_resident() -> None:
    v = detect_sysmem_fallback(10.0, 35.0, 10_000, 10_000)
    assert (v.detected, v.evidence) == (True, "resident_but_slow")


def test_sysmem_not_detected_cases() -> None:
    assert detect_sysmem_fallback(30.0, 35.0, 10_000, 5_000).evidence == "none"
    assert detect_sysmem_fallback(None, 35.0, 1, 1).evidence == "no_data"
    assert detect_sysmem_fallback(10.0, None, 1, 1).evidence == "no_data"
    busy = detect_sysmem_fallback(5.0, 35.0, 10_000, 5_000, gpu_busy=True)
    assert (busy.detected, busy.evidence) == (False, "gpu_busy")


def test_sysmem_tracker_and_expected_tps() -> None:
    tracker = SysmemTracker()
    tracker.record("m", detect_sysmem_fallback(5.0, 35.0, 10_000, 5_000))
    assert tracker.is_flagged("m")
    assert "Sysmem Fallback Policy" in tracker.hints()["m"]
    tracker.record("m", detect_sysmem_fallback(34.0, 35.0, 10_000, 10_000))
    assert not tracker.is_flagged("m")
    entry = ModelEntry(id="m", provider="ollama", expected_tps={"laptop_12gb": 35.0})
    assert expected_tps_for(entry, "laptop_12gb") == 35.0
    assert expected_tps_for(entry, "custom") is None


def test_residency_warm_and_eviction() -> None:
    loaded = [LoadedModel("a", 9.0)]
    warm = residency_inputs("a", 9.0, loaded, 10.5)
    assert warm.warm
    assert warm.warm_bonus > 0
    assert warm.evicts == ()
    cold = residency_inputs("b", 6.0, loaded, 10.5)
    assert not cold.warm
    assert cold.evicts == ("a",)
    assert cold.eviction_penalty == ResidencyConfig().eviction_penalty_per_model
    free = residency_inputs("b", 6.0, [], 10.5)
    assert free.evicts == ()
    assert free.eviction_penalty == 0.0


def test_residency_two_models_and_defaults() -> None:
    loaded = [LoadedModel("a", 9.0)]
    two = ResidencyConfig(max_loaded_models=2)
    assert residency_inputs("b", 3.0, loaded, 14.5, two).evicts == ()
    assert residency_inputs("b", 6.0, loaded, 14.5, two).evicts == ("a",)  # VRAM, not count
    assert default_max_loaded("laptop_12gb", PROFILES) == 1
    assert default_max_loaded("desktop_16gb", PROFILES) == 2
