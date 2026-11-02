"""``HardwareState`` snapshot (HDW-108): GPU, reserve, profile, battery, GPU-busy.

Detection order is NVML -> ``nvidia-smi`` -> manual config. With no NVIDIA GPU and no manual
config the state says ``cloud_only_suggested`` instead of raising. HDW-109 does the KV/fit math
on top of ``budget_gb``; this module only measures and classifies.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.schema import GpuProfile
from headwaters.core.hardware.gpu import (
    GpuInfo,
    GpuProbe,
    GpuProcess,
    probe_nvidia_smi,
    probe_nvml,
)
from headwaters.core.hardware.ollama_vram import read_ollama_vram_mb
from headwaters.core.rules import HardwareRules, LocalTier

Profile = Literal["laptop_12gb", "desktop_16gb", "custom"]
ReserveSource = Literal["override", "display_active", "no_display", "unknown_display"]
LocalSuggestion = Literal["ok", "degraded", "cloud_only_suggested"]
OnBatteryPolicy = Literal["prefer_cloud", "normal", "local_only"]

DEFAULT_CACHE_TTL_S = 5.0

_OLLAMA_PREFIX = "ollama"
_RUNNER_PREFIX = "llama-server"


@dataclass(frozen=True)
class ManualGpu:
    """User-supplied GPU description, used only when detection finds nothing."""

    name: str
    total_gb: float


@dataclass(frozen=True)
class BatteryState:
    present: bool
    on_battery: bool
    percent: float | None = None


@dataclass(frozen=True)
class GpuBusy:
    busy: bool
    other_used_mb: float
    other_used_pct: float
    top_process: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class HardwareState:
    gpu: GpuInfo | None
    profile: Profile
    reserve_gb: float
    reserve_source: ReserveSource
    budget_gb: float
    battery: BatteryState
    battery_policy: OnBatteryPolicy  # the effective rule right now ("normal" when on AC)
    gpu_busy: GpuBusy
    local_suggestion: LocalSuggestion
    notes: tuple[str, ...] = ()
    taken_at: float = 0.0

    @property
    def has_gpu(self) -> bool:
        return self.gpu is not None


def select_profile(
    total_gb: float | None,
    requested: str = "auto",
    profiles: Mapping[str, GpuProfile] | None = None,
) -> Profile:
    """Pick a profile: an explicit rules value wins, else classify by total VRAM against the
    catalog ranges (``vram_range_gb``). Without catalog ranges the profile is ``custom``."""
    if requested == "laptop_12gb":
        return "laptop_12gb"
    if requested == "desktop_16gb":
        return "desktop_16gb"
    if requested == "custom" or total_gb is None:
        return "custom"
    for name in ("laptop_12gb", "desktop_16gb"):
        found = (profiles or {}).get(name)
        if found is not None and found.vram_range_gb is not None:
            low, high = found.vram_range_gb
            if low <= total_gb <= high:
                return name
    return "custom"


def compute_reserve(
    display_active: bool | None, override_gb: float | None, rules: HardwareRules | None = None
) -> tuple[float, ReserveSource]:
    """Reserve in GB. A user override always wins; unknown display uses the higher value."""
    rules = rules or HardwareRules()
    if override_gb is not None:
        return override_gb, "override"
    if display_active is None:
        return rules.reserve_unknown_gb, "unknown_display"
    if display_active:
        return rules.reserve_display_gb, "display_active"
    return rules.reserve_no_display_gb, "no_display"


def read_battery() -> BatteryState:
    """Battery via ``psutil.sensors_battery()``; no battery (desktop) is not on-battery."""
    try:
        import psutil

        reading = psutil.sensors_battery()
    except Exception:
        return BatteryState(present=False, on_battery=False)
    if reading is None:
        return BatteryState(present=False, on_battery=False)
    return BatteryState(
        present=True,
        on_battery=reading.power_plugged is False,  # None = unknown: treat as plugged in
        percent=float(reading.percent),
    )


def _is_ollama(proc: GpuProcess) -> bool:
    """Ollama's server, or its model runner (HDW-144).

    The runner is a child process named ``llama-server.exe`` that lives under Ollama's own
    install folder (seen live: ``...\\Programs\\Ollama\\lib\\ollama\\llama-server.exe``), so the
    name alone never matched and the runner was counted as another app. A runner is accepted
    only when a folder in its path starts with ``ollama``; the same name elsewhere is not ours.
    """
    name = proc.name.lower()
    if name.startswith(_OLLAMA_PREFIX):
        return True
    if not name.startswith(_RUNNER_PREFIX):
        return False
    folders = proc.path.replace("\\", "/").lower().split("/")[:-1]
    return any(part.startswith(_OLLAMA_PREFIX) for part in folders)


def detect_gpu_busy(
    gpu: GpuInfo | None,
    threshold_pct: float | None = None,
    own_pid: int | None = None,
    ollama_vram_mb: float | None = None,
) -> GpuBusy:
    """Busy = other (non-Ollama, non-us) processes hold more than ``threshold_pct`` of VRAM.

    ``ollama_vram_mb`` is what Ollama's ``/api/ps`` says its loaded models hold (HDW-144).
    When Windows hides per-process memory and no Ollama process is listed, the used VRAM is
    all there is to go on; Ollama's own share is subtracted first, so a lone loaded model does
    not read as another app. ``None`` means unknown (Ollama not reachable): nothing is
    subtracted, and the whole used VRAM still counts as other.
    """
    if gpu is None or gpu.total_mb <= 0:
        return GpuBusy(False, 0.0, 0.0)
    own = os.getpid() if own_pid is None else own_pid
    others = [p for p in gpu.processes if p.pid != own and not _is_ollama(p)]
    known = [p for p in others if p.mem_mb is not None]
    if known:
        used = sum(p.mem_mb or 0.0 for p in known)
        top = max(known, key=lambda p: p.mem_mb or 0.0)
        top_name: str | None = top.name or str(top.pid)
    elif gpu.free_mb is not None and not any(_is_ollama(p) for p in gpu.processes):
        # Windows hides per-process memory; all used VRAM is "other" except what Ollama holds.
        used = max(0.0, (gpu.used_mb or 0.0) - (ollama_vram_mb or 0.0))
        top_name = others[0].name if others and others[0].name else None
    else:
        return GpuBusy(False, 0.0, 0.0)
    pct = 100.0 * used / gpu.total_mb
    limit = HardwareRules().busy_threshold_pct if threshold_pct is None else threshold_pct
    busy = pct > limit
    reason = f"other processes hold {pct:.0f}% of VRAM" if busy else ""
    return GpuBusy(busy, used, pct, top_name, reason)


def build_state(
    gpu: GpuInfo | None,
    local: LocalTier,
    battery: BatteryState,
    busy_threshold_pct: float | None = None,
    taken_at: float = 0.0,
    ollama_vram_mb: float | None = None,
    profiles: Mapping[str, GpuProfile] | None = None,
) -> HardwareState:
    notes: list[str] = []
    total_gb = None if gpu is None else gpu.total_mb / 1024
    profile = select_profile(total_gb, local.profile, profiles)
    display = None if gpu is None else gpu.display_active
    reserve, reserve_src = compute_reserve(display, local.reserve_gb, local.hardware)
    budget = 0.0 if total_gb is None else max(0.0, total_gb - reserve)
    busy = detect_gpu_busy(
        gpu,
        local.hardware.busy_threshold_pct if busy_threshold_pct is None else busy_threshold_pct,
        ollama_vram_mb=ollama_vram_mb,
    )
    policy: OnBatteryPolicy = local.on_battery if battery.on_battery else "normal"

    suggestion: LocalSuggestion
    if gpu is None:
        suggestion = "cloud_only_suggested"
        notes.append("No NVIDIA GPU detected; local models are not suggested.")
    elif busy.busy:
        suggestion = "degraded"
        notes.append(f"GPU busy: {busy.reason}.")
    else:
        suggestion = "ok"
    if gpu is not None and gpu.source == "manual":
        notes.append("GPU taken from manual config; free VRAM is not measured.")
    if reserve_src == "unknown_display":
        notes.append("Display status unknown; using the higher VRAM reserve.")
    if policy == "prefer_cloud":
        notes.append("On battery: prefer free cloud models.")
    elif policy == "local_only":
        notes.append("On battery: local only (per rules).")
    return HardwareState(
        gpu=gpu,
        profile=profile,
        reserve_gb=reserve,
        reserve_source=reserve_src,
        budget_gb=budget,
        battery=battery,
        battery_policy=policy,
        gpu_busy=busy,
        local_suggestion=suggestion,
        notes=tuple(notes),
        taken_at=taken_at,
    )


def _manual_gpu(local: LocalTier, manual: ManualGpu | None) -> GpuInfo | None:
    if manual is not None:
        return GpuInfo(manual.name, manual.total_gb * 1024, None, None, "manual")
    sizes = {"laptop_12gb": 12.0, "desktop_16gb": 16.0}
    if local.profile in sizes:
        label = f"{local.profile} (manual)"
        return GpuInfo(label, sizes[local.profile] * 1024, None, None, "manual")
    return None


class HardwareMonitor:
    """Detects hardware and caches the ``HardwareState`` for a few seconds."""

    def __init__(
        self,
        local: LocalTier | None = None,
        *,
        probes: Sequence[GpuProbe] | None = None,
        battery_reader: Callable[[], BatteryState] = read_battery,
        manual: ManualGpu | None = None,
        busy_threshold_pct: float | None = None,
        profiles: Mapping[str, GpuProfile] | None = None,
        ttl_s: float = DEFAULT_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
        ollama_vram: Callable[[str], float | None] = read_ollama_vram_mb,
    ) -> None:
        self._ollama_vram = ollama_vram
        self._local = local or LocalTier()
        self._probes: tuple[GpuProbe, ...] = (
            tuple(probes) if probes is not None else (probe_nvml, probe_nvidia_smi)
        )
        self._battery_reader = battery_reader
        self._manual = manual
        self._busy_pct = busy_threshold_pct
        self._profiles = profiles
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: HardwareState | None = None

    def set_local_rules(self, local: LocalTier) -> None:
        with self._lock:
            self._local = local
            self._cached = None

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def get_state(self, force: bool = False) -> HardwareState:
        with self._lock:
            now = self._clock()
            cached = self._cached
            if not force and cached is not None and now - cached.taken_at < self._ttl:
                return cached
            gpu = self._detect()
            state = build_state(
                gpu,
                self._local,
                self._battery_reader(),
                self._busy_pct,
                now,
                self._ollama_share(gpu),
                self._profiles,
            )
            self._cached = state
            return state

    def _ollama_share(self, gpu: GpuInfo | None) -> float | None:
        """Ollama's own VRAM, asked only when the busy check can use it (no per-process data)."""
        if gpu is None or gpu.free_mb is None:
            return None
        if any(p.mem_mb is not None for p in gpu.processes):
            return None
        try:
            return self._ollama_vram(self._local.host)
        except Exception:  # a failed lookup means unknown, never a failed snapshot
            return None

    def _detect(self) -> GpuInfo | None:
        for probe in self._probes:
            try:
                info = probe()
            except Exception:
                info = None
            if info is not None:
                return info
        return _manual_gpu(self._local, self._manual)
