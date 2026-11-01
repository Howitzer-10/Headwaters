"""HDW-204: ``headwaters_do`` through a real MCP client, against real temp git repos."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from tests.frontdoor_support import DIFF_ANSWER, Rig, make_repo, structured, text_of

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0,
    reason="git is not installed",
)

APP = "def run():\n    return 1\n"
MARKER = "zq-private-marker-9"
DIFF_ARGS = {"task": "return 2", "paths": ["app.py"], "want": "diff"}


def _repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "proj", {"app.py": APP, "notes.md": f"# notes {MARKER}\n"})


def _do(rig: Rig, args: Mapping[str, object], cwd: Path | None = None) -> dict[str, object]:
    return structured(asyncio.run(rig.call("headwaters_do", args, cwd)))


def test_a_diff_is_produced_against_a_real_repo_and_applies(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    data = _do(Rig(tmp_path, reply=DIFF_ANSWER), DIFF_ARGS, repo)
    assert data["ok"] is True
    diffs = data["diffs"]
    assert isinstance(diffs, list)
    assert len(diffs) == 1
    assert diffs[0].startswith("--- a/app.py")
    assert data["verified"] is True
    assert data["verify_status"] == "pass"
    assert data["relay"] == "apply_diff"
    applied = subprocess.run(  # the diff really applies to the user's files
        ["git", "-C", str(repo), "apply", "--check", "-"],
        input=diffs[0].encode(),
        capture_output=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stderr


def test_the_file_is_sent_to_the_model_not_to_the_host(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    rig = Rig(tmp_path, reply=DIFF_ANSWER)
    args = {**DIFF_ARGS, "paths": ["app.py", "notes.md"]}
    result = asyncio.run(rig.call("headwaters_do", args, repo))
    (sent,) = rig.backend.requests
    assert MARKER in json.dumps(list(sent.messages))  # the model got the file
    assert MARKER not in text_of(result)  # the host did not
    assert MARKER not in json.dumps(structured(result))
    note = structured(result)["files"][0]
    assert note["path"] == "app.py"
    assert note["status"] == "included"
    assert "text" not in note
    assert "content" not in note


def test_a_diff_that_does_not_apply_fails_verification_and_is_not_relayed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    bad = DIFF_ANSWER.replace("return 1", "return 99")  # context that is not in the file
    data = _do(Rig(tmp_path, reply=bad), DIFF_ARGS, repo)
    assert data["verified"] is False
    assert data["verify_status"] == "fail"
    assert any("diff_does_not_apply" in r for r in data["verify_reasons"])  # type: ignore[attr-defined]
    assert data["relay"] == "verbatim"


def test_a_diff_without_a_workspace_is_never_relayed_as_apply_diff(tmp_path: Path) -> None:
    data = _do(Rig(tmp_path, reply=DIFF_ANSWER), {"task": "x"})
    assert data["diffs"]
    assert data["relay"] == "verbatim"  # nothing proved it applies


def test_plain_prose_may_be_summarized_and_code_must_be_shown_verbatim(tmp_path: Path) -> None:
    prose = _do(Rig(tmp_path, reply="It works by caching."), {"task": "explain the cache"})
    assert prose["relay"] == "summarize_ok"
    code = _do(Rig(tmp_path, reply="Here:\n```python\nx = 1\n```\n"), {"task": "set x"})
    assert code["relay"] == "verbatim"
    assert code["verified"] is True  # the fence parsed


def test_commands_are_split_from_the_prose(tmp_path: Path) -> None:
    reply = "Run this:\n```bash\npytest -q\n```\nDone."
    assert _do(Rig(tmp_path, reply=reply), {"task": "t"})["commands"] == ["pytest -q"]


def test_a_placeholder_answer_fails_verification(tmp_path: Path) -> None:
    reply = "```python\nAPI = 'your_api_key'\n```\n"
    data = _do(Rig(tmp_path, reply=reply), {"task": "t"})
    assert data["verify_status"] == "fail"
    assert data["relay"] == "verbatim"
