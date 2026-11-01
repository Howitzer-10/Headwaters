"""HDW-205: headwaters_handoff (basic): goal, files by hash, last error, recent turns."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from headwaters.core.state_misc import get_handoff
from headwaters.mcp.tools.handoff import REDACTED
from tests.cli_support import FAKE_KEY
from tests.frontdoor_support import Rig, make_repo, structured, text_of

BODY = "def run():\n    return 1\n"


def _call(rig: Rig, args: dict[str, Any], cwd: Path | None = None) -> dict[str, Any]:
    return structured(asyncio.run(rig.call("headwaters_handoff", args, cwd)))


def test_the_packet_has_goal_files_last_error_and_turns(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"app.py": BODY})
    rig = Rig(tmp_path)
    data = _call(
        rig,
        {
            "goal": "fix the flaky test",
            "paths": ["app.py"],
            "last_error": "AssertionError: 1 != 2",
            "recent_turns": ["user: it fails", "assistant: try x"],
        },
        repo,
    )
    packet = data["packet"]
    assert data["ok"] is True
    assert packet["goal"] == "fix the flaky test"
    assert packet["last_error"] == "AssertionError: 1 != 2"
    assert packet["recent_turns"] == ["user: it fails", "assistant: try x"]
    (entry,) = packet["files"]
    assert entry["path"] == "app.py"
    assert entry["sha256"] == hashlib.sha256(BODY.encode()).hexdigest()


def test_file_contents_are_never_in_the_packet_or_the_text(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"app.py": "UNIQUE_BODY_MARKER = 1\n"})
    rig = Rig(tmp_path)
    result = asyncio.run(rig.call("headwaters_handoff", {"goal": "g", "paths": ["app.py"]}, repo))
    assert "UNIQUE_BODY_MARKER" not in json.dumps(result.structured_content)
    assert "UNIQUE_BODY_MARKER" not in text_of(result)


def test_a_secret_in_any_field_is_removed_and_counted(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    data = _call(
        rig,
        {
            "goal": f"use {FAKE_KEY} to call the api",
            "last_error": f"401 for key {FAKE_KEY}",
            "recent_turns": [f"user: here is {FAKE_KEY}"],
        },
    )
    blob = json.dumps(data)
    assert FAKE_KEY not in blob
    assert REDACTED in blob
    assert data["redacted"] == 3
    stored = get_handoff(rig.db, int(data["packet_id"]))
    assert stored is not None
    assert FAKE_KEY not in json.dumps(stored)  # nor in the saved copy


def test_only_the_last_turns_are_kept_and_the_packet_says_so(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    turns = [f"turn {i}" for i in range(20)]
    data = _call(rig, {"goal": "g", "recent_turns": turns})
    kept = data["packet"]["recent_turns"]
    assert kept == turns[-6:]
    assert any("last 6 turns" in n for n in data["notes"])


def test_a_long_field_is_cut(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    data = _call(rig, {"goal": "x" * 50_000})
    goal = data["packet"]["goal"]
    assert len(goal) < 5_000
    assert goal.endswith("[cut]")


def test_unreadable_paths_are_reported_and_not_listed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"app.py": BODY})
    rig = Rig(tmp_path)
    data = _call(rig, {"goal": "g", "paths": ["app.py", "../outside.py", "missing.py"]}, repo)
    statuses = {f["path"]: f["status"] for f in data["files"]}
    assert statuses["app.py"] == "included"
    assert statuses["../outside.py"] in ("outside", "invalid")
    assert statuses["missing.py"] == "missing"
    assert [f["path"] for f in data["packet"]["files"]] == ["app.py"]


def test_paths_without_a_workspace_are_not_listed(tmp_path: Path) -> None:
    data = _call(Rig(tmp_path), {"goal": "g", "paths": ["app.py"]})
    assert data["packet"]["files"] == []
    assert data["files"][0]["status"] == "missing"


def test_the_packet_is_saved_and_can_be_read_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    data = _call(rig, {"goal": "carry on"})
    stored = get_handoff(rig.db, int(data["packet_id"]))
    assert stored is not None
    assert stored["goal"] == "carry on"


def test_a_goal_is_required(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_handoff", {"goal": ""}))
    assert result.is_error is True
