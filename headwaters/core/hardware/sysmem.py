"""NVIDIA Sysmem Fallback detection (HDW-109, v3.1 C1).

Recent Windows drivers can silently spill VRAM into system RAM, so a model runs very slowly
instead of failing. Signal = measured tokens/s well below the catalog ``expected_tps`` for the
GPU profile, read together with ``/api/ps`` ``size_vram`` vs ``size``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.schema import ModelEntry

SLOW_RATIO = 0.5  # measured < 50% of expected counts as slow (first-draft threshold)
SPILL_TOLERANCE = 0.02  # size_vram may be 2% under size before we call it a spill

SYSMEM_HINT = (
    "The model looks like it is running partly out of system RAM. In the NVIDIA Control Panel "
    "open Manage 3D settings > Global Settings and set 'CUDA - Sysmem Fallback Policy' to "
    "'Prefer No Sysmem Fallback', then reload the model. Closing other GPU-heavy apps also helps."
)

Evidence = Literal["spilled", "resident_but_slow", "none", "no_data", "gpu_busy"]


@dataclass(frozen=True)
class SysmemVerdict:
    detected: bool
    evidence: Evidence
    measured_tps: float | None
    expected_tps: float | None
    ratio: float | None = None
    hint: str | None = None


def expected_tps_for(entry: ModelEntry, profile: str) -> float | None:
    return entry.expected_tps.get(profile)


def detect_sysmem_fallback(
    measured_tps: float | None,
    expected_tps: float | None,
    size_bytes: int | None,
    size_vram_bytes: int | None,
    gpu_busy: bool = False,
    slow_ratio: float = SLOW_RATIO,
) -> SysmemVerdict:
    """Slow generation plus ``/api/ps`` evidence. Another busy GPU process explains slowness."""
    if not measured_tps or measured_tps <= 0 or not expected_tps or expected_tps <= 0:
        return SysmemVerdict(False, "no_data", measured_tps, expected_tps)
    ratio = measured_tps / expected_tps
    if ratio >= slow_ratio:
        return SysmemVerdict(False, "none", measured_tps, expected_tps, ratio)
    if gpu_busy:
        return SysmemVerdict(False, "gpu_busy", measured_tps, expected_tps, ratio)
    spilled = bool(
        size_bytes
        and size_vram_bytes is not None
        and size_vram_bytes < size_bytes * (1 - SPILL_TOLERANCE)
    )
    evidence: Evidence = "spilled" if spilled else "resident_but_slow"
    return SysmemVerdict(True, evidence, measured_tps, expected_tps, ratio, SYSMEM_HINT)


class SysmemTracker:
    """Remembers which models were flagged so the fit step marks them ``offload``."""

    def __init__(self) -> None:
        self._flagged: dict[str, SysmemVerdict] = {}
        self._lock = threading.Lock()

    def record(self, model_id: str, verdict: SysmemVerdict) -> None:
        with self._lock:
            if verdict.detected:
                self._flagged[model_id] = verdict
            else:
                self._flagged.pop(model_id, None)

    def is_flagged(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._flagged

    def hints(self) -> dict[str, str]:
        """For ``doctor``: model id -> explanation."""
        with self._lock:
            return {k: v.hint or SYSMEM_HINT for k, v in self._flagged.items()}
