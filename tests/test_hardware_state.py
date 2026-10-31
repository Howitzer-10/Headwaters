"""HDW-108: reserve, profile, battery, GPU-busy and the HardwareState snapshot."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.hardware.gpu import GpuInfo, GpuProcess
from headwaters.core.hardware.state import (
    BatteryState,
    build_state,
    compute_reserve,
    detect_gpu_busy,
    read_battery,
    select_profile,
)
from headwaters.core.rules import LocalTier

AC = BatteryState(False, False)


def gpu_info(
    total: float = 12288,
    free: float | None = 11000,
    display: bool | None = False,
    procs: tuple[GpuProcess, ...] = (),
    source: Any = "nvml",
) -> GpuInfo:
    return GpuInfo("RTX", total, free, display, source, procs)


@pytest.mark.parametrize(
    ("display", "override", "expected", "source"),
    [
        (True, None, 2.0, "display_active"),
        (False, None, 1.0, "no_display"),
        (None, None, 2.5, "unknown_display"),
        (True, 3.5, 3.5, "override"),
        (False, 0.0, 0.0, "override"),
    ],
)
def test_compute_reserve(
    display: bool | None, override: float | None, expected: float, source: str
) -> None:
    assert compute_reserve(display, override) == (expected, source)


@pytest.mark.parametrize(
    ("total_gb", "requested", "expected"),
    [
        (12.0, "auto", "laptop_12gb"),
        (16.0, "auto", "desktop_16gb"),
        (24.0, "auto", "custom"),
        (8.0, "auto", "custom"),
        (None, "auto", "custom"),
        (24.0, "laptop_12gb", "laptop_12gb"),
        (12.0, "custom", "custom"),
        (12.0, "desktop_16gb", "desktop_16gb"),
    ],
)
def test_select_profile(total_gb: float | None, requested: str, expected: str) -> None:
    assert select_profile(total_gb, requested) == expected


def test_gpu_busy_by_known_process() -> None:
    game = GpuProcess(10, "game.exe", 8000)
    busy = detect_gpu_busy(gpu_info(procs=(game,)), 50, own_pid=1)
    assert busy.busy
    assert busy.top_process == "game.exe"
    assert round(busy.other_used_pct) == 65


def test_gpu_busy_ignores_ollama_and_self() -> None:
    procs = (GpuProcess(1, "python.exe", 9000), GpuProcess(2, "ollama.exe", 9000))
    assert not detect_gpu_busy(gpu_info(procs=procs), 50, own_pid=1).busy


def test_gpu_busy_hidden_memory_uses_used_vram() -> None:
    hidden = GpuProcess(10, "render.exe", None)
    busy = detect_gpu_busy(gpu_info(free=2000, procs=(hidden,)), 50, own_pid=1)
    assert busy.busy
    assert busy.top_process == "render.exe"
    # Ollama holds memory but its size is hidden: cannot attribute, so not busy.
    ollama = GpuProcess(11, "ollama.exe", None)
    assert not detect_gpu_busy(gpu_info(free=2000, procs=(ollama,)), 50, own_pid=1).busy


def test_gpu_busy_no_gpu_and_threshold() -> None:
    assert not detect_gpu_busy(None).busy
    small = GpuProcess(10, "app.exe", 1000)
    assert not detect_gpu_busy(gpu_info(procs=(small,)), 50, own_pid=1).busy
    assert detect_gpu_busy(gpu_info(procs=(small,)), 5, own_pid=1).busy


def test_build_state_laptop_no_display() -> None:
    state = build_state(gpu_info(display=False), LocalTier(), BatteryState(True, False, 90.0))
    assert state.profile == "laptop_12gb"
    assert state.reserve_gb == 1.0
    assert state.budget_gb == pytest.approx(11.0)
    assert state.local_suggestion == "ok"
    assert state.battery_policy == "normal"


def test_build_state_desktop_display_and_override() -> None:
    gpu = gpu_info(total=16384, free=15000, display=True)
    state = build_state(gpu, LocalTier(), AC)
    assert (state.profile, state.reserve_gb, state.budget_gb) == ("desktop_16gb", 2.0, 14.0)
    over = build_state(gpu, LocalTier(reserve_gb=4.0), AC)
    assert (over.reserve_gb, over.reserve_source, over.budget_gb) == (4.0, "override", 12.0)


def test_build_state_unknown_display_notes() -> None:
    state = build_state(gpu_info(display=None), LocalTier(), AC)
    assert state.reserve_gb == 2.5
    assert any("Display status unknown" in n for n in state.notes)


@pytest.mark.parametrize("policy", ["prefer_cloud", "normal", "local_only"])
def test_battery_policy_applies_only_on_battery(policy: str) -> None:
    local = LocalTier(on_battery=policy)  # type: ignore[arg-type]
    on = build_state(gpu_info(), local, BatteryState(True, True, 40.0))
    ac = build_state(gpu_info(), local, BatteryState(True, False, 40.0))
    assert on.battery_policy == policy
    assert ac.battery_policy == "normal"


def test_busy_gpu_is_degraded() -> None:
    game = GpuProcess(10, "game.exe", 9000)
    state = build_state(gpu_info(procs=(game,)), LocalTier(), AC)
    assert state.local_suggestion == "degraded"
    assert state.gpu_busy.busy


def test_read_battery(monkeypatch: pytest.MonkeyPatch) -> None:
    import psutil

    monkeypatch.setattr(psutil, "sensors_battery", lambda: None, raising=False)
    assert read_battery() == BatteryState(False, False)
    reading = SimpleNamespace(percent=55, power_plugged=False)
    monkeypatch.setattr(psutil, "sensors_battery", lambda: reading, raising=False)
    assert read_battery() == BatteryState(True, True, 55.0)
    reading.power_plugged = None
    assert not read_battery().on_battery

    def boom() -> None:
        raise OSError("no sensor")

    monkeypatch.setattr(psutil, "sensors_battery", boom, raising=False)
    assert read_battery() == BatteryState(False, False)
