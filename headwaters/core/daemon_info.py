"""What other components know about the running daemon (HDW-201).

The daemon writes ``daemon.json`` (pid and ports) into the state dir once it listens, and keeps a
per-user secret in ``daemon.token`` next to it. Shims, the CLI and later the gateway read these
two files instead of guessing ports. The state dir is per user (``%LOCALAPPDATA%\\Headwaters``).
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from headwaters.core import paths

__all__ = [
    "DEFAULT_API_PORT",
    "LOOPBACK_HOST",
    "MCP_PATH",
    "DaemonInfo",
    "DaemonPorts",
    "load_or_create_token",
    "lock_path",
    "read_info",
    "read_token",
    "remove_info",
    "state_file_path",
    "token_path",
    "write_info",
]

STATE_FILE = "daemon.json"
TOKEN_FILE = "daemon.token"
LOCK_FILE = "daemon.lock"
LOOPBACK_HOST = "127.0.0.1"
MCP_PATH = "/mcp"
# Internal API and streamable-HTTP MCP share one port; 4000/4001 are the gateway and dashboard.
DEFAULT_API_PORT = 4002
SCHEMA = 1
_REPLACE_TRIES = 5


@dataclass(frozen=True)
class DaemonPorts:
    """``gateway`` and ``dashboard`` are probed free ports, recorded for those servers to bind."""

    api: int
    gateway: int | None = None
    dashboard: int | None = None


@dataclass(frozen=True)
class DaemonInfo:
    pid: int
    started_at: float
    version: str
    ports: DaemonPorts
    host: str = LOOPBACK_HOST
    mcp_path: str = MCP_PATH

    @property
    def api_url(self) -> str:
        return f"http://{self.host}:{self.ports.api}"

    @property
    def mcp_url(self) -> str:
        return self.api_url + self.mcp_path

    def to_json(self) -> str:
        payload: dict[str, Any] = {"schema": SCHEMA, **asdict(self)}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _state_dir() -> Path:
    directory = paths.long_safe(paths.state_dir())
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def state_file_path() -> Path:
    return paths.state_dir() / STATE_FILE


def token_path() -> Path:
    return paths.state_dir() / TOKEN_FILE


def lock_path() -> Path:
    return paths.state_dir() / LOCK_FILE


def _opt_port(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    raise ValueError("bad port")


def _parse(raw: object) -> DaemonInfo:
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        raise ValueError("not a daemon state file")
    ports = raw.get("ports")
    if not isinstance(ports, dict):
        raise ValueError("no ports")
    api = _opt_port(ports.get("api"))
    pid = raw.get("pid")
    started = raw.get("started_at")
    version = raw.get("version")
    host = raw.get("host")
    mcp_path = raw.get("mcp_path")
    if (
        api is None
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(started, int | float)
        or not isinstance(version, str)
        or not isinstance(host, str)
        or not isinstance(mcp_path, str)
    ):
        raise ValueError("bad field")
    return DaemonInfo(
        pid=pid,
        started_at=float(started),
        version=version,
        ports=DaemonPorts(api, _opt_port(ports.get("gateway")), _opt_port(ports.get("dashboard"))),
        host=host,
        mcp_path=mcp_path,
    )


def _atomic_write(path: Path, text: str) -> None:
    target = paths.long_safe(path)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(temp, target)
            return
        except PermissionError:  # Windows: a reader has the target open for a moment
            if attempt == _REPLACE_TRIES - 1:
                raise
            time.sleep(0.02)


def write_info(info: DaemonInfo) -> Path:
    _state_dir()
    _atomic_write(state_file_path(), info.to_json())
    return state_file_path()


def read_info() -> DaemonInfo | None:
    """The state file, or ``None`` when it is missing or not valid (never raises)."""
    try:
        text = paths.long_safe(state_file_path()).read_text(encoding="utf-8")
        return _parse(json.loads(text))
    except (OSError, ValueError):
        return None


def remove_info(pid: int | None = None) -> bool:
    """Delete the state file. With ``pid`` only when it still names that process."""
    current = read_info()
    if pid is not None and (current is None or current.pid != pid):
        return False
    try:
        paths.long_safe(state_file_path()).unlink()
    except OSError:
        return False
    return True


def read_token() -> str | None:
    try:
        text = paths.long_safe(token_path()).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def load_or_create_token() -> str:
    """The per-user secret, created on first use (owner-only mode where the OS has modes)."""
    existing = read_token()
    if existing is not None:
        return existing
    _state_dir()
    token = secrets.token_urlsafe(32)
    target = paths.long_safe(token_path())
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process won the race, or an empty file was left behind.
        raced = read_token()
        if raced is not None:
            return raced
        with contextlib.suppress(OSError):
            target.unlink()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(token + "\n")
    return token
