"""HDW-204: ``headwaters_ask`` is read-only Q&A over paths."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

from tests.frontdoor_support import CODE_TASK, DIFF_ANSWER, Rig, make_repo, structured, text_of

MARKER = "zq-ask-marker-7"


def _ask(rig: Rig, args: Mapping[str, object], cwd: Path | None = None) -> dict[str, object]:
    return structured(asyncio.run(rig.call("headwaters_ask", args, cwd)))


def test_ask_answers_over_a_path_without_sending_the_file_to_the_host(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "p", {"util.py": f"# {MARKER}\ndef retry():\n    pass\n"})
    rig = Rig(tmp_path, reply="The retry logic lives in retry().")
    result = asyncio.run(
        rig.call("headwaters_ask", {"question": "where is retry?", "paths": ["util.py"]}, repo)
    )
    data = structured(result)
    assert data["ok"] is True
    assert data["answer"] == "The retry logic lives in retry()."
    assert data["relay"] == "summarize_ok"
    assert data["result_uri"].startswith("headwaters://result/")  # type: ignore[attr-defined]
    (sent,) = rig.backend.requests
    assert MARKER in json.dumps(list(sent.messages))
    assert MARKER not in text_of(result)
    assert MARKER not in json.dumps(data)


def test_ask_never_returns_a_diff_or_commands_even_if_the_model_writes_one(tmp_path: Path) -> None:
    data = _ask(Rig(tmp_path, reply=DIFF_ANSWER), {"question": CODE_TASK})
    assert "diffs" not in data
    assert "commands" not in data
    assert data["relay"] != "apply_diff"
    assert "```diff" in data["answer"]  # type: ignore[operator]  # shown to the user as text


def test_ask_uses_the_read_only_system_prompt(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _ask(rig, {"question": CODE_TASK})
    (sent,) = rig.backend.requests
    assert "read-only" in json.dumps(list(sent.messages))


def test_ask_without_paths_needs_no_workspace(tmp_path: Path) -> None:
    assert _ask(Rig(tmp_path), {"question": CODE_TASK})["ok"] is True


def test_ask_with_paths_and_no_workspace_is_a_structured_error(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_ask", {"question": "q", "paths": ["a.py"]}))
    data = structured(result)
    assert result.is_error is True
    assert data["error"]["code"] == "no_workspace"  # type: ignore[index]
    assert data["error"]["fix"]  # type: ignore[index]


def test_ask_stores_its_detail_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path, reply="42")
    _ask(rig, {"question": CODE_TASK})
    (detail,) = list(rig.context.store._items.values())
    assert detail["tool"] == "ask"
    assert detail["answer"] == "42"
