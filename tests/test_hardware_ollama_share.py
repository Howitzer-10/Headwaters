"""HDW-144: Ollama's own VRAM is never another app's; nvidia-smi names decode safely."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from headwaters.core.catalog.http import SourceError
from headwaters.core.hardware.gpu import GpuInfo, GpuProcess, decode_smi, probe_nvidia_smi
from headwaters.core.hardware.ollama_vram import parse_ps_vram_mb, read_ollama_vram_mb
from headwaters.core.hardware.state import BatteryState, HardwareMonitor, detect_gpu_busy
from headwaters.core.rules import LocalTier
from tests.executor_support import REPO_ROOT

MIB = 1024 * 1024
HOST = "http://127.0.0.1:11434"
RUNNER = r"C:\Users\x\AppData\Local\Programs\Ollama\lib\ollama\llama-server.exe"
AC = BatteryState(False, False)


def gpu(used: float, procs: tuple[GpuProcess, ...] = (), total: float = 12288) -> GpuInfo:
    return GpuInfo("RTX", total, total - used, False, "nvml", procs)


def hidden(pid: int, name: str, path: str = "") -> GpuProcess:
    return GpuProcess(pid, name, None, path)


class FakeHttp:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.urls: list[str] = []

    def request_json(self, method: str, url: str, **_: Any) -> Any:
        self.urls.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_ps_sums_size_vram_and_handles_empty_and_junk() -> None:
    reply = {"models": [{"size_vram": 8 * MIB}, {"size_vram": 4 * MIB}, {"size_vram": None}]}
    assert parse_ps_vram_mb(reply) == 12.0
    assert parse_ps_vram_mb({"models": []}) == 0.0
    assert parse_ps_vram_mb({}) == 0.0
    assert parse_ps_vram_mb({"models": "x"}) is None
    assert parse_ps_vram_mb([1]) is None
    assert parse_ps_vram_mb({"models": [{"size_vram": True}, {"size_vram": -5}]}) == 0.0


def test_read_ollama_vram_uses_api_ps_and_fails_soft() -> None:
    http = FakeHttp({"models": [{"size_vram": 10 * MIB}]})
    assert read_ollama_vram_mb(HOST + "/", http) == 10.0
    assert http.urls == [HOST + "/api/ps"]
    assert read_ollama_vram_mb(HOST, FakeHttp(SourceError("down"))) is None
    assert read_ollama_vram_mb(HOST, FakeHttp(OSError("x"))) is None


def test_read_ollama_vram_never_asks_a_remote_host() -> None:
    http = FakeHttp({"models": []})
    assert read_ollama_vram_mb("http://10.0.0.5:11434", http) is None
    assert http.urls == []


def test_ollama_model_alone_is_not_busy_when_memory_is_hidden() -> None:
    # No Ollama process listed: used VRAM minus what /api/ps says Ollama holds.
    info = gpu(7000, (hidden(10, "dwm.exe"),))
    assert detect_gpu_busy(info, 50, own_pid=1).busy  # 57% unexplained
    assert not detect_gpu_busy(info, 50, own_pid=1, ollama_vram_mb=6500).busy


def test_runner_under_the_ollama_folder_is_ollama_not_another_app() -> None:
    # The live shape: every process hidden, the runner listed with its install path.
    procs = (hidden(10, "dwm.exe"), hidden(20, "llama-server.exe", RUNNER))
    assert not detect_gpu_busy(gpu(11800, procs), 50, own_pid=1).busy


def test_runner_name_outside_an_ollama_folder_is_not_ours() -> None:
    procs = (hidden(10, "dwm.exe"), hidden(20, "llama-server.exe", r"C:\tools\llama-server.exe"))
    assert detect_gpu_busy(gpu(11800, procs), 50, own_pid=1).busy
    assert detect_gpu_busy(gpu(11800, (hidden(20, "llama-server.exe"),)), 50, own_pid=1).busy


def test_game_plus_ollama_is_busy_over_the_threshold() -> None:
    game = GpuProcess(10, "game.exe", 7000)
    runner = GpuProcess(20, "llama-server.exe", 4000, RUNNER)
    busy = detect_gpu_busy(gpu(11000, (game, runner)), 50, own_pid=1)
    assert busy.busy
    assert busy.top_process == "game.exe"
    assert round(busy.other_used_mb) == 7000  # Ollama's 4000 is not counted


def test_game_under_the_threshold_with_ollama_is_not_busy() -> None:
    game = GpuProcess(10, "game.exe", 1500)
    runner = GpuProcess(20, "llama-server.exe", 9000, RUNNER)
    assert not detect_gpu_busy(gpu(10500, (game, runner)), 50, own_pid=1).busy


def test_unknown_memory_and_no_ollama_still_counts_all_used_vram() -> None:
    info = gpu(11000, (hidden(10, "render.exe"),))
    busy = detect_gpu_busy(info, 50, own_pid=1, ollama_vram_mb=None)
    assert busy.busy
    assert round(busy.other_used_mb) == 11000
    assert busy.top_process == "render.exe"


def _monitor(info: GpuInfo, reader: Any) -> HardwareMonitor:
    return HardwareMonitor(
        LocalTier(), probes=[lambda: info], battery_reader=lambda: AC, ollama_vram=reader
    )


def test_monitor_subtracts_ollama_share_and_asks_the_local_host() -> None:
    asked: list[str] = []

    def reader(host: str) -> float | None:
        asked.append(host)
        return 10000.0

    state = _monitor(gpu(10500, (hidden(10, "dwm.exe"),)), reader).get_state()
    assert not state.gpu_busy.busy
    assert state.local_suggestion == "ok"
    assert asked == [LocalTier().host]


def test_monitor_failing_lookup_keeps_the_conservative_reading() -> None:
    def reader(host: str) -> float | None:
        raise RuntimeError("boom")

    state = _monitor(gpu(10500, (hidden(10, "dwm.exe"),)), reader).get_state()
    assert state.gpu_busy.busy


def test_monitor_skips_the_lookup_when_per_process_memory_is_known() -> None:
    asked: list[str] = []

    def reader(host: str) -> float | None:
        asked.append(host)
        return 0.0

    info = gpu(9000, (GpuProcess(10, "game.exe", 9000),))
    assert _monitor(info, reader).get_state().gpu_busy.busy
    assert asked == []


def _smi(lines: str) -> Any:
    def run(args: Sequence[str]) -> str | None:
        if any(a.startswith("--query-gpu") for a in args):
            return "GPU X, 12288, 500, Enabled\n"
        return lines

    return run


SAMPLE = REPO_ROOT / "docs" / "nvidia_smi_compute_apps_sample.txt"


def test_real_capture_parses_with_hidden_memory_and_paths() -> None:
    raw = SAMPLE.read_bytes()
    assert all(b < 128 for b in raw)  # the real tool printed pure ASCII here
    info = probe_nvidia_smi(_smi(decode_smi(raw)))
    assert info is not None
    assert len(info.processes) == 40
    assert all(p.mem_mb is None for p in info.processes)  # Windows prints [N/A]
    assert all(p.path.lower().startswith("c:\\") for p in info.processes)
    assert "explorer.exe" in {p.name for p in info.processes}


def test_decode_smi_keeps_utf8_and_survives_other_bytes() -> None:
    utf8 = "7, C:\\Games\\Sp\u00e9cial\u30c4.exe, 100\n".encode()
    assert decode_smi(utf8).startswith("7, C:\\Games\\Sp\u00e9cial\u30c4.exe")
    ansi = b"8, C:\\Games\\Sp\xe9cial.exe, 200\n9, C:\\Games\\ok.exe, 300\n"  # not UTF-8
    text = decode_smi(ansi)
    assert "9, C:\\Games\\ok.exe, 300" in text  # a bad line never spoils its neighbours
    info = probe_nvidia_smi(_smi(text))
    assert info is not None
    assert [(p.pid, p.mem_mb) for p in info.processes] == [(8, 200.0), (9, 300.0)]


@pytest.mark.parametrize("name", ["Sp\u00e9cial.exe", "\u30b2\u30fc\u30e0.exe", "caf\ufffd.exe"])
def test_non_ascii_name_never_changes_the_busy_reading(name: str) -> None:
    line = f"5, C:\\Games\\{name}, 8000\n6, {RUNNER}, 3000\n"
    info = probe_nvidia_smi(_smi(decode_smi(line.encode("utf-8"))))
    assert info is not None
    busy = detect_gpu_busy(info, 50, own_pid=1)
    assert busy.busy
    assert round(busy.other_used_mb) == 8000
    quiet = probe_nvidia_smi(_smi(decode_smi(f"6, {RUNNER}, 3000\n".encode())))
    assert quiet is not None
    assert not detect_gpu_busy(quiet, 50, own_pid=1).busy


def test_subtraction_never_goes_below_zero() -> None:
    info = gpu(3000, (hidden(10, "x.exe"),))
    busy = detect_gpu_busy(info, 5, own_pid=1, ollama_vram_mb=9000)
    assert not busy.busy
    assert busy.other_used_mb == 0.0
