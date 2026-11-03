"""The install layout and the itch-vs-installer update decision (HDW-405).

Paths are pure data, so these tests never create a runtime, a task or a file under ``%APPDATA%``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core import paths
from headwaters.packaging.layout import (
    MODE_INSTALLER,
    MODE_ITCH,
    Install,
    installed_root,
    is_inside,
    is_itch_managed,
    update_mode,
)


def test_at_roots_every_path_under_the_given_root(tmp_path: Path) -> None:
    install = Install.at(tmp_path)

    assert install.local == tmp_path
    assert install.roaming == tmp_path / "Roaming"
    assert install.runtime == tmp_path / "runtime"
    assert install.venv == tmp_path / "runtime" / ".venv"
    assert install.uv == tmp_path / "runtime" / "uv.exe"
    assert install.lock == tmp_path / "runtime" / "uv.lock"
    assert install.project == tmp_path / "runtime" / "pyproject.toml"
    assert install.catalog == tmp_path / "runtime" / "catalog"
    assert install.autostart_xml == tmp_path / "runtime" / "autostart.xml"
    assert install.roots() == (tmp_path, tmp_path / "Roaming")


def test_roaming_root_can_be_given_separately(tmp_path: Path) -> None:
    install = Install.at(tmp_path / "local", roaming=tmp_path / "roaming")

    assert install.local == tmp_path / "local"
    assert install.roaming == tmp_path / "roaming"


def test_default_uses_the_core_state_and_config_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))

    install = Install.default()

    assert install.local == paths.state_dir()
    assert install.roaming == paths.config_dir()
    assert install.runtime == paths.state_dir() / "runtime"


def test_the_launcher_shims_live_in_the_private_venv(tmp_path: Path) -> None:
    install = Install.at(tmp_path)

    assert install.cli == tmp_path / "runtime" / ".venv" / "Scripts" / "headwaters.exe"
    assert install.mcp == tmp_path / "runtime" / ".venv" / "Scripts" / "headwaters-mcp.exe"
    assert install.python == tmp_path / "runtime" / ".venv" / "Scripts" / "python.exe"


def test_an_unknown_launcher_is_a_programming_error(tmp_path: Path) -> None:
    install = Install.at(tmp_path)

    with pytest.raises(ValueError, match="nope"):
        install.launcher("nope")


def test_installed_root_is_found_from_the_venv_interpreter(tmp_path: Path) -> None:
    root = tmp_path / "Headwaters"
    exe = root / "runtime" / ".venv" / "Scripts" / "python.exe"

    assert installed_root(exe) == root


def test_installed_root_is_none_for_a_source_checkout(tmp_path: Path) -> None:
    assert installed_root(tmp_path / "python.exe") is None
    assert installed_root(tmp_path / "runtime" / "python.exe") is None
    assert installed_root(tmp_path / "other" / ".venv" / "Scripts" / "python.exe") is None


def test_the_itch_marker_decides_between_the_two_update_paths(tmp_path: Path) -> None:
    assert not is_itch_managed(tmp_path)
    assert update_mode(tmp_path).kind == MODE_INSTALLER

    (tmp_path / ".itch.toml").write_text("", encoding="utf-8")

    assert is_itch_managed(tmp_path)
    mode = update_mode(tmp_path)
    assert mode.kind == MODE_ITCH
    assert "itch app" in mode.detail
    assert "Health" not in mode.detail  # the text is about updating, not about starting
    assert update_mode(None).kind == MODE_INSTALLER


def test_inside_accepts_the_root_itself_and_rejects_a_sibling(tmp_path: Path) -> None:
    root = tmp_path / "Headwaters"
    root.mkdir()

    assert is_inside(root, root)
    assert is_inside(root / "runtime", root)
    assert not is_inside(tmp_path, root)
    assert not is_inside(root / ".." / "elsewhere", root)
