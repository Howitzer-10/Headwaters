"""HDW-145 (FU-202-1): which Windows Job Object rule decides whether a daemon outlives its host.

A host that runs the shim in a kill-on-close Job Object reaps everything still in the job when it
closes. These tests run the real ``daemon_control._spawn_detached`` (the code that starts the
daemon) inside such a job, close the job and look at whether the detached child survived:

- kill-on-close, no breakaway allowed: ``CREATE_BREAKAWAY_FROM_JOB`` is refused, the fallback
  launch stays in the job, and the child dies with it (the failure FU-202-1 saw under the SDK's
  stdio client);
- kill-on-close with ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``: the breakaway launch works, the child lives;
- kill-on-close with ``JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK``: children are never in the job.

Windows only. Everything runs on this machine; no daemon, no model, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import psutil
import pytest

pytestmark = [
    pytest.mark.windows,
    pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows only"),
]

KILL_ON_CLOSE = 0x2000
BREAKAWAY_OK = 0x0800
SILENT_BREAKAWAY_OK = 0x1000
EXTENDED_LIMIT_CLASS = 9
HELPER = (
    "import json, sys, time\n"
    "from headwaters.core.daemon_control import _spawn_detached\n"
    "child = _spawn_detached([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
    "print(json.dumps({'pid': child.pid}), flush=True)\n"
    "sys.stdin.readline()\n"
)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Io(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in "abcdef"]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("Basic", _Basic),
            ("Io", _Io),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def _kernel() -> Any:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


@contextmanager
def _host(flags: int) -> Iterator[tuple[Any, int, Any]]:
    """A "host": a job with ``flags``, and the helper (the shim) started inside it.

    The helper starts suspended so the venv launcher and the interpreter it starts both inherit
    the job; it prints the pid ``_spawn_detached`` returned and waits for a line on stdin.
    """
    kernel = _kernel()
    job = kernel.CreateJobObjectW(None, None)
    assert job, ctypes.get_last_error()
    limits = _Extended()
    limits.Basic.LimitFlags = flags
    assert kernel.SetInformationJobObject(
        job, EXTENDED_LIMIT_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)
    ), ctypes.get_last_error()
    helper = subprocess.Popen(
        [sys.executable, "-c", HELPER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        creationflags=0x00000004,  # CREATE_SUSPENDED
    )
    process = kernel.OpenProcess(0x0100 | 0x0001, False, helper.pid)  # SET_QUOTA | TERMINATE
    assert process, ctypes.get_last_error()
    try:
        assigned = kernel.AssignProcessToJobObject(job, process)
        assert assigned, f"could not put the helper in the job: {ctypes.get_last_error()}"
        psutil.Process(helper.pid).resume()
        assert helper.stdout is not None
        line = helper.stdout.readline()
        assert line, "the helper printed no pid"
        yield kernel, int(json.loads(line)["pid"]), (job, helper)
    finally:
        kernel.CloseHandle(process)
        for pid in _tree(helper.pid):
            _kill(pid)


def _tree(pid: int) -> list[int]:
    try:
        root = psutil.Process(pid)
    except psutil.Error:
        return []
    return [root.pid, *[c.pid for c in root.children(recursive=True)]]


def _kill(pid: int) -> None:
    with suppress(psutil.Error):
        psutil.Process(pid).kill()


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _close_job_and_wait(kernel: Any, job: Any, pid: int) -> bool:
    """Close the job (the host closing); True when ``pid`` is still running a moment later."""
    kernel.CloseHandle(job)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _alive(pid):
        time.sleep(0.1)
    return _alive(pid)


def _survives(flags: int) -> bool:
    with _host(flags) as (kernel, child_pid, (job, _helper)):
        assert _alive(child_pid), "the detached child was not running before the job closed"
        survived = _close_job_and_wait(kernel, job, child_pid)
        if survived:
            _kill(child_pid)
        return survived


def test_a_kill_on_close_job_without_breakaway_reaps_the_daemon() -> None:
    """FU-202-1: the breakaway launch is refused and the fallback stays in the job."""
    assert _survives(KILL_ON_CLOSE) is False


def test_a_kill_on_close_job_that_allows_breakaway_does_not_reap_the_daemon() -> None:
    """``_spawn_detached`` tries ``CREATE_BREAKAWAY_FROM_JOB`` first, so this host is safe."""
    assert _survives(KILL_ON_CLOSE | BREAKAWAY_OK) is True


def test_a_kill_on_close_job_with_silent_breakaway_does_not_reap_the_daemon() -> None:
    """Children are never put in the job, so no flag is needed; the plain launch is enough."""
    assert _survives(KILL_ON_CLOSE | SILENT_BREAKAWAY_OK) is True
