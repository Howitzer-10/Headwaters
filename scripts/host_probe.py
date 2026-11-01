"""HDW-145: a tiny stdio MCP server that records how a real host starts it (stdlib only).

Register it in a host (VS Code, Cursor, Claude Desktop, ...) like any MCP server::

    "headwaters-probe": {"command": "<python>", "args": ["<repo>/scripts/host_probe.py"]}

It answers ``initialize``, ``tools/list`` and one tool, ``probe``, and appends what it sees to a
JSON Lines file (``HEADWATERS_PROBE_LOG``, default ``host_probe.jsonl`` in the temp folder): its
working folder, the host's name, version and capabilities, whether the host sent roots, the parent
process chain and the Windows Job Object limits the host put it in. ``probe`` with
``spawn_sleeper: true`` starts a detached sleeper the way ``headwaters.core.daemon_control`` starts
the daemon; after the host is closed, ``--check LOG`` says whether it survived. No prompt or file
text is read or logged, and no secret is logged: environment variables are listed by name only.

``--check LOG`` prints one line per sleeper: ``alive`` or ``dead``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, TextIO

__all__ = [
    "BREAKAWAY_OK",
    "ENV_LOG",
    "KILL_ON_JOB_CLOSE",
    "SILENT_BREAKAWAY_OK",
    "ProbeState",
    "handle",
    "job_info",
    "main",
    "process_alive",
    "serve",
    "spawn_sleeper",
]

ENV_LOG = "HEADWATERS_PROBE_LOG"
BREAKAWAY_OK = 0x0800  # JOB_OBJECT_LIMIT_BREAKAWAY_OK
SILENT_BREAKAWAY_OK = 0x1000  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
KILL_ON_JOB_CLOSE = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
_JOB_EXTENDED_LIMIT_CLASS = 9
_STILL_ACTIVE = 259
_ENV_PREFIXES = ("VSCODE", "CURSOR", "CLAUDE", "ELECTRON", "MCP", "TERM_PROGRAM")
SLEEPER_CODE = "import time; time.sleep(7200)"
TOOL = {
    "name": "probe",
    "description": "Report how the host started this server. Optional: spawn_sleeper.",
    "inputSchema": {
        "type": "object",
        "properties": {"spawn_sleeper": {"type": "boolean"}},
    },
}


def log_path() -> Path:
    return Path(os.environ.get(ENV_LOG) or Path(tempfile.gettempdir()) / "host_probe.jsonl")


def record(event: str, **data: Any) -> None:
    line = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "pid": os.getpid(), "event": event, **data}
    with log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class BasicLimit(ctypes.Structure):
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

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in "abcdef"]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimit),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


def job_info() -> dict[str, Any]:
    """The Job Object this process is in (Windows): in a job, its limit flags and what they mean."""
    if sys.platform != "win32":
        return {"supported": False}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    inside = wintypes.BOOL()
    kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(inside))
    if not inside.value:
        return {"supported": True, "in_job": False}
    limits = ExtendedLimit()
    ok = kernel.QueryInformationJobObject(
        None, _JOB_EXTENDED_LIMIT_CLASS, ctypes.byref(limits), ctypes.sizeof(limits), None
    )
    if not ok:
        return {"supported": True, "in_job": True, "error": ctypes.get_last_error()}
    flags = int(limits.BasicLimitInformation.LimitFlags)
    return {
        "supported": True,
        "in_job": True,
        "limit_flags": hex(flags),
        "kill_on_close": bool(flags & KILL_ON_JOB_CLOSE),
        "breakaway_ok": bool(flags & BREAKAWAY_OK),
        "silent_breakaway_ok": bool(flags & SILENT_BREAKAWAY_OK),
    }


def process_alive(pid: int) -> bool:
    """True while ``pid`` is a running process (never signals it)."""
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle_ = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle_:
            return False
        try:
            code = wintypes.DWORD()
            got = kernel.GetExitCodeProcess(handle_, ctypes.byref(code))
            return bool(got) and code.value == _STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle_)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def parent_chain(depth: int = 5) -> list[str]:
    """Names of the parent processes (needs psutil; the project's venv has it)."""
    try:
        import psutil

        names: list[str] = []
        proc: Any = psutil.Process().parent()
        while proc is not None and len(names) < depth:
            names.append(proc.name())
            proc = proc.parent()
        return names
    except Exception:
        return []


def spawn_sleeper() -> dict[str, Any]:
    """Start a detached sleeper the way ``daemon_control._spawn_detached`` starts the daemon."""
    argv = [sys.executable, "-c", SLEEPER_CODE]
    quiet: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform != "win32":
        child = subprocess.Popen(argv, start_new_session=True, **quiet)
        return {"pid": child.pid, "mode": "new_session"}
    base = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    for mode, extra in (("breakaway", 0x01000000), ("no_breakaway", 0)):
        try:
            child = subprocess.Popen(argv, creationflags=base | extra, **quiet)
        except OSError as exc:
            if not extra:
                return {"pid": None, "mode": "failed", "error": str(exc)}
            continue
        return {"pid": child.pid, "mode": mode}
    return {"pid": None, "mode": "failed"}  # pragma: no cover


class ProbeState:
    def __init__(self) -> None:
        self.client: dict[str, Any] = {}
        self.roots_requested = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "cwd": os.getcwd(),
            "argv": sys.argv,
            "python": sys.executable,
            "ppid": os.getppid(),
            "parents": parent_chain(),
            "job": job_info(),
            "env_names": sorted(k for k in os.environ if k.upper().startswith(_ENV_PREFIXES)),
            "client": self.client,
        }


