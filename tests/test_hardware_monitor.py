"""HDW-108: HardwareMonitor detection order, graceful no-GPU degrade, caching."""

from __future__ import annotations

from pathlib import Path

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.hardware.gpu import GpuInfo
from headwaters.core.hardware.state import BatteryState, HardwareMonitor, ManualGpu
from headwaters.core.rules import LocalTier

REPO_ROOT = Path(__file__).resolve().parent.parent
PROFILES = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH).gpu_profiles
AC = BatteryState(False, False)


def gpu_info(source: str = "nvml", display: bool | None = False) -> GpuInfo:
    return GpuInfo("RTX", 12288, 11000, display, source, ())  # type: ignore[arg-type]


def _none() -> GpuInfo | None:
    return None


def test_no_nvidia_gpu_degrades_to_cloud_only() -> None:
    state = HardwareMonitor(LocalTier(), probes=[_none, _none]).get_state()
    assert state.gpu is None
    assert not state.has_gpu
    assert state.local_suggestion == "cloud_only_suggested"
    assert state.budget_gb == 0.0
    assert state.profile == "custom"


def test_probe_order_falls_back_and_survives_exceptions() -> None:
    def boom() -> GpuInfo | None:
        raise RuntimeError("driver crash")

    smi = gpu_info("nvidia-smi")
    state = HardwareMonitor(LocalTier(), probes=[boom, lambda: smi]).get_state()
    assert state.gpu is smi


def test_manual_config_fallback() -> None:
    manual = HardwareMonitor(
        LocalTier(), probes=[_none], manual=ManualGpu("My GPU", 12.0), profiles=PROFILES
    )
    state = manual.get_state()
    assert state.gpu is not None
    assert state.gpu.source == "manual"
    assert state.profile == "laptop_12gb"
    assert state.local_suggestion == "ok"
    by_profile = HardwareMonitor(LocalTier(profile="desktop_16gb"), probes=[_none]).get_state()
    assert by_profile.gpu is not None
    assert by_profile.gpu.total_mb == 16 * 1024


def test_state_is_cached_then_refreshed() -> None:
    now = [100.0]
    calls = [0]

    def probe() -> GpuInfo | None:
        calls[0] += 1
        return gpu_info()

    monitor = HardwareMonitor(
        LocalTier(),
        probes=[probe],
        battery_reader=lambda: AC,
        ttl_s=5.0,
        clock=lambda: now[0],
    )
    first = monitor.get_state()
    now[0] += 4.0
    assert monitor.get_state() is first
    assert calls[0] == 1
    now[0] += 2.0
    assert monitor.get_state() is not first
    assert calls[0] == 2
    monitor.get_state(force=True)
    assert calls[0] == 3
    monitor.invalidate()
    monitor.get_state()
    assert calls[0] == 4


def test_set_local_rules_invalidates_cache() -> None:
    monitor = HardwareMonitor(LocalTier(), probes=[lambda: gpu_info()], battery_reader=lambda: AC)
    assert monitor.get_state().reserve_gb == 1.0
    monitor.set_local_rules(LocalTier(reserve_gb=3.0))
    assert monitor.get_state().reserve_gb == 3.0
