"""HDW-403: the installer's parts - JSONC, merges, marked blocks, diffs, the launcher check.

These never write into a real host config: every path comes from a temporary ``Context``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.mcp_launch import McpLaunch, MissingLauncher, require_existing
from headwaters.installer import commands, jsonc, merge, plan, templates, text
from headwaters.installer.context import Context
from headwaters.installer.names import MCP_SERVER_NAME


def make_ctx(tmp_path: Path, *, port: int = 43123, args: tuple[str, ...] = ()) -> Context:
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)
    launch = McpLaunch("script", str(tmp_path / "bin" / "headwaters-mcp"), args)
    return Context(project=project, home=home, state=tmp_path / "state", launch=launch, port=port)


@pytest.fixture(autouse=True)
def _isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may read or write the real profile: APPDATA names a throwaway folder."""
    appdata = tmp_path / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    for name in ("APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(appdata))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))


# -- JSONC -----------------------------------------------------------------------------------
def test_jsonc_reads_comments_and_trailing_commas() -> None:
    raw = '{\n  // a comment\n  "a": 1,\n  "b": [1, 2,],\n}\n'
    assert jsonc.loads(raw) == {"a": 1, "b": [1, 2]}
    assert jsonc.needs_lenient_parse(raw) is True
    assert jsonc.needs_lenient_parse('{"a": 1}') is False


def test_jsonc_keeps_a_double_slash_inside_a_string() -> None:
    assert jsonc.loads('{"url": "https://example.test/x"}') == {"url": "https://example.test/x"}


def test_jsonc_raises_on_broken_text() -> None:
    with pytest.raises(jsonc.JsoncError):
        jsonc.loads("{oops}")


def test_jsonc_load_file_treats_an_empty_file_as_absent(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert jsonc.load_file(path, {}) == {}
    path.write_text("   \n", encoding="utf-8")
    assert jsonc.load_file(path, []) == []


# -- merges ----------------------------------------------------------------------------------
def test_merge_object_keeps_the_other_keys_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{\n  "servers": {"other": {"command": "x"}}\n}\n', encoding="utf-8")

    def update(data: dict[str, object]) -> None:
        servers = data.setdefault("servers", {})
        assert isinstance(servers, dict)
        servers[MCP_SERVER_NAME] = {"command": "ours"}

    after, note = merge.merge_object(path, update)
    assert note == ""
    assert jsonc.loads(after)["servers"] == {
        "other": {"command": "x"},
        MCP_SERVER_NAME: {"command": "ours"},
    }
    path.write_text(after, encoding="utf-8")
    again, _ = merge.merge_object(path, update)
    assert again == after


def test_merge_object_notes_that_comments_are_lost(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{\n  // mine\n  "a": 1\n}\n', encoding="utf-8")
    after, note = merge.merge_object(path, lambda data: data.update({"b": 2}))
    assert jsonc.loads(after) == {"a": 1, "b": 2}
    assert note == merge.COMMENTS_NOTE


def test_merge_list_replaces_one_entry_and_appends_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "chatLanguageModels.json"
    path.write_text('[{"name": "Mine", "id": "x"}]\n', encoding="utf-8")

    def update(items: list[object]) -> None:
        items.append({"name": "Headwaters"})

    after, _ = merge.merge_list(path, update)
    assert jsonc.loads(after) == [{"name": "Mine", "id": "x"}, {"name": "Headwaters"}]


def test_merge_object_refuses_a_top_level_array(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(jsonc.JsoncError):
        merge.merge_object(path, lambda data: None)


# -- marked block ----------------------------------------------------------------------------
def test_text_upsert_adds_one_block_and_keeps_the_rest() -> None:
    once = text.upsert("# Project rules\n", "Headwaters: use the gateway.")
    assert once.startswith("# Project rules\n")
    assert once.count(text.BEGIN) == 1
    twice = text.upsert(once, "Headwaters: use the gateway.")
    assert twice == once
    changed = text.upsert(once, "Headwaters: something new.")
    assert "something new" in changed
    assert changed.count(text.BEGIN) == 1


def test_text_remove_takes_only_the_block() -> None:
    added = text.upsert("# Project rules\n", "Headwaters rules.")
    assert text.remove(added) == "# Project rules\n"
    assert text.remove(text.upsert(None, "Only us.")) == ""


# -- diff ------------------------------------------------------------------------------------
def test_plan_render_marks_create_update_and_unchanged(tmp_path: Path) -> None:
    keep = tmp_path / "same.json"
    keep.write_text("{}\n", encoding="utf-8")
    changes = [
        plan.change(tmp_path / "new.json", "{}\n"),
        plan.change(keep, "{}\n"),
        plan.change(tmp_path / "edit.json", "one\ntwo\n"),
    ]
    (tmp_path / "edit.json").write_text("one\n", encoding="utf-8")
    changes[2] = plan.change(tmp_path / "edit.json", "one\ntwo\n")
    rendered = plan.render(changes)
    assert f"  create     {tmp_path / 'new.json'}" in rendered
    assert f"  unchanged  {keep}" in rendered
    assert f"  update     {tmp_path / 'edit.json'}" in rendered
    assert "+two" in rendered


def test_plan_change_treats_a_missing_file_as_new(tmp_path: Path) -> None:
    change = plan.change(tmp_path / "absent.json", "{}\n")
    assert change.kind == plan.CREATE
    assert change.changed is True


# -- command files ---------------------------------------------------------------------------
def test_render_gives_each_host_its_own_argument_style() -> None:
    command = commands.CommandFile("headwaters-handoff", "Hand off.", "Carry on with $ARGUMENTS.\n")
    dollar = commands.render(command, commands.DOLLAR_STYLE)
    assert "Carry on with $ARGUMENTS." in dollar
    prompt = commands.render(command, commands.INPUT_STYLE)
    assert "${input:goal}" in prompt
    assert "argument-hint:" in prompt
    literal = commands.render(command, commands.LITERAL_STYLE)
    assert "$ARGUMENTS" not in literal
    assert "the goal stated after this command" in literal


def test_the_pack_ships_five_commands() -> None:
    names = [command.name for command in commands.commands()]
    assert len(names) == 5
    assert all(name.startswith("headwaters-") for name in names)


# -- templates -------------------------------------------------------------------------------
def test_templates_fill_paths_after_parsing_not_as_text(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    block = templates.load("vscode_mcp", ctx)
    entry = block["servers"][MCP_SERVER_NAME]
    assert entry["command"] == ctx.launch.command
    assert entry["args"] == []


def test_templates_carry_the_gateway_urls(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, port=45999)
    byok = templates.load("vscode_byok", ctx)[0]
    assert {model["url"] for model in byok["models"]} == {
        "http://127.0.0.1:45999/v1/chat/completions"
    }
    assert byok["apiKey"] == "${input:headwatersToken}"


# -- launcher (FU-202-5) ---------------------------------------------------------------------
def test_require_existing_takes_the_script_beside_the_interpreter(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / ("headwaters-mcp.exe")
    script.write_text("", encoding="utf-8")
    (bin_dir / "python.exe").write_text("", encoding="utf-8")
    chosen = require_existing(str(bin_dir / "python.exe"))
    assert chosen.command == str(script)


def test_require_existing_raises_when_the_interpreter_is_gone(tmp_path: Path) -> None:
    missing = tmp_path / "gone" / "python.exe"
    with pytest.raises(MissingLauncher) as caught:
        require_existing(str(missing))
    assert "no MCP launcher exists" in str(caught.value)