def _reply(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _text(request_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    body = {"content": [{"type": "text", "text": json.dumps(payload, indent=1)}]}
    return _reply(request_id, body)


def handle(message: dict[str, Any], state: ProbeState) -> list[dict[str, Any]]:
    """Messages to send back for one incoming JSON-RPC message."""
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if method is None:  # a reply to a request we sent (roots/list)
        if request_id == "probe-roots":
            record("roots_reply", result=message.get("result"), error=message.get("error"))
        return []
    if method == "initialize":
        info = params.get("clientInfo") or {}
        caps = params.get("capabilities") or {}
        state.client = {
            "name": info.get("name"),
            "version": info.get("version"),
            "protocol": params.get("protocolVersion"),
            "capabilities": sorted(caps),
            "roots": "roots" in caps,
            "sampling": "sampling" in caps,
            "elicitation": "elicitation" in caps,
        }
        record("initialize", **state.snapshot())
        server = {"name": "headwaters-probe", "version": "0"}
        result = {
            "protocolVersion": params.get("protocolVersion") or "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": server,
        }
        return [_reply(request_id, result)]
    if method == "notifications/initialized":
        if state.client.get("roots") and not state.roots_requested:
            state.roots_requested = True
            return [{"jsonrpc": "2.0", "id": "probe-roots", "method": "roots/list"}]
        return []
    if method == "tools/list":
        return [_reply(request_id, {"tools": [TOOL]})]
    if method == "tools/call":
        payload = state.snapshot()
        if (params.get("arguments") or {}).get("spawn_sleeper"):
            payload["sleeper"] = spawn_sleeper()
            record("sleeper", **payload["sleeper"])
        record("call", cwd=payload["cwd"])
        return [_text(request_id, payload)]
    if method == "ping":
        return [_reply(request_id, {})]
    if request_id is None:  # an unknown notification
        return []
    error = {"code": -32601, "message": f"method not found: {method}"}
    return [{"jsonrpc": "2.0", "id": request_id, "error": error}]


def serve(stdin: Iterable[str], stdout: TextIO) -> None:
    state = ProbeState()
    record("start", cwd=os.getcwd(), job=job_info(), parents=parent_chain())
    for raw in stdin:
        text = raw.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            continue
        for out in handle(message, state):
            stdout.write(json.dumps(out) + "\n")
            stdout.flush()
    record("stdin_closed")


def check(log: Path) -> list[str]:
    """One line per sleeper recorded in ``log``: pid, start mode, alive or dead."""
    lines: list[str] = []
    for raw in log.read_text(encoding="utf-8").splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            event = json.loads(raw)
            if event.get("event") == "sleeper" and event.get("pid"):
                alive = "alive" if process_alive(int(event["pid"])) else "dead"
                lines.append(f"sleeper pid={event['pid']} mode={event.get('mode')} {alive}")
    return lines or ["no sleeper recorded"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", metavar="LOG", help="report on the sleepers recorded in LOG")
    args = parser.parse_args(argv)
    if args.check:
        print("\n".join(check(Path(args.check))))
        return 0
    with contextlib.suppress(Exception):
        sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    serve(sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
