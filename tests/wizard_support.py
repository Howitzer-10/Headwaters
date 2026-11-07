"""Shared fakes for the HDW-505 wizard tests.

Nothing here reaches the network, the Windows registry, ``schtasks``, the Windows Credential
Manager or the user's real ``%APPDATA%``: the environment writer is a dict, Ollama is three
methods, the task runner records its argv, the key store is a list of provider names, the pull is
a scripted iterator of ``PullLine``, and every filesystem root is under ``tmp_path``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.executor.ollama import PullLine
from headwaters.core.features import FeatureRegistry
from headwaters.core.mcp_launch import McpLaunch
from headwaters.core.paths import config_dir
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.installer.context import Context
from headwaters.packaging.layout import Install

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_PORT = 4002
CSRF = "csrf-token-for-the-wizard-tests"


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every path the wizard can reach, moved under ``tmp_path``.

    Kept as a fixture for a module that asks for it by name; ``tests/conftest.py`` also applies
    it to every ``test_wizard*`` module, because an autouse fixture imported into a module is not
    autouse there — and a test that forgot this one would edit this machine.
    """
    isolate_paths(monkeypatch, tmp_path)


def isolate_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Move every path the wizard can reach under ``tmp_path``.

    ``HEADWATERS_HOME`` moves the state database and ``rules.yaml``; ``HOME``/``USERPROFILE`` and
    ``APPDATA`` move the editor configs the installer plans against; ``XDG_CONFIG_HOME`` covers
    the non-Windows hosts.
    """
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    home.mkdir(exist_ok=True)
    appdata.mkdir(exist_ok=True)
    for name, value in (
        ("HOME", home),
        ("USERPROFILE", home),
        ("APPDATA", appdata),
        ("XDG_CONFIG_HOME", appdata),
        ("HEADWATERS_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    for name in ("CLINE_DIR", "CLINE_DATA_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    # Isolation is only worth having if the product agrees to it: fail loudly here rather than
    # letting a test reach this machine's real config.
    assert config_dir().is_relative_to(tmp_path), f"isolation did not apply: {config_dir()}"


class FakeWriter:
    """The user environment as a dict: ``supported`` like Windows, recording every write."""

    supported = True

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values or {})
        self.writes: list[tuple[str, str | None]] = []

    def read(self) -> Mapping[str, str]:
        return dict(self.values)

    def set(self, name: str, value: str | None) -> None:
        self.writes.append((name, value))
        if value is None:
            self.values.pop(name, None)
            return
        self.values[name] = value


class UnwritableWriter(FakeWriter):
    """A platform with no supported write, such as Linux: it reads and refuses."""

    supported = False


class FakeOllama:
    """The three calls the wizard makes on Ollama, scripted per test."""

    def __init__(
        self,
        *,
        version: str | None = "0.12.3",
        version_error: str = "",
        installed: Sequence[str] = ("qwen3:14b",),
        installed_error: str = "",
        lines: Sequence[PullLine] = (),
    ) -> None:
        self.version_text = version
        self.version_error = version_error
        self.installed_models = tuple(installed)
        self.installed_error = installed_error
        self.scripted = tuple(lines)
        self.version_hosts: list[str] = []
        self.installed_hosts: list[str] = []
        self.pulled: list[tuple[str, str, Any]] = []

    def version(self, host: str) -> tuple[str | None, str]:
        self.version_hosts.append(host)
        return self.version_text, self.version_error

    def installed(self, host: str) -> tuple[str, ...]:
        self.installed_hosts.append(host)
        if self.installed_error:
            raise RuntimeError(self.installed_error)
        return self.installed_models

    def lines(self, host: str, model: str, patterns: Any) -> Iterator[PullLine]:
        self.pulled.append((host, model, patterns))
        return iter(self.scripted)


@dataclass
class FakeRunner:
    """``schtasks`` as a recorder: every argv, and the exit code the caller scripts."""

    code: int = 0
    stdout: str = ""
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), self.code, self.stdout, "")


def installed_task(
    stdout: str = "<Task><Settings><Enabled>true</Enabled></Settings></Task>",
) -> str:
    """The stdout ``schtasks /Query /XML`` prints for a task that exists and is on."""
    return stdout


LOW_ENV = {"OLLAMA_FLASH_ATTENTION": "0", "OLLAMA_KV_CACHE_TYPE": "f16"}


def detected_machine() -> Any:
    """A detected machine: one 12 GiB card, a budget and a profile the catalog knows."""
    gpu = SimpleNamespace(name="NVIDIA GeForce RTX 3060", total_mb=12288)
    return SimpleNamespace(gpu=gpu, budget_gb=11.5, profile="laptop_12gb", notes=["measured"])


def hardware_only(machine: Any) -> Callable[[], Any]:
    """What ``Engine.hardware`` really is: a callable, so tests hand over one too."""
    return lambda: machine


def built(tmp_path: Path, **kw: Any) -> Harness:
    """``harness`` with this machine's detected card and the two tuning values already set."""
    values = kw.pop("values", LOW_ENV)
    hardware = kw.pop("hardware", hardware_only(detected_machine()))
    return harness(tmp_path, values=values, hardware=hardware, **kw)


