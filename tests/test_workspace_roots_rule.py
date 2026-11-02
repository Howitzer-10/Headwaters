"""HDW-147: ``workspace.roots`` in rules.yaml supplies the configured workspace boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from headwaters.core.project_config import ProjectConfig
from headwaters.core.rules import Rules
from headwaters.core.workspace_paths import WorkspaceError
from headwaters.daemon.service import Daemon
from headwaters.mcp.meta import META_CWD, META_ROOTS
from headwaters.mcp.tools.workspace import open_workspace


def test_schema_and_default_for_workspace_roots() -> None:
    schema = Rules.model_json_schema()
    assert "roots" in schema["$defs"]["WorkspaceRules"]["properties"]
    assert Rules().workspace.roots == []
    assert Rules.model_validate({"workspace": {"roots": ["/x"]}}).workspace.roots == ["/x"]
    with pytest.raises(ValidationError):
        Rules.model_validate({"workspace": {"folders": []}})


def test_a_project_config_cannot_set_roots() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({"workspace": {"roots": ["/x"]}})


def test_the_daemon_passes_live_configured_roots(tmp_path: Path) -> None:
    daemon = Daemon.__new__(Daemon)
    current = [Rules(workspace={"roots": [str(tmp_path)]})]  # type: ignore[arg-type]
    daemon.rules_source = lambda: current[0]
    daemon.engine = None
    daemon.db = object()  # type: ignore[assignment]
    daemon.reload_rules = None
    context = daemon.tool_context()
    assert list(context.configured_roots()) == [str(tmp_path)]
    current[0] = Rules()
    assert list(context.configured_roots()) == []


def test_configured_roots_ignore_outside_host_roots_and_refuse_outside_paths(
    tmp_path: Path,
) -> None:
    proj = tmp_path / "proj"
    other = tmp_path / "other"
    proj.mkdir()
    other.mkdir()
    rules = Rules(workspace={"roots": [str(proj)]})  # type: ignore[arg-type]
    ws = open_workspace(
        {META_CWD: str(other), META_ROOTS: [str(other)]}, rules, rules.workspace.roots
    )
    assert ws.roots is not None
    assert ws.roots.roots == (proj.resolve(),) or ws.roots.roots == (proj,)
    with pytest.raises(WorkspaceError):
        ws.roots.resolve(str(other / "a.txt"))


def test_unset_roots_behave_as_before(tmp_path: Path) -> None:
    rules = Rules()
    ws = open_workspace({META_CWD: str(tmp_path)}, rules, rules.workspace.roots)
    assert ws.roots is not None
    assert ws.roots.source == "cwd"
