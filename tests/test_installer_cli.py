"""HDW-403: ``headwaters install`` / ``headwaters uninstall`` through the real CLI.

The CLI runs against a temporary HOME, APPDATA, HEADWATERS_HOME and project, so nothing here can
reach a real host config. A refusal must leave every file exactly as it was.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import Result

from headwaters.cli.main import app
from headwaters.installer import jsonc
from headwaters.installer.names import MCP_SERVER_NAME
from tests.cli_support import runner


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway profile and project, plus the state dir the manifest lives in."""
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    project = tmp_path / "project"
    for folder in (home, appdata, project):
        folder.mkdir()
    for name, value in (
        ("HOME", home),
        ("USERPROFILE", home),
        ("APPDATA", appdata),
        ("XDG_CONFIG_HOME", appdata),
        ("HEADWATERS_HOME", tmp_path / "state"),
    ):
        monkeypatch.setenv(name, str(value))
    monkeypatch.delenv("CLINE_DIR", raising=False)
    monkeypatch.delenv("CLINE_DATA_DIR", raising=False)
    monkeypatch.chdir(project)
    return project


def run(*args: str, input_text: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=input_text)


def claude_config(sandbox: Path) -> Path:
    return sandbox.parent / "appdata" / "Claude" / "claude_desktop_config.json"


def test_cli_dry_run_writes_nothing(sandbox: Path) -> None:
    result = run("install", "--host", "vscode", "--dry-run")
    assert result.exit_code == 0, result.output
    assert "create" in result.output
    assert "dry run" in result.output
    assert not (sandbox / ".vscode").exists()
    assert not (sandbox / ".github").exists()


def test_cli_install_writes_after_a_yes(sandbox: Path) -> None:
    result = run("install", "--host", "claude-desktop", input_text="y\n")
    assert result.exit_code == 0, result.output
    assert "wrote" in result.output
    assert "checked    ok" in result.output
    config = claude_config(sandbox)
    assert jsonc.loads(config.read_text(encoding="utf-8"))["mcpServers"][MCP_SERVER_NAME]


def test_cli_refuses_to_write_without_consent(sandbox: Path) -> None:
    result = run("install", "--host", "opencode", input_text="n\n")
    assert result.exit_code == 0, result.output
    assert "you said no" in result.output
    assert not (sandbox / "opencode.json").exists()
    assert not (sandbox / "AGENTS.md").exists()
    assert not claude_config(sandbox).exists()


def test_cli_install_yes_installs_every_host(sandbox: Path) -> None:
    result = run("install", "--host", "all", "--yes")
    assert result.exit_code == 0, result.output
    assert (sandbox / ".vscode" / "mcp.json").is_file()
    assert (sandbox / "opencode.json").is_file()
    assert (sandbox / ".clinerules" / "headwaters.md").is_file()
    assert claude_config(sandbox).is_file()


def test_cli_install_json_does_not_write_on_its_own(sandbox: Path) -> None:
    result = run("install", "--host", "opencode", "--json")
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["changed"] is False  # no --yes, so nothing was written
    assert [row["host"] for row in data["results"]] == ["opencode"]
    assert not (sandbox / "opencode.json").exists()


def test_cli_install_yields_an_idempotent_second_run(sandbox: Path) -> None:
    run("install", "--host", "cline", "--yes")
    again = run("install", "--host", "cline", "--yes")
    assert again.exit_code == 0, again.output
    assert "already installed" in again.output


def test_cli_uninstall_restores_and_removes(sandbox: Path) -> None:
    settings = sandbox / "opencode.json"
    settings.write_text('{"theme": "dark"}\n', encoding="utf-8")
    run("install", "--host", "opencode", "--yes")
    assert jsonc.loads(settings.read_text(encoding="utf-8"))["theme"] == "dark"
    result = run("uninstall", "--host", "opencode", "--yes")
    assert result.exit_code == 0, result.output
    assert settings.read_text(encoding="utf-8") == '{"theme": "dark"}\n'
    assert not (sandbox / "AGENTS.md").exists()


def test_cli_uninstall_without_consent_keeps_the_install(sandbox: Path) -> None:
    run("install", "--host", "opencode", "--yes")
    result = run("uninstall", "--host", "opencode", input_text="n\n")
    assert result.exit_code == 0, result.output
    assert (sandbox / "opencode.json").is_file()
    assert "you said no" in result.output


def test_cli_rejects_an_unknown_host() -> None:
    result = run("install", "--host", "nope")
    assert result.exit_code == 2
    assert "unknown host" in result.output


def test_cli_lists_the_hosts_in_help() -> None:
    result = run("install", "--help")
    assert result.exit_code == 0, result.output
    for name in ("vscode", "cline", "opencode", "claude-desktop", "all"):
        assert name in result.output


def test_cli_project_option_targets_another_folder(sandbox: Path) -> None:
    other = sandbox.parent / "other"
    other.mkdir()
    result = run("install", "--host", "opencode", "--project", str(other), "--yes")
    assert result.exit_code == 0, result.output
    assert (other / "opencode.json").is_file()
    assert not (sandbox / "opencode.json").exists()
