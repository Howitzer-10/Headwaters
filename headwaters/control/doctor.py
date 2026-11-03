"""``headwaters doctor``: one pass over everything that can make the first run fail (HDW-402).

Each check answers with a status (``ok`` / ``warn`` / ``fail`` / ``skip``), one line of detail and,
when the user can act, a fix. The fix list at the end is what the user and support read; nothing
here changes a setting on its own. Prompt text, model output and keys never reach the report, and
``headwaters.control.bundle`` scrubs what it writes.

D14: the daemon is read only through ``headwaters.core.daemon_client`` / ``daemon_info``. This
module imports neither ``headwaters.daemon``, the MCP SDK nor the web framework, so ``--host`` runs
its live checks in a child process (``headwaters.mcp.probe``) and reads the JSON it prints.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import headwaters
from headwaters.control import keys as key_checks
from headwaters.core import netports, paths
from headwaters.core.catalog import updater as catalog_updater
from headwaters.core.catalog.discovery import network_block
from headwaters.core.catalog.http import SourceError, UrllibHttp
from headwaters.core.catalog.updater import CatalogStatus
from headwaters.core.daemon_client import DaemonClient, DaemonError, find_running
from headwaters.core.daemon_info import DEFAULT_API_PORT, read_info, read_token, token_path
from headwaters.core.hardware.state import HardwareState
from headwaters.core.hardware.sysmem import SYSMEM_HINT
from headwaters.core.keys import KeyStore
from headwaters.core.mcp_launch import launch_options
from headwaters.core.rules import Rules
from headwaters.installer import verify as host_verify
from headwaters.installer.context import default_context

__all__ = [
    "HOSTS",
    "MIN_OLLAMA_VERSION",
    "OLLAMA_ENV_VARS",
    "OLLAMA_PRIVATE_HINT",
    "TOKEN_ACL_NOTE",
    "Check",
    "Doctor",
    "Report",
    "host_probe",
    "read_ollama_env",
    "read_ollama_version",
    "run_doctor",
]

# status values
OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"
# KeyStore.entries() reports this source for an alias that has no key anywhere.
NOT_SET = "not set"

# The host ids HDW-703 installs. ``--host`` names one of these, or ``all``.
HOSTS = (
    "vscode",
    "copilot",
    "cline",
    "kilo",
    "roo",
    "continue",
    "opencode",
    "claude-code",
    "cursor",
    "trae",
    "windsurf",
    "claude-desktop",
    "all",
)

# A-OLL-6: no floor is documented. 0.40 is the release the catalog and the /api/ps fields the
# router reads were probed against (A-OLL-9), so an older build warns instead of failing.
MIN_OLLAMA_VERSION = (0, 40, 0)
CATALOG_STALE_S = 7 * 24 * 3600.0
PROBE_TIMEOUT_S = 90.0
OLLAMA_TIMEOUT_S = 5.0

TOKEN_ACL_NOTE = (
    "On Windows the token relies on the ACL of your per-user profile folder: daemon.token has no "
    "explicit ACL of its own, so another process of the same user can read it."
)
OLLAMA_PRIVATE_HINT = (
    'Set OLLAMA_NO_CLOUD=1 (or "disable_ollama_cloud": true in ~/.ollama/server.json) and '
    "restart Ollama; otherwise a cloud model stays reachable while Private Mode is on."
)
HOST_FIX = (
    "Run `headwaters mcp-path` and put that command in the host's MCP config, restart the host so "
    "it relaunches the server, then run `headwaters doctor --host <host>` again."
)
# FU-402-2: `mcp-path` resolving says nothing about the file a host actually reads.
HOST_CONFIG_FIX = (
    "Run `headwaters install --host <id>`: it shows the diff of the host's own config, asks first "
    "and writes the entry again."
)

OLLAMA_ENV_VARS = (
    "OLLAMA_FLASH_ATTENTION",
    "OLLAMA_KV_CACHE_TYPE",
    "OLLAMA_MAX_LOADED_MODELS",
    "OLLAMA_NO_CLOUD",
)
_REGISTRY_KEYS = (
    ("HKCU", "Environment"),
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
)


@dataclass(frozen=True)
class Check:
    """One check: what was found, and what the user can do about it."""

    id: str
    title: str
    status: str
    detail: str = ""
    fix: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass(frozen=True)
class Report:
    """The whole run. ``fixes`` is the ordered, de-duplicated list the user acts on."""

    checks: tuple[Check, ...]
    host: str | None = None
    generated_at: float = field(default_factory=time.time)

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status == FAIL)

    @property
    def warnings(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status == WARN)

    @property
    def fixes(self) -> tuple[tuple[str, str], ...]:
        seen: dict[str, str] = {}
        for check in self.checks:
            if check.fix and check.fix not in seen:
                seen[check.fix] = check.title
        return tuple((title, fix) for fix, title in seen.items())

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.generated_at)),
            "headwaters": headwaters.__version__,
            "host": self.host,
            "checks": [
                {
                    "id": check.id,
                    "title": check.title,
                    "status": check.status,
                    "detail": check.detail,
                    "fix": check.fix,
                    "data": check.data,
                }
                for check in self.checks
            ],
            "fixes": [{"check": title, "fix": fix} for title, fix in self.fixes],
        }


def render_text(report: Report) -> str:
    """The human report and the ``report.txt`` member of a bundle (plain text, no Rich)."""
    lines = [f"Headwaters doctor {headwaters.__version__}"]
    for check in report.checks:
        lines.append(f"[{check.status:4}] {check.title}: {check.detail}")
    if report.fixes:
        lines.append("")
        lines.append("Fix list:")
        for title, fix in report.fixes:
            lines.append(f"- {title}: {fix}")
    return "\n".join(lines) + "\n"


def _read_registry() -> dict[str, str]:
    """Environment values from the user and machine registry keys (nothing else)."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows always has winreg
        return {}
    values: dict[str, str] = {}
    for hive, sub in _REGISTRY_KEYS:
        root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        try:
            with winreg.OpenKey(root, sub) as key:  # type: ignore[arg-type]
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    index += 1
                    if isinstance(value, str):
                        values[name] = value
        except OSError:
            continue
    return values


