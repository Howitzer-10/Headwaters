"""GPU probes (HDW-108): NVML first, then ``nvidia-smi``. Pure data in, no policy.

Both probes return ``None`` when they cannot answer; callers fall back to the next source and
finally to manual config. Nothing here talks to the network.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

_log = logging.getLogger(__name__)

_MIB = 1024 * 1024
# NVML reports "not available" per-process memory as a huge sentinel on Windows (WDDM).
_NVML_NA_THRESHOLD = 1 << 60
_SMI_TIMEOUT_S = 5.0

GpuSource = Literal["nvml", "nvidia-smi", "manual"]


@dataclass(frozen=True)
class GpuProcess:
    """One process using the GPU. ``mem_mb`` is ``None`` when the driver hides it."""

    pid: int
    name: str
    mem_mb: float | None


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: float
    free_mb: float | None  # None for a manual entry: nothing was measured
    display_active: bool | None  # None = unknown
    source: GpuSource
    processes: tuple[GpuProcess, ...] = field(default_factory=tuple)

    @property
    def used_mb(self) -> float | None:
        return None if self.free_mb is None else max(0.0, self.total_mb - self.free_mb)


GpuProbe = Callable[[], GpuInfo | None]


def _process_name(pid: int) -> str:
    try:
        import psutil

        return str(psutil.Process(pid).name())
    except Exception:
        return ""


def _nvml_mem_mb(raw: object) -> float | None:
    if not isinstance(raw, int) or raw < 0 or raw >= _NVML_NA_THRESHOLD:
        return None
    return raw / _MIB


def _text(value: object) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _nvml_processes(nvml: Any, handle: Any) -> tuple[GpuProcess, ...]:
    seen: dict[int, GpuProcess] = {}
    for getter in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
        try:
            entries = getattr(nvml, getter)(handle)
        except Exception:
            continue
        for entry in entries:
            pid = int(entry.pid)
            mem = _nvml_mem_mb(getattr(entry, "usedGpuMemory", None))
            old = seen.get(pid)
            if old is None or (old.mem_mb is None and mem is not None):
                seen[pid] = GpuProcess(pid=pid, name=_process_name(pid), mem_mb=mem)
    return tuple(seen.values())


def probe_nvml(module_name: str = "pynvml") -> GpuInfo | None:
    """Read the GPU with the largest memory through NVML (``nvidia-ml-py``)."""
    try:
        nvml = importlib.import_module(module_name)
        nvml.nvmlInit()
    except Exception as exc:
        _log.debug("NVML unavailable: %s", exc)
        return None
    try:
        best: tuple[float, Any] | None = None
        for index in range(int(nvml.nvmlDeviceGetCount())):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            total = float(nvml.nvmlDeviceGetMemoryInfo(handle).total)
            if best is None or total > best[0]:
                best = (total, handle)
        if best is None:
            return None
        handle = best[1]
        mem = nvml.nvmlDeviceGetMemoryInfo(handle)
        try:
            # R14 resolved: nvidia-ml-py exposes nvmlDeviceGetDisplayActive (0/1).
            display: bool | None = bool(nvml.nvmlDeviceGetDisplayActive(handle))
        except Exception:
            display = None
        return GpuInfo(
            name=_text(nvml.nvmlDeviceGetName(handle)),
            total_mb=float(mem.total) / _MIB,
            free_mb=float(mem.free) / _MIB,
            display_active=display,
            source="nvml",
            processes=_nvml_processes(nvml, handle),
        )
    except Exception as exc:
        _log.debug("NVML query failed: %s", exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            nvml.nvmlShutdown()


SmiRunner = Callable[[Sequence[str]], str | None]


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    candidate = os.path.join(system_root, "System32", "nvidia-smi.exe")
    return candidate if os.path.isfile(candidate) else None


def run_nvidia_smi(args: Sequence[str]) -> str | None:
    exe = find_nvidia_smi()
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            timeout=_SMI_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _smi_number(cell: str) -> float | None:
    try:
        return float(cell.strip())
    except ValueError:
        return None


def _smi_display(cell: str) -> bool | None:
    value = cell.strip().lower()
    if value in {"enabled", "yes", "active"}:
        return True
    if value in {"disabled", "no", "inactive"}:
        return False
    return None


def _smi_processes(runner: SmiRunner) -> tuple[GpuProcess, ...]:
    out = runner(
        ["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]
    )
    procs: list[GpuProcess] = []
    for line in (out or "").splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        name = os.path.basename(cells[1].replace("\\", "/"))
        procs.append(GpuProcess(pid=int(cells[0]), name=name, mem_mb=_smi_number(cells[2])))
    return tuple(procs)


def probe_nvidia_smi(runner: SmiRunner = run_nvidia_smi) -> GpuInfo | None:
    """Fallback probe: parse ``nvidia-smi --query-gpu`` CSV (largest GPU wins)."""
    out = runner(
        [
            "--query-gpu=name,memory.total,memory.free,display_active",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return None
    best: GpuInfo | None = None
    for line in out.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 4:
            continue
        total, free = _smi_number(cells[1]), _smi_number(cells[2])
        if total is None or free is None or total <= 0:
            continue
        info = GpuInfo(
            name=cells[0],
            total_mb=total,
            free_mb=free,
            display_active=_smi_display(cells[3]),
            source="nvidia-smi",
        )
        if best is None or info.total_mb > best.total_mb:
            best = info
    if best is None:
        return None
    return GpuInfo(
        name=best.name,
        total_mb=best.total_mb,
        free_mb=best.free_mb,
        display_active=best.display_active,
        source="nvidia-smi",
        processes=_smi_processes(runner),
    )
