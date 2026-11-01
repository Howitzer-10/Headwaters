"""HDW-208: end-to-end MCP suite. One in-process client, a real engine, mocked providers.

Every tool, the relay contract, local-scope fail-closed behaviour, progressive disclosure,
elicitation and background jobs are exercised through the public MCP surface only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import types

from headwaters.core.rules import Rules
from headwaters.mcp.meta import META_CLIENT, META_CWD
from headwaters.mcp.tools import descriptions
from tests.frontdoor_support import (
    CODE_TASK,
    DIFF_ANSWER,
    Rig,
    make_repo,
    structured,
    text_of,
)
from tests.run_support import PAID, agree, cold_rig, paid_rig, start_and_finish

FRONT_DOOR = {
    "headwaters_do",
    "headwaters_ask",
    "headwaters_check",
    "headwaters_think",
    "headwaters_status",
    "headwaters_scope",
    "headwaters_handoff",
    "headwaters_more",
}
APP = "def run():\n    return 1\n"


def test_a_new_host_sees_exactly_the_eight_front_door_tools(tmp_path: Path) -> None:
    async def go() -> list[types.Tool]:
        async with Rig(tmp_path).client() as client:
            return list((await client.list_tools()).tools)

    tools = asyncio.run(go())
    assert {t.name for t in tools} == FRONT_DOOR
    assert all(t.description and t.output_schema for t in tools)


def test_the_whole_session_do_check_status_more(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"app.py": APP})
    rig = Rig(tmp_path, reply=DIFF_ANSWER)

    async def go() -> dict[str, Any]:
        out: dict[str, Any] = {}
        async with rig.client() as client:
            meta: Any = {META_CLIENT: "e2e", META_CWD: str(repo)}
            args = {"task": "return 2", "paths": ["app.py"], "want": "diff"}
            out["do"] = structured(await client.call_tool("headwaters_do", args, meta=meta))
            diff = out["do"]["diffs"][0]
            out["check"] = structured(
                await client.call_tool("headwaters_check", {"diff": diff}, meta=meta)
            )
            out["status"] = structured(await client.call_tool("headwaters_status", {}))
            await client.call_tool("headwaters_more", {}, meta=meta)
            listed = await client.list_tools(meta=meta, cache_mode="bypass")
            out["tools"] = {t.name for t in listed.tools}
        return out

    out = asyncio.run(go())
    assert out["do"]["relay"] == "apply_diff"
    assert out["check"]["verified"] is True
    assert out["status"]["scope"]["default_scope"] == "auto"
    assert set(descriptions.ADVANCED) <= out["tools"]


def test_a_failed_check_is_never_relayed_as_an_applicable_diff(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"app.py": APP})
    rig = Rig(tmp_path, reply=DIFF_ANSWER.replace("return 1", "return 99"))
    args = {"task": "return 2", "paths": ["app.py"], "want": "diff"}
    data = structured(asyncio.run(rig.call("headwaters_do", args, repo)))
    assert data["verified"] is False
    assert data["relay"] == "verbatim"


def test_ask_and_do_agree_on_the_relay_for_plain_prose(tmp_path: Path) -> None:
    rig = Rig(tmp_path, reply="It caches results.")
    ask = structured(asyncio.run(rig.call("headwaters_ask", {"question": "how?"})))
    do = structured(asyncio.run(rig.call("headwaters_do", {"task": "explain the cache"})))
    assert ask["relay"] == do["relay"] == "summarize_ok"


def test_local_scope_is_fail_closed_through_every_model_tool(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    for name, args in (
        ("headwaters_do", {"task": CODE_TASK, "scope": "local"}),
        ("headwaters_ask", {"question": CODE_TASK, "scope": "local"}),
    ):
        assert structured(asyncio.run(rig.call(name, args)))["tier"] == "local"
    assert rig.cloud_calls == 0


def test_a_private_lock_beats_a_request_for_cloud(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True, rules=Rules(private_lock=True))
    result = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "cloud"}))
    assert result.is_error is True
    assert structured(result)["error"]["code"] == "private_lock_conflict"
    assert rig.cloud_calls == 0


def test_loosening_a_scope_needs_the_user_and_is_refused_without_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local"))
    result = asyncio.run(rig.call("headwaters_scope", {"scope": "auto"}))
    assert structured(result)["confirmation_required"] is True
    assert rig.edits == []
    assert "Do not run it yourself" in text_of(result)


def test_handoff_keeps_file_hashes_but_never_file_contents(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"app.py": "E2E_BODY_MARKER = 1\n"})
    result = asyncio.run(
        Rig(tmp_path).call("headwaters_handoff", {"goal": "g", "paths": ["app.py"]}, repo)
    )
    assert structured(result)["ok"] is True
    assert "E2E_BODY_MARKER" not in json.dumps(result.structured_content) + text_of(result)


def test_a_slow_call_becomes_a_job_whose_result_is_fetched_later(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> dict[str, Any]:
        started = await start_and_finish(rig, "headwaters_do", {"task": CODE_TASK})
        return structured(await rig.call("headwaters_job_result", {"job_id": started["job_id"]}))

    done = asyncio.run(go())
    assert done["result"]["structured"]["answer"] == "Plain answer."


def test_every_front_door_description_is_short_and_has_an_example() -> None:
    for text in (
        descriptions.DO,
        descriptions.ASK,
        descriptions.CHECK,
        descriptions.THINK,
        descriptions.STATUS,
        descriptions.SCOPE,
        descriptions.HANDOFF,
        descriptions.MORE,
    ):
        assert "Example:" in text
        assert len(text.split()) <= 120


def test_a_spend_question_goes_to_the_user_and_a_yes_runs_the_call(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    args = {"question": CODE_TASK}
    data = structured(asyncio.run(rig.call("headwaters_ask", args, elicit=agree)))
    assert data["ok"] is True
    assert data["model"] == PAID