def read_ollama_env(
    *,
    source: Mapping[str, str] | None = None,
    platform: str | None = None,
    registry: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """``OLLAMA_*`` as Ollama itself sees them: the registry system/user values, then the process.

    Ollama reads them from the user environment at logon, so a value set with ``setx`` is invisible
    to it until the tray app restarts - which is why the fix names the restart.
    """
    values = dict(
        registry
        if registry is not None
        else (_read_registry() if (platform or sys.platform) == "win32" else {})
    )
    values.update(source if source is not None else os.environ)
    return {name: values.get(name, "") for name in OLLAMA_ENV_VARS}


def read_ollama_version(host: str, http: Any = None) -> tuple[str | None, str]:
    """``GET {host}/api/version``; returns ``(version, error)`` without raising."""
    client = http if http is not None else UrllibHttp(timeout=OLLAMA_TIMEOUT_S)
    try:
        reply = client.request_json("GET", f"{host.rstrip('/')}/api/version")
    except SourceError as exc:
        return None, str(exc)
    except Exception as exc:  # a broken probe must not stop the report
        return None, type(exc).__name__
    version = reply.get("version") if isinstance(reply, Mapping) else None
    if not isinstance(version, str) or not version.strip():
        return None, "the reply has no version"
    return version.strip(), ""


def parse_version(text: str) -> tuple[int, ...] | None:
    """``0.40.13`` -> ``(0, 40, 13)``. A pre-release suffix is ignored; ``None`` when unparsable."""
    match = re.match(r"^v?(\d+(?:\.\d+)*)", text.strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _git_config(key: str) -> str | None:
    """``git config --get <key>``; ``None`` when git is missing or the key is unset."""
    try:
        done = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


def _port_state(port: int, excluded: Sequence[netports.PortRange]) -> str:
    """``free``, ``excluded`` (a Windows reserved range) or ``busy`` (something holds it)."""
    if netports.is_excluded(port, excluded):
        return "excluded"
    try:
        with closing(netports.bind_loopback(port)):
            return "free"
    except (netports.PortError, OSError):
        return "busy"


def host_probe(host: str, *, timeout_s: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    """Run ``headwaters.mcp.probe`` in a child process and read the JSON it prints.

    A child process keeps the MCP SDK out of this process (D14); the probe reports what a host of
    that kind sees: the tool list, one read-only tool call, and the effective scope.
    """
    argv = [sys.executable, "-m", "headwaters.mcp.probe", "--host", host]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"the host probe could not run ({type(exc).__name__})"}
    text = (done.stdout or "").strip()
    payload: Any = None
    if text:
        try:
            payload = json.loads(text.splitlines()[-1])
        except ValueError:
            payload = None
    if not isinstance(payload, dict):
        tail = (done.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return {
            "ok": False,
            "error": f"the host probe printed no result (exit {done.returncode}): {tail[0]}",
        }
    return payload


# ---------------------------------------------------------------------------- the checks

Finder = Callable[[], DaemonClient | None]
PortState = Callable[[int], str]
GitConfig = Callable[[str], str | None]
Prober = Callable[[str], Mapping[str, Any]]
Pinger = Callable[[KeyStore, Rules], list[key_checks.PingResult]]


@dataclass
class Doctor:
    """Runs the checks. Every probe is injectable, so a test needs no daemon, GPU or network."""

    rules: Rules
    host: str | None = None
    hardware: HardwareState | None = None
    env: Mapping[str, str] | None = None
    find: Finder = find_running
    http: Any = None
    store: KeyStore | None = None
    ping: Pinger | None = None
    catalog_status: CatalogStatus | None = None
    excluded: Sequence[netports.PortRange] | None = None
    port_state: PortState | None = None
    git_config: GitConfig | None = None
    probe: Prober | None = None
    platform: str | None = None
    now: float | None = None
    _client: DaemonClient | None = field(default=None, init=False, repr=False)
    _found: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------ helpers

    def _client_for(self) -> DaemonClient | None:
        """The running daemon, looked up once per report (lazily, so a check can be run alone)."""
        if not self._found:
            self._found = True
            try:
                self._client = self.find()
            except DaemonError:  # a daemon.json that cannot be read is "not running"
                self._client = None
        return self._client

    def _platform(self) -> str:
        return self.platform or sys.platform

    def _clock(self) -> float:
        return time.time() if self.now is None else self.now

    def _env(self) -> dict[str, str]:
        if self.env is not None:
            return {name: self.env.get(name, "") for name in OLLAMA_ENV_VARS}
        return read_ollama_env(platform=self._platform())

    def _port_states(self) -> dict[str, tuple[int, str]]:
        excluded = (
            list(self.excluded)
            if self.excluded is not None
            else netports.read_excluded_ranges(platform=self._platform())
        )
        probe = self.port_state

        def state(port: int) -> str:
            # A reserved range wins over the probe: Windows will refuse the bind either way.
            if netports.is_excluded(port, excluded):
                return "excluded"
            return _port_state(port, ()) if probe is None else probe(port)

        wanted = {
            "api": DEFAULT_API_PORT,
            "gateway": self.rules.gateway.port,
            "dashboard": self.rules.dashboard.port,
        }
        return {name: (port, state(port)) for name, port in wanted.items()}

    # ------------------------------------------------------------------ checks

    def _environment(self) -> Check:
        detail = (
            f"Headwaters {headwaters.__version__}, Python {sys.version.split()[0]}, "
            f"{self._platform()}"
        )
        return Check(
            "environment",
            "Environment",
            OK,
            detail,
            data={
                "headwaters": headwaters.__version__,
                "python": sys.version.split()[0],
                "platform": self._platform(),
                "state_dir": str(paths.state_dir()),
            },
        )

    def _ollama(self) -> Check:
        host = self.rules.tiers.local.host.rstrip("/")
        version, error = read_ollama_version(host, self.http)
        if version is None:
            return Check(
                "ollama",
                "Ollama",
                WARN,
                f"no answer from {host}: {error}",
                fix=(
                    "Install Ollama and start it, or set tiers.local.host in rules.yaml to the "
                    "port it listens on."
                ),
            )
        parsed = parse_version(version)
        floor = ".".join(str(part) for part in MIN_OLLAMA_VERSION)
        if parsed is not None and parsed < MIN_OLLAMA_VERSION:
            return Check(
                "ollama",
                "Ollama",
                WARN,
                f"{version} at {host} is older than {floor}; the /api/ps fields the router reads "
                "(size_vram, context_length) may be missing.",
                fix="Update Ollama, then run `headwaters doctor` again.",
                data={"version": version},
            )
        return Check("ollama", "Ollama", OK, f"{version} at {host}", data={"version": version})

    def _ollama_env(self) -> Check:
        values = self._env()
        wanted_kv = self.rules.tiers.local.kv_cache_type
        problems: list[str] = []
        if values["OLLAMA_FLASH_ATTENTION"] != "1":
            problems.append("OLLAMA_FLASH_ATTENTION is not 1")
        if values["OLLAMA_KV_CACHE_TYPE"] != wanted_kv:
            problems.append(f"OLLAMA_KV_CACHE_TYPE is not {wanted_kv}")
        loaded = values["OLLAMA_MAX_LOADED_MODELS"]
        if loaded and loaded != "1":
            problems.append("OLLAMA_MAX_LOADED_MODELS is more than 1")
        shown = ", ".join(
            f"{name}={values[name] or '(unset)'}"
            for name in OLLAMA_ENV_VARS
            if name != "OLLAMA_NO_CLOUD"
        )
        if not problems:
            return Check("ollama_env", "Ollama environment", OK, shown, data=dict(values))
        fix = (
            "Set them for your user (setx OLLAMA_FLASH_ATTENTION 1, setx OLLAMA_KV_CACHE_TYPE "
            f"{wanted_kv}, setx OLLAMA_MAX_LOADED_MODELS 1), then restart the Ollama tray app so "
            "it reads them."
        )
        return Check(
            "ollama_env",
            "Ollama environment",
            WARN,
            f"{shown} -- {'; '.join(problems)}",
            fix=fix,
            data=dict(values),
        )

    def _server_json_blocks_cloud(self) -> bool:
        path = Path.home() / ".ollama" / "server.json"
        try:
            data = json.loads(paths.long_safe(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and data.get("disable_ollama_cloud") is True

    def _private_mode(self) -> Check:
        rules = self.rules
        if not (rules.private_lock or rules.airplane or rules.default_scope == "local"):
            return Check("private_mode", "Private Mode", SKIP, f"off (scope {rules.default_scope})")
        if self._env().get("OLLAMA_NO_CLOUD", "").strip() == "1":
            return Check(
                "private_mode", "Private Mode", OK, "OLLAMA_NO_CLOUD=1: Ollama refuses cloud models"
            )
        if self._server_json_blocks_cloud():
            return Check(
                "private_mode",
                "Private Mode",
                OK,
                '~/.ollama/server.json sets "disable_ollama_cloud": true',
            )
        return Check(
            "private_mode",
            "Private Mode",
            WARN,
            "Ollama can still run a cloud model, which would send your prompt off this PC.",
            fix=OLLAMA_PRIVATE_HINT,
        )

    def _gpu(self) -> Check:
        state = self.hardware
        if state is None:
            return Check("gpu", "GPU", SKIP, "the GPU probe is unavailable on this machine")
        if state.gpu is None:
            return Check(
                "gpu",
                "GPU",
                WARN,
                "no NVIDIA GPU was found, so local models stay off",
                fix=(
                    "Local models need an NVIDIA GPU; without one Headwaters routes to cloud where "
                    "it can. Install the driver, then run `headwaters doctor` again."
                ),
            )
        gpu = state.gpu
        detail = (
            f"{gpu.name} ({gpu.total_mb / 1024:.1f} GB), profile {state.profile}, reserve "
            f"{state.reserve_gb:.1f} GB ({state.reserve_source}), budget {state.budget_gb:.1f} GB"
        )
        data = {
            "gpu": gpu.name,
            "total_gb": round(gpu.total_mb / 1024, 1),
            "profile": state.profile,
            "reserve_gb": state.reserve_gb,
            "reserve_source": state.reserve_source,
            "budget_gb": round(state.budget_gb, 1),
            "gpu_busy": state.gpu_busy.busy,
            "local_suggestion": state.local_suggestion,
            "notes": list(state.notes),
        }
        if any("sysmem" in note.lower() for note in state.notes):
            return Check(
                "gpu",
                "GPU",
                WARN,
                f"{detail}; a sysmem fallback was seen",
                fix=SYSMEM_HINT,
                data=data,
            )
        if state.gpu_busy.busy:
            why = state.gpu_busy.reason or state.gpu_busy.top_process or "another process"
            return Check(
                "gpu",
                "GPU",
                WARN,
                f"{detail}; {why} holds the GPU now",
                fix="Close the other GPU app, or let Headwaters route this request to cloud.",
                data=data,
            )
        if state.local_suggestion != "ok":
            return Check(
                "gpu",
                "GPU",
                WARN,
                f"{detail}; local quality is {state.local_suggestion}",
                data=data,
            )
        return Check("gpu", "GPU", OK, detail, data=data)

    def _ports(self) -> Check:
        states = self._port_states()
        detail = ", ".join(f"{name} {port} ({state})" for name, (port, state) in states.items())
        data = {name: {"port": port, "state": state} for name, (port, state) in states.items()}
        if any(state == "excluded" for _port, state in states.values()):
            command = " ".join(netports.EXCLUDED_RANGE_COMMAND)
            return Check(
                "ports",
                "Ports",
                WARN,
                detail,
                fix=(
                    "Windows reserves these ranges (Hyper-V or WSL): the daemon skips them and "
                    f"takes the next free port. Inspect them with: {command}."
                ),
                data=data,
            )
        held = set(self._daemon_ports())
        problems = [
            f"{name} {port} is {state}"
            for name, (port, state) in states.items()
            if state != "free" and port not in held
        ]
        if problems:
            return Check(
                "ports",
                "Ports",
                WARN,
                f"{detail} -- {'; '.join(problems)}",
                fix=(
                    "Free the port, or let the daemon take the next one: a preferred port is only "
                    "a preference."
                ),
                data=data,
            )
        return Check("ports", "Ports", OK, detail, data=data)

    def _daemon_ports(self) -> set[int]:
        client = self._client_for()
        if client is None:
            return set()
        ports = client.info.ports
        return {port for port in (ports.api, ports.gateway, ports.dashboard) if port}

    def _daemon(self) -> Check:
        client = self._client_for()
        if client is None:
            stale = read_info()
            if stale is not None:
                return Check(
                    "daemon",
                    "Daemon",
                    WARN,
                    f"not answering, but daemon.json still names pid {stale.pid}",
                    fix="headwaters stop --force",
                )
            return Check(
                "daemon",
                "Daemon",
                WARN,
                "not running (it starts on demand)",
                fix="headwaters start",
            )
        try:
            status = client.status()
        except DaemonError as exc:
            return Check(
                "daemon",
                "Daemon",
                WARN,
                f"pid {client.info.pid} does not answer: {exc}",
                fix="headwaters stop --force",
            )
        info = client.info
        uptime = status.get("uptime_s")
        detail = f"pid {info.pid}, version {info.version}, API {info.api_url}, MCP {info.mcp_url}"
        if isinstance(uptime, int | float):
            detail += f", up {uptime:.0f} s"
        return Check(
            "daemon",
            "Daemon",
            OK,
            detail,
            data={
                "pid": info.pid,
                "version": info.version,
                "ports": {"api": info.ports.api, "gateway": info.ports.gateway},
            },
        )

    def _token(self) -> Check:
        token = read_token()
        if not token:
            return Check(
                "daemon_token",
                "Daemon token",
                WARN,
                "no daemon.token yet; it is written on the first start",
                fix="headwaters start",
            )
        detail = "daemon.token is present (the gateway and the MCP endpoint require it)"
        data: dict[str, Any] = {"path": str(token_path())}
        if self._platform() == "win32":
            detail = f"{detail}. {TOKEN_ACL_NOTE}"
            data["windows_acl"] = "per-user profile folder only"
        return Check("daemon_token", "Daemon token", OK, detail, data=data)

    def _pinger(self) -> Pinger:
        if self.ping is not None:
            return self.ping
        http = self.http
        return lambda store, rules: key_checks.check_keys(store, rules, http=http)

    def _keys(self) -> Check:
        store = self.store or KeyStore(self.rules)
        try:
            entries = [row for row in store.entries() if row.source != NOT_SET]
        except Exception as exc:  # a broken backend must not stop the report
            return Check(
                "keys",
                "API keys",
                WARN,
                f"the credential store cannot be read ({type(exc).__name__})",
                fix="headwaters keys list",
            )
        if not entries:
            return Check("keys", "API keys", SKIP, "no keys saved; local models only")
        results = self._pinger()(store, self.rules)
        if results and all(result.status == key_checks.BLOCKED for result in results):
            return Check(
                "keys",
                "API keys",
                SKIP,
                f"{len(entries)} saved, none checked ({network_block(self.rules)})",
            )
        summary = ", ".join(f"{result.alias} {result.status}" for result in results)
        data = {"keys": [{"alias": result.alias, "status": result.status} for result in results]}
        bad = [r for r in results if r.status in (key_checks.UNAUTHORIZED, key_checks.HTTP_ERROR)]
        if bad:
            return Check(
                "keys",
                "API keys",
                WARN,
                summary,
                fix="headwaters keys test, then headwaters keys set <alias> for a rejected key.",
                data=data,
            )
        unreachable = [r for r in results if r.status == key_checks.UNREACHABLE]
        if unreachable:
            return Check(
                "keys",
                "API keys",
                WARN,
                summary,
                fix="A provider could not be reached; check the network or airplane mode.",
                data=data,
            )
        return Check("keys", "API keys", OK, summary, data=data)

    def _catalog(self) -> Check:
        status = self.catalog_status or catalog_updater.status(self.rules)
        data = {
            "source": status.source,
            "version": status.catalog_version,
            "fetched_at": status.fetched_at,
            "update_blocked": status.update_blocked,
        }
        if status.fetched_at is None:
            return Check(
                "catalog",
                "Catalog",
                OK,
                f"{status.catalog_version} from the {status.source} catalog",
                data=data,
            )
        age = max(0.0, self._clock() - status.fetched_at)
        detail = (
            f"{status.catalog_version} from the {status.source} catalog, fetched "
            f"{age / 86400:.1f} days ago"
        )
        if status.update_blocked:
            return Check(
                "catalog",
                "Catalog",
                OK,
                f"{detail}; updates off ({status.update_blocked})",
                data=data,
            )
        if age > CATALOG_STALE_S:
            return Check(
                "catalog",
                "Catalog",
                WARN,
                detail,
                fix="headwaters catalog update",
                data=data,
            )
        return Check("catalog", "Catalog", OK, detail, data=data)

    def _git(self) -> Check:
        read = self.git_config or _git_config
        value = read("core.autocrlf")
        if value is None:
            return Check("git", "Git line endings", OK, "core.autocrlf is unset")
        detail = f"core.autocrlf={value}"
        if value.lower() == "true":
            return Check(
                "git",
                "Git line endings",
                WARN,
                detail,
                fix=(
                    "core.autocrlf=true rewrites every text file to CRLF on checkout; a patch or "
                    "hash made here then differs from a Linux CI run. Set core.autocrlf=input."
                ),
            )
        return Check("git", "Git line endings", OK, detail)

    def _mcp_launch(self) -> Check:
        best = launch_options()[0]
        command = " ".join([best.command, *best.args]).strip()
        return Check(
            "mcp_launch",
            "MCP launcher",
            OK,
            f"hosts launch: {command}",
            data={"kind": best.kind, "command": best.command, "args": list(best.args)},
        )

    def _host_configs(self) -> Check:
        """FU-402-2: the host's own config, not just whether the MCP command resolves."""
        ctx = default_context(launch=launch_options()[0], require_launcher=False)
        found = host_verify.problems(ctx)
        if not found:
            return Check(
                "host_configs",
                "Host config files",
                OK,
                "no installed host config has a problem",
            )
        detail = "; ".join(f"{item.host}: {item.detail}" for item in found[:3])
        if len(found) > 3:
            detail += f"; and {len(found) - 3} more"
        return Check("host_configs", "Host config files", WARN, detail, fix=HOST_CONFIG_FIX)

    def _host_checks(self) -> list[Check]:
        assert self.host is not None
        probe = self.probe or host_probe
        payload = probe(self.host)
        rows = payload.get("checks") if isinstance(payload, Mapping) else None
        found: dict[str, Mapping[str, Any]] = {
            str(row.get("name")): row for row in (rows or []) if isinstance(row, Mapping)
        }
        error = payload.get("error") if isinstance(payload, Mapping) else None
        checks: list[Check] = []
        for name, title in (
            ("round_trip", "Host round trip"),
            ("tool_call", "Host tool call"),
            ("scope", "Host scope test"),
        ):
            row = found.get(name)
            if row is None:
                checks.append(
                    Check(
                        f"host_{name}",
                        title,
                        FAIL,
                        f"{self.host}: {error or 'the host probe printed no result'}",
                        fix=HOST_FIX,
                    )
                )
                continue
            ok = bool(row.get("ok"))
            detail = f"{self.host}: {row.get('detail') or ''}".strip()
            checks.append(
                Check(
                    f"host_{name}", title, OK if ok else FAIL, detail, fix=None if ok else HOST_FIX
                )
            )
        return checks

    def run(self) -> Report:
        self._client_for()
        checks = [
            self._environment(),
            self._ollama(),
            self._ollama_env(),
            self._private_mode(),
            self._gpu(),
            self._ports(),
            self._daemon(),
            self._token(),
            self._keys(),
            self._catalog(),
            self._git(),
            self._mcp_launch(),
            self._host_configs(),
        ]
        if self.host:
            checks.extend(self._host_checks())
        return Report(tuple(checks), host=self.host, generated_at=self._clock())


def run_doctor(rules: Rules, **kwargs: Any) -> Report:
    """``headwaters doctor``: one report over every check. ``kwargs`` go to ``Doctor``."""
    return Doctor(rules, **kwargs).run()
