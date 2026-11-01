"""HDW-207: MCP prompts, command files and rules packs.

Accept when: prompts are listed and callable in the test client (also through the shim); the rules
files pass a lint check (length, no contradictory instructions); command files match the prompts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp import Client, types

from headwaters.mcp.shim import build_shim_server
from headwaters.mcp.tools import descriptions
from headwaters.mcp.tools.commands import COMMANDS, render_command_file, render_prompt
from headwaters.mcp.tools.rules_lint import MAX_LINES, lint_rules
from tests.frontdoor_support import REPO_ROOT, Rig
from tests.mcp_shim_support import FakeUpstream, make_link

SLUGS = [
    "headwaters-local",
    "headwaters-free",
    "headwaters-auto",
    "headwaters-handoff",
    "headwaters-status",
]
TOOLS = {
    "headwaters_do",
    "headwaters_ask",
    "headwaters_check",
    "headwaters_think",
    "headwaters_status",
    "headwaters_scope",
    "headwaters_handoff",
    "headwaters_more",
    *descriptions.ADVANCED,
}
RULES = REPO_ROOT / "integrations" / "rules"
RULE_FILES = [
    "AGENTS.md",
    ".clinerules",
    ".kilocode/rules/headwaters.md",
    ".roo/rules/headwaters.md",
    ".continue/rules/headwaters.md",
    ".github/copilot-instructions.md",
]


def _text(result: types.GetPromptResult) -> str:
    return "".join(
        m.content.text for m in result.messages if isinstance(m.content, types.TextContent)
    )


def test_the_five_prompts_are_listed_and_callable(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> tuple[list[str], dict[str, str]]:
        async with rig.client() as client:
            listed = await client.list_prompts()
            got = {s: _text(await client.get_prompt(s)) for s in SLUGS}
        return [p.name for p in listed.prompts], got

    names, got = asyncio.run(scenario())
    assert sorted(names) == sorted(SLUGS)
    assert '"scope": "local"' in got["headwaters-local"]
    assert '"scope": "free"' in got["headwaters-free"]
    assert '"scope": "auto"' in got["headwaters-auto"]
    assert "headwaters_handoff" in got["headwaters-handoff"]
    assert "headwaters_status" in got["headwaters-status"]


def test_scope_prompts_never_tell_the_model_to_loosen_a_lock_itself() -> None:
    for spec in COMMANDS:
        if "scope" in spec.slug or spec.slug.endswith(("local", "free", "auto")):
            text = render_prompt(spec)
            assert "Never run it yourself" in text
            assert "private_lock" not in text


def test_handoff_prompt_takes_an_optional_goal(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def scenario() -> tuple[str, str]:
        async with rig.client() as client:
            with_goal = _text(await client.get_prompt("headwaters-handoff", {"goal": "fix io"}))
            without = _text(await client.get_prompt("headwaters-handoff"))
        return with_goal, without

    with_goal, without = asyncio.run(scenario())
    assert "The goal: fix io" in with_goal
    assert "Work out the goal" in without
    assert "{goal}" not in with_goal + without


def test_the_shim_forwards_prompts_to_the_daemon() -> None:
    upstream = FakeUpstream()

    async def scenario() -> tuple[list[str], str]:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            listed = await client.list_prompts()
            got = await client.get_prompt("headwaters-status", {"x": "y"})
        await link.close()
        return [p.name for p in listed.prompts], _text(got)

    names, text = asyncio.run(scenario())
    assert names == ["headwaters-status"]
    assert text == "do it"
    assert upstream.prompt_gets == [("headwaters-status", {"x": "y"})]


@pytest.mark.parametrize("spec", COMMANDS, ids=lambda s: s.slug)
def test_command_files_match_the_prompts(spec) -> None:  # type: ignore[no-untyped-def]
    path = REPO_ROOT / "integrations" / "commands" / f"{spec.slug}.md"
    assert path.read_text(encoding="utf-8") == render_command_file(spec)
    assert path.read_text(encoding="utf-8").startswith("---\ndescription: ")


def test_there_is_a_command_file_for_every_prompt_and_no_other() -> None:
    folder = REPO_ROOT / "integrations" / "commands"
    assert sorted(p.stem for p in folder.glob("*.md")) == sorted(SLUGS)


@pytest.mark.parametrize("name", RULE_FILES)
def test_rules_files_pass_the_lint(name: str) -> None:
    text = (RULES / name).read_text(encoding="utf-8")
    assert lint_rules(text, TOOLS) == []
    assert len(text.splitlines()) <= MAX_LINES


def test_all_rules_packs_teach_the_same_thing() -> None:
    first = (RULES / RULE_FILES[0]).read_text(encoding="utf-8")
    assert all((RULES / n).read_text(encoding="utf-8") == first for n in RULE_FILES)


def test_the_lint_catches_each_kind_of_problem() -> None:
    good = (RULES / "AGENTS.md").read_text(encoding="utf-8")
    assert any("too long" in p for p in lint_rules(good + "line\n" * 50, TOOLS))
    assert any("unknown tool" in p for p in lint_rules(good + "Call `headwaters_nope`.", TOOLS))
    assert any("relay" in p for p in lint_rules(good.replace("`relay`", "the flag"), TOOLS))
    clash = good + "\nNever call `headwaters_do` for edits.\n"
    assert any("contradiction" in p for p in lint_rules(clash, TOOLS))
    assert lint_rules("", TOOLS)  # an empty file teaches nothing