class DeadTransport(httpx.BaseTransport):
    """A socket that refuses: what a pull looks like on a machine with no Ollama running."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)


def make_ctx(tmp_path: Path, *, port: int = 43123) -> Context:
    """An installer context rooted entirely at ``tmp_path``, so no real host config is planned."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return Context(
        project=project,
        home=tmp_path / "home",
        state=tmp_path / "state",
        launch=McpLaunch("script", str(tmp_path / "bin" / "headwaters-mcp")),
        port=port,
    )


def seed_catalog() -> Any:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


def open_db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "wizard.db")


@dataclass
class Harness:
    """One wizard, everything it can reach, and the objects a test wants to inspect."""

    registry: FeatureRegistry
    db: Database
    writer: FakeWriter
    ollama: FakeOllama
    runner: FakeRunner
    rules: Rules
    catalog: Any
    tmp_path: Path
    plugins: Mapping[str, object]

    def plugin(self, interface: str = "WizardPlugin") -> Any:
        return self.registry.plugin("wizard", interface)


def attach_report(registry: FeatureRegistry) -> None:
    """Register the real ★ usage report the way discovery would, for step 7.

    The wizard itself is registered by :func:`harness` with its injected fakes, so full discovery
    cannot be used here; the discovery walk is proved separately on a clean registry.
    """
    from headwaters.features import usage_report

    usage_report.register(registry)


def harness(
    tmp_path: Path,
    *,
    values: Mapping[str, str] | None = None,
    writer: FakeWriter | None = None,
    ollama: FakeOllama | None = None,
    runner: FakeRunner | None = None,
    keys: Sequence[str] = ("openrouter",),
    rules: Rules | None = None,
    catalog: Any = None,
    no_catalog: bool = False,
    hardware: Callable[[], Any] | None = None,
    probe: Callable[[], tuple[str, bool, str]] | None = None,
    pull_lines: Sequence[PullLine] | None = None,
    autostart_platform: str = "win32",
    flags: Mapping[str, bool] | None = None,
) -> Harness:
    """Build a fully faked wizard through the same ``register`` the ★ pack uses."""
    from headwaters.features import wizard as wizard_pack
    from headwaters.features.wizard import pull, steps

    db = open_db(tmp_path)
    live_rules = rules if rules is not None else Rules()
    fake_writer = writer if writer is not None else FakeWriter(values)
    fake_ollama = ollama if ollama is not None else FakeOllama()
    fake_runner = runner if runner is not None else FakeRunner()
    used_catalog = None if no_catalog else (catalog if catalog is not None else seed_catalog())
    registry = FeatureRegistry(flags, {"db": db, "rules": live_rules})
    if used_catalog is not None:
        registry.provide_services({"catalog": used_catalog})
    if hardware is not None:
        registry.provide_services({"hardware": hardware})
    registry.provide_services({"dashboard_port": DASHBOARD_PORT})
    manager = pull.PullManager(
        lines=fake_ollama.lines if pull_lines is None else _scripted(pull_lines)
    )
    gate = steps.HostGate(
        context=lambda: make_ctx(tmp_path), apply_context=lambda: make_ctx(tmp_path)
    )
    start = steps.AutostartGate(
        install=lambda: Install.at(tmp_path), runner=fake_runner, platform=autostart_platform
    )
    plugins = {
        "WizardPlugin": wizard_pack.Wizard(
            registry,
            ollama=fake_ollama,
            hosts=gate,
            autostart=start,
            writer=lambda: fake_writer,
            probe=probe or _loopback_ok,
            pull=manager,
            keys=lambda: list(keys),
        )
    }
    registry.register(wizard_pack.FEATURE_SPEC, plugins)
    return Harness(
        registry=registry,
        db=db,
        writer=fake_writer,
        ollama=fake_ollama,
        runner=fake_runner,
        rules=live_rules,
        catalog=used_catalog,
        tmp_path=tmp_path,
        plugins=plugins,
    )


def _scripted(lines: Sequence[PullLine]) -> Any:
    def lines_for(host: str, model: str, patterns: Any) -> Iterator[PullLine]:
        return iter(lines)

    return lines_for


def _loopback_ok() -> tuple[str, bool, str]:
    return f"http://127.0.0.1:{DASHBOARD_PORT}/", True, ""


def progress(completed: int, total: int, *, digest: str = "sha256:abc") -> PullLine:
    """One ``/api/pull`` line: a layer with a known size, so a percentage is knowable."""
    return PullLine("pulling manifest", digest, total, completed)


def written_files(root: Path) -> list[str]:
    """Every file the wizard wrote into the isolated tree, relative and sorted.

    ``state`` is left out on purpose: the database is created by the harness itself, not by a step.
    """
    found: list[str] = []
    for name in ("project", "home"):
        base = root / name
        if base.exists():
            found += [str(path.relative_to(root)) for path in base.rglob("*") if path.is_file()]
    return sorted(found)


def envfix_module() -> Any:
    from headwaters.features.wizard import envfix

    return envfix
