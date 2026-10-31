"""HDW-108: NVML and nvidia-smi probes with mocked driver output."""

from __future__ import annotations

import sys
import types
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.core.hardware import gpu as gpu_mod
from headwaters.core.hardware.gpu import probe_nvidia_smi, probe_nvml

MIB = 1024 * 1024
_NAME = b"NVIDIA GeForce RTX 4080 Laptop GPU"


def _fake_nvml(
    total_mib: int = 12288,
    free_mib: int = 11000,
    display: int | Exception = 0,
    procs: Sequence[Any] = (),
    fail_init: bool = False,
) -> types.ModuleType:
    mod: Any = types.ModuleType("fake_nvml")

    def init() -> None:
        if fail_init:
            raise RuntimeError("no driver")

    def display_active(_h: object) -> int:
        if isinstance(display, Exception):
            raise display
        return display

    mod.nvmlInit = init
    mod.nvmlShutdown = lambda: None
    mod.nvmlDeviceGetCount = lambda: 1
    mod.nvmlDeviceGetHandleByIndex = lambda i: i
    mod.nvmlDeviceGetMemoryInfo = lambda h: SimpleNamespace(
        total=total_mib * MIB, free=free_mib * MIB, used=(total_mib - free_mib) * MIB
    )
    mod.nvmlDeviceGetName = lambda h: _NAME
    mod.nvmlDeviceGetDisplayActive = display_active
    mod.nvmlDeviceGetComputeRunningProcesses = lambda h: list(procs)
    mod.nvmlDeviceGetGraphicsRunningProcesses = lambda h: []
    return mod


@pytest.fixture
def install_nvml(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(mod: types.ModuleType) -> str:
        monkeypatch.setitem(sys.modules, "fake_nvml", mod)
        return "fake_nvml"

    return install


def test_nvml_probe_reads_gpu(install_nvml: Any) -> None:
    proc = SimpleNamespace(pid=1234, usedGpuMemory=2048 * MIB)
    info = probe_nvml(install_nvml(_fake_nvml(display=1, procs=[proc])))
    assert info is not None
    assert info.name == "NVIDIA GeForce RTX 4080 Laptop GPU"
    assert info.total_mb == 12288
    assert info.free_mb == 11000
    assert info.used_mb == 1288
    assert info.display_active is True
    assert info.source == "nvml"
    assert info.processes[0].pid == 1234
    assert info.processes[0].mem_mb == 2048


def test_nvml_display_query_failure_is_unknown(install_nvml: Any) -> None:
    info = probe_nvml(install_nvml(_fake_nvml(display=RuntimeError("not supported"))))
    assert info is not None
    assert info.display_active is None


def test_nvml_hidden_process_memory_is_none(install_nvml: Any) -> None:
    proc = SimpleNamespace(pid=7, usedGpuMemory=(1 << 64) - 1)
    info = probe_nvml(install_nvml(_fake_nvml(procs=[proc])))
    assert info is not None
    assert info.processes[0].mem_mb is None


def test_nvml_unavailable_returns_none(install_nvml: Any) -> None:
    assert probe_nvml(install_nvml(_fake_nvml(fail_init=True))) is None
    assert probe_nvml("definitely_not_a_module_xyz") is None


def _smi(gpu_line: str | None, proc_lines: str = "") -> Any:
    def run(args: Sequence[str]) -> str | None:
        if any(a.startswith("--query-gpu") for a in args):
            return gpu_line
        return proc_lines

    return run


def test_smi_probe_parses_csv() -> None:
    runner = _smi(
        "NVIDIA GeForce RTX 5080, 16303, 15000, Disabled\n",
        "4321, C:\\Games\\game.exe, 9000\n99, ollama.exe, 5000\n",
    )
    info = probe_nvidia_smi(runner)
    assert info is not None
    assert info.source == "nvidia-smi"
    assert info.name == "NVIDIA GeForce RTX 5080"
    assert info.total_mb == 16303
    assert info.free_mb == 15000
    assert info.display_active is False
    assert [(p.pid, p.name, p.mem_mb) for p in info.processes] == [
        (4321, "game.exe", 9000.0),
        (99, "ollama.exe", 5000.0),
    ]


def test_smi_probe_garbage_and_missing() -> None:
    assert probe_nvidia_smi(_smi(None)) is None
    assert probe_nvidia_smi(_smi("")) is None
    assert probe_nvidia_smi(_smi("NVIDIA, [N/A], [N/A], Enabled")) is None
    assert probe_nvidia_smi(_smi("too, few")) is None
    info = probe_nvidia_smi(_smi("GPU X, 8192, 8000, [N/A]"))
    assert info is not None
    assert info.display_active is None


def test_smi_picks_largest_gpu() -> None:
    info = probe_nvidia_smi(_smi("Small, 4096, 4000, Enabled\nBig, 16303, 9000, Disabled\n"))
    assert info is not None
    assert info.name == "Big"


def test_run_nvidia_smi_without_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gpu_mod, "find_nvidia_smi", lambda: None)
    assert gpu_mod.run_nvidia_smi(["-L"]) is None
