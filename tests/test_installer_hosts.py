"""HDW-403: each host adapter, and the engine that writes, verifies and undoes its plan.

Every test builds its own project, home and state under ``tmp_path``, so a run here can never touch
the real ``%APPDATA%``, a real host config or the Windows Credential Manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core.mcp_launch import McpLaunch
from headwaters.installer import engine, jsonc, verify
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context
from headwaters.installer.hosts import all_adapters, get_host, host_ids
from headwaters.installer.hosts.vscode import ENTERPRISE_NOTE
from headwaters.installer.names import MCP_SERVER_NAME, OPENCODE_PROVIDER_ID
from headwaters.installer.plan import FileChange, read_text


@pytest.fixture(autouse=True)
def _isolated_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    home.mkdir()
    appdata.mkdir()
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


def make_ctx(tmp_path: Path, *, port: int = 43123) -> Context:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    home = tmp_path / "home"
    return Context(
        project=project,
        home=home,
        state=tmp_path / "state",
        launch=McpLaunch("script", str(tmp_path / "bin" / "headwaters-mcp")),
        port=port,
    )


def paths_of(result: engine.HostResult) -> dict[str, FileChange]:
    return {item.path.name: item for item in result.changes}


# -- the registry ----------------------------------------------------------------------------
def test_the_four_hosts_of_this_ticket_are_registered() -> None:
    assert host_ids() == ("vscode", "cline", "opencode", "claude-desktop")
    assert get_host("nope") is None
    assert all(isinstance(adapter, HostAdapter) for adapter in all_adapters())


# -- VS Code ---------------------------------------------------------------------------------
def test_vscode_plans_the_mcp_entry_the_byok_models_and_the_prompts(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("vscode"), ctx)  # type: ignore[arg-type]
    changes = paths_of(result)
    assert {"mcp.json", "settings.json", "chatLanguageModels.json"} <= set(changes)
    assert "copilot-instructions.md" in changes
    assert changes["chatLanguageModels.json"].path.parent == ctx.appdata / "Code" / "User"
    prompts = [item.path for item in result.changes if item.path.parent.name == "prompts"]
    assert len(prompts) == 5
    assert all(path.name.endswith(".prompt.md") for path in prompts)


def test_vscode_notes_the_enterprise_policy(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    adapter = get_host("vscode")
    assert adapter is not None
    assert ENTERPRISE_NOTE in adapter.notes(ctx)


def test_vscode_byok_lists_the_three_picker_names_and_no_secret(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, port=45555)
    result = engine.plan(get_host("vscode"), ctx)  # type: ignore[arg-type]
    text = paths_of(result)["chatLanguageModels.json"].after
    providers: Any = jsonc.loads(text)
    models = providers[0]["models"]
    assert [model["id"] for model in models] == [
        "headwaters/auto",
        "headwaters/free",
        "headwaters/local",
    ]
    assert {model["url"] for model in models} == {"http://127.0.0.1:45555/v1/chat/completions"}
    assert providers[0]["apiKey"] == "${input:headwatersToken}"


def test_vscode_keeps_a_server_the_user_already_had(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    mcp = ctx.project / ".vscode" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text('{"servers": {"mine": {"command": "theirs"}}}\n', encoding="utf-8")
    result = engine.plan(get_host("vscode"), ctx)  # type: ignore[arg-type]
    after = jsonc.loads(paths_of(result)["mcp.json"].after)
    assert after["servers"]["mine"] == {"command": "theirs"}
    assert after["servers"][MCP_SERVER_NAME]["command"] == ctx.launch.command


def test_vscode_settings_turn_on_byok_models(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("vscode"), ctx)  # type: ignore[arg-type]
    after = jsonc.loads(paths_of(result)["settings.json"].after)
    assert after["chat.agentHost.byokModels.enabled"] is True


# -- Cline -----------------------------------------------------------------------------------
def test_cline_writes_its_own_settings_file_and_honours_cline_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "cline-data"
    monkeypatch.setenv("CLINE_DATA_DIR", str(data_dir))
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("cline"), ctx)  # type: ignore[arg-type]
    settings = paths_of(result)["cline_mcp_settings.json"]
    assert settings.path == data_dir / "settings" / "cline_mcp_settings.json"
    entry = jsonc.loads(settings.after)["mcpServers"][MCP_SERVER_NAME]
    assert entry["command"] == ctx.launch.command
    assert entry["disabled"] is False


def test_cline_installs_rules_and_workflows_into_the_project(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("cline"), ctx)  # type: ignore[arg-type]
    rules = paths_of(result)["headwaters.md"]
    assert rules.path == ctx.project / ".clinerules" / "headwaters.md"
    workflows = [item.path for item in result.changes if item.path.parent.name == "workflows"]
    assert len(workflows) == 5
    assert all(path.name.startswith("headwaters-") for path in workflows)


def test_cline_workflows_have_no_dollar_arguments(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("cline"), ctx)  # type: ignore[arg-type]
    bodies = [
        item.after
        for item in result.changes
        if item.path.parent == ctx.project / ".cline" / "workflows"
    ]
    assert bodies
    assert all("$ARGUMENTS" not in body for body in bodies)


# -- OpenCode --------------------------------------------------------------------------------
def test_opencode_writes_the_provider_it_also_suggests(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("opencode"), ctx)  # type: ignore[arg-type]
    config = paths_of(result)["opencode.json"]
    data = jsonc.loads(config.after)
    provider = data["provider"][OPENCODE_PROVIDER_ID]
    assert provider["options"]["baseURL"] == ctx.base_url
    assert provider["options"]["apiKey"] == "{env:HEADWATERS_TOKEN}"
    assert set(provider["models"]) == {"auto", "free", "local"}
    assert data["mcp"][MCP_SERVER_NAME]["command"] == [ctx.launch.command]
    assert data["mcp"][MCP_SERVER_NAME]["type"] == "local"


def test_opencode_prefers_the_jsonc_file_already_there(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    existing = ctx.project / "opencode.jsonc"
    existing.write_text('{\n  // mine\n  "theme": "dark"\n}\n', encoding="utf-8")
    result = engine.plan(get_host("opencode"), ctx)  # type: ignore[arg-type]
    config = paths_of(result)["opencode.jsonc"]
    assert config.note == "comments are dropped by the rewrite; the original is backed up"
    assert jsonc.loads(config.after)["theme"] == "dark"


def test_opencode_commands_and_rules(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("opencode"), ctx)  # type: ignore[arg-type]
    commands = [item for item in result.changes if item.path.parent.name == "commands"]
    assert len(commands) == 5
    handoff = next(item for item in commands if item.path.name == "headwaters-handoff.md")
    assert "$ARGUMENTS" in handoff.after
    rules = paths_of(result)["AGENTS.md"]
    assert rules.path == ctx.project / "AGENTS.md"
    assert "<!-- headwaters:begin" in rules.after


# -- Claude Desktop --------------------------------------------------------------------------
def test_claude_desktop_writes_only_the_mcp_entry(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    result = engine.plan(get_host("claude-desktop"), ctx)  # type: ignore[arg-type]
    assert len(result.changes) == 1
    path = result.changes[0].path
    assert path == ctx.appdata / "Claude" / "claude_desktop_config.json"
    assert jsonc.loads(result.changes[0].after)["mcpServers"][MCP_SERVER_NAME] == {
        "command": ctx.launch.command,
        "args": [],
    }
    assert not get_host("claude-desktop").has_commands  # type: ignore[union-attr]


# -- apply, verify, uninstall ----------------------------------------------------------------
def test_apply_backs_up_the_original_and_verifies(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    mcp = ctx.project / ".vscode" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text('{"servers": {}}\n', encoding="utf-8")
    adapter = get_host("vscode")
    assert adapter is not None
    before = read_text(mcp)
    result = engine.apply(adapter, ctx)
    assert result.verified is True
    assert result.errors == []
    assert result.problems == []
    assert str(mcp) in result.written
    backup = mcp.with_name(mcp.name + ".headwaters-backup")
    assert read_text(backup) == before
    assert read_text(mcp) != before
    assert (ctx.state / "install" / "manifest.json").is_file()


def test_a_second_plan_after_apply_has_nothing_pending(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    adapter = get_host("opencode")
    assert adapter is not None
    engine.apply(adapter, ctx)
    assert engine.plan(adapter, ctx).pending == []


def test_uninstall_restores_the_backup_and_removes_the_files_it_created(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    mcp = ctx.project / ".vscode" / "mcp.json"
    mcp.parent.mkdir(parents=True)
    mcp.write_text('{"servers": {}}\n', encoding="utf-8")
    adapter = get_host("vscode")
    assert adapter is not None
    created = ctx.project / ".github" / "prompts" / "headwaters-auto.prompt.md"
    engine.apply(adapter, ctx)
    assert created.is_file()
    result = engine.uninstall(adapter, ctx)
    assert result.errors == []
    assert result.verified is True
    assert read_text(mcp) == '{"servers": {}}\n'
    assert not created.exists()
    assert not mcp.with_name(mcp.name + ".headwaters-backup").exists()


def test_uninstall_leaves_a_file_the_user_edited_alone(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    adapter = get_host("claude-desktop")
    assert adapter is not None
    engine.apply(adapter, ctx)
    path = adapter.config_paths(ctx)[0]
    path.write_text('{"mcpServers": {}}\n', encoding="utf-8")
    result = engine.uninstall(adapter, ctx)
    assert result.verified is False
    assert any("changed since install" in line for line in result.errors)
    assert jsonc.loads(path.read_text(encoding="utf-8")) == {"mcpServers": {}}
    forced = engine.uninstall(adapter, ctx, force=True)
    assert forced.errors == []


def test_uninstall_without_a_record_says_so(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    adapter = get_host("cline")
    assert adapter is not None
    result = engine.uninstall(adapter, ctx)
    assert result.verified is False
    assert "no install recorded" in result.errors[0]


# -- doctor support (FU-402-2) ---------------------------------------------------------------
def test_verify_reports_a_stale_launcher_and_a_missing_setting(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    found = verify.problems(ctx)
    assert found == []
    adapter = get_host("vscode")
    assert adapter is not None
    engine.apply(adapter, ctx)
    assert verify.problems(ctx) == []
    # the entry a previous install wrote, now pointing at another interpreter
    mcp = ctx.project / ".vscode" / "mcp.json"
    data = jsonc.loads(read_text(mcp) or "{}")
    data["servers"][MCP_SERVER_NAME]["command"] = "C:/old/python.exe"
    mcp.write_text(jsonc.dumps(data), encoding="utf-8")
    settings = ctx.project / ".vscode" / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")
    found = verify.problems(ctx)
    details = " ".join(item.detail for item in found)
    assert "not the current launcher" in details
    assert "chat.agentHost.byokModels.enabled" in details


def test_verify_does_not_read_a_host_that_is_not_installed(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    assert verify.problems(ctx) == []


def test_verify_reports_an_unreadable_config(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    broken = ctx.project / "opencode.json"
    broken.write_text("{not json", encoding="utf-8")
    found = verify.problems(ctx)
    assert any(item.host == "opencode" for item in found)
