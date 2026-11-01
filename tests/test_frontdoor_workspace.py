"""HDW-204 / FU-203-1: the tools map cwd, configured roots and host roots onto ``resolve_roots``."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.rules import Rules
from headwaters.mcp.meta import META_CWD, META_ROOTS
from headwaters.mcp.tools.context import ToolFailure
from headwaters.mcp.tools.workspace import open_workspace


def _proj(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    return proj


def test_no_workspace_means_no_files_and_never_the_daemons_own_directory() -> None:
    ws = open_workspace(None, Rules(), ())
    assert ws.roots is None
    assert ws.repo is None
    assert open_workspace({META_CWD: "   "}, Rules(), ()).roots is None


def test_the_shims_directory_is_the_boundary_when_nothing_is_configured(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    ws = open_workspace({META_CWD: str(proj)}, Rules(), ())
    assert ws.roots is not None
    assert ws.roots.roots == (proj.resolve(),)
    assert ws.repo == proj.resolve()


def test_a_configured_root_is_the_boundary_and_host_roots_only_narrow(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    ws = open_workspace({META_CWD: str(other)}, Rules(), [str(proj)])
    assert ws.roots is not None
    assert ws.roots.roots == (proj.resolve(),)  # the shim's directory does not widen it
    narrowed = open_workspace({META_ROOTS: [str(proj / "sub")]}, Rules(), [str(proj)])
    assert narrowed.roots is not None
    assert narrowed.roots.roots == ((proj / "sub").resolve(),)
    widened = open_workspace({META_ROOTS: [str(other)]}, Rules(), [str(proj)])
    assert widened.roots is not None
    assert widened.roots.roots == (proj.resolve(),)


def test_host_roots_are_ignored_when_the_rule_turns_them_off(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    rules = Rules.model_validate({"mcp": {"roots": "off"}})
    ws = open_workspace({META_CWD: str(proj), META_ROOTS: [str(proj / "sub")]}, rules, ())
    assert ws.roots is not None
    assert ws.roots.roots == (proj.resolve(),)


def test_a_bad_workspace_is_a_tool_failure_with_a_fix(tmp_path: Path) -> None:
    with pytest.raises(ToolFailure) as raised:
        open_workspace({META_CWD: str(tmp_path / "missing")}, Rules(), ())
    assert raised.value.code == "bad_workspace"
    assert raised.value.fix
