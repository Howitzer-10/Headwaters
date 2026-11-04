"""Where a Windows install of Headwaters puts its files (HDW-405).

One layout serves three callers: the Inno Setup installer, the itch payload and the uninstaller.
The install root *is* the per-user state dir (``%LOCALAPPDATA%\\Headwaters``), so the private
runtime sits next to the SQLite state it serves and an uninstaller never has to guess where the
program went. Nothing here writes: an ``Install`` is data, and a test points it at ``tmp_path``.

Two install styles exist and they update differently:

* the Inno installer (``HeadwatersSetup-<version>.exe``), replaced by running a newer setup;
* an itch app install, which the itch app owns. Its self-update path must not fight the itch
  client, so the payload ships a ``.itch.toml`` marker and :func:`update_mode` reports it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from headwaters.core import paths

APP_DIR_NAME = "Headwaters"
RUNTIME_DIR_NAME = "runtime"
VENV_DIR_NAME = ".venv"
SCRIPTS_DIR_NAME = "Scripts"
UV_EXE_NAME = "uv.exe"
LOCK_FILE_NAME = "uv.lock"
PROJECT_FILE_NAME = "pyproject.toml"
SOURCE_DIR_NAME = "headwaters"
CATALOG_DIR_NAME = "catalog"
ITCH_MANIFEST_NAME = ".itch.toml"

CLI_LAUNCHER = "headwaters"
MCP_LAUNCHER = "headwaters-mcp"
LAUNCHERS = (CLI_LAUNCHER, MCP_LAUNCHER)

#: The Windows console shims are always ``.exe``, whatever the machine running the tests is.
LAUNCHER_SUFFIX = ".exe"

MODE_ITCH = "itch"
MODE_INSTALLER = "installer"


@dataclass(frozen=True)
class Install:
    """The two per-user roots a Windows install owns.

    ``local`` is ``%LOCALAPPDATA%\\Headwaters``: program, runtime, database, cache and logs.
    ``roaming`` is ``%APPDATA%\\Headwaters``: rules, keys settings and the catalog overrides.
    """

    local: Path
    roaming: Path

    @classmethod
    def default(cls) -> Install:
        """The real per-user install, honouring ``HEADWATERS_HOME`` like the rest of the app."""
        return cls(paths.state_dir(), paths.config_dir())

    @classmethod
    def at(cls, root: Path | str, *, roaming: Path | str | None = None) -> Install:
        """An install rooted at ``root`` (tests pass ``tmp_path``); ``roaming`` defaults inside."""
        base = Path(root)
        return cls(local=base, roaming=Path(roaming) if roaming is not None else base / "Roaming")

    @property
    def runtime(self) -> Path:
        return self.local / RUNTIME_DIR_NAME

    @property
    def venv(self) -> Path:
        return self.runtime / VENV_DIR_NAME

    @property
    def uv(self) -> Path:
        return self.runtime / UV_EXE_NAME

    @property
    def lock(self) -> Path:
        return self.runtime / LOCK_FILE_NAME

    @property
    def project(self) -> Path:
        return self.runtime / PROJECT_FILE_NAME

    @property
    def source(self) -> Path:
        return self.runtime / SOURCE_DIR_NAME

    @property
    def catalog(self) -> Path:
        return self.runtime / CATALOG_DIR_NAME

    @property
    def autostart_xml(self) -> Path:
        return self.runtime / "autostart.xml"

    @property
    def watchdog_xml(self) -> Path:
        return self.runtime / "watchdog.xml"

    @property
    def cache(self) -> Path:
        return self.local / "Cache"

    @property
    def logs(self) -> Path:
        return self.local / "Logs"

    def launcher(self, name: str) -> Path:
        """A console shim created by ``uv sync`` inside the private virtualenv."""
        if name not in LAUNCHERS:
            raise ValueError(f"unknown launcher {name!r}; expected one of {LAUNCHERS}")
        return self.venv / SCRIPTS_DIR_NAME / f"{name}{LAUNCHER_SUFFIX}"

    @property
    def cli(self) -> Path:
        return self.launcher(CLI_LAUNCHER)

    @property
    def mcp(self) -> Path:
        return self.launcher(MCP_LAUNCHER)

    @property
    def python(self) -> Path:
        return self.venv / SCRIPTS_DIR_NAME / "python.exe"

    def roots(self) -> tuple[Path, Path]:
        """The only two trees an uninstaller may ever delete from."""
        return (self.local, self.roaming)


def itch_manifest(install_dir: Path | str) -> Path:
    return Path(install_dir) / ITCH_MANIFEST_NAME


def is_itch_managed(install_dir: Path | str | None) -> bool:
    """True when ``install_dir`` is an itch payload (the marker is only pushed through itch)."""
    if install_dir is None:
        return False
    return itch_manifest(install_dir).is_file()


@dataclass(frozen=True)
class UpdateMode:
    """How this copy of Headwaters is meant to be updated."""

    kind: str
    detail: str


def update_mode(install_dir: Path | str | None) -> UpdateMode:
    """Pick the update path for ``install_dir``: the itch client, or a new setup executable."""
    if is_itch_managed(install_dir):
        return UpdateMode(
            MODE_ITCH,
            "installed by the itch app: update from the itch app (channel windows-beta). "
            "Do not run HeadwatersSetup over it.",
        )
    return UpdateMode(
        MODE_INSTALLER,
        "installed by HeadwatersSetup: run a newer HeadwatersSetup-<version>.exe. "
        "It replaces the runtime in place and keeps your state.",
    )


def installed_root(executable: Path | str | None = None) -> Path | None:
    """The install root a running interpreter belongs to, or ``None`` for a source checkout.

    Both install styles run ``<root>/runtime/.venv/Scripts/python.exe``; the itch payload simply
    puts the same ``runtime`` folder next to its ``.itch.toml``.
    """
    exe = Path(sys.executable) if executable is None else Path(executable)
    parts = exe.parts
    # Case-insensitive: Windows paths are, and a probe may pass either spelling.
    if len(parts) < 5:
        return None
    if parts[-3].lower() != VENV_DIR_NAME.lower() or parts[-2].lower() != SCRIPTS_DIR_NAME.lower():
        return None
    if parts[-4].lower() != RUNTIME_DIR_NAME.lower():
        return None
    return Path(*parts[:-4])


def is_inside(path: Path | str, root: Path | str) -> bool:
    """True when ``path`` is ``root`` or below it, after resolving both (no ``..`` escapes)."""
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True
