"""HDW-204 / FU-203-1: files and images by path go through the workspace rules (HDW-203)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from tests.frontdoor_support import CODE_TASK, Rig, make_repo, structured, text_of

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHXu1rAAAAABJRU5ErkJggg=="
)
FILES = {
    "a.py": "x = 1\n",
    ".env": "TOKEN=zq-secret\n",
    "sub/b.py": "y = 2\n",
    ".gitignore": "gen/\n",
}


def _proj(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "proj", FILES)


def _statuses(data: dict[str, Any]) -> dict[str, str]:
    return {f["path"]: f["status"] for f in data["files"]}


def test_paths_outside_the_workspace_are_refused_without_reading(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE-MARKER", encoding="utf-8")
    rig = Rig(tmp_path)
    args = {"task": "t", "paths": ["a.py", "../outside.txt", str(outside)]}
    stats = _statuses(structured(asyncio.run(rig.call("headwaters_do", args, proj))))
    assert stats["a.py"] == "included"
    assert stats["../outside.txt"] == "invalid"  # traversal is caught before it is resolved
    assert stats[str(outside)] == "outside"
    assert "OUTSIDE-MARKER" not in json.dumps(list(rig.backend.requests[0].messages))


def test_secret_and_ignored_files_are_never_sent_to_the_model(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    (proj / "gen").mkdir()
    (proj / "gen" / "o.py").write_text("GEN-MARKER = 1\n", encoding="utf-8")
    rig = Rig(tmp_path)
    paths = ["a.py", ".env", "gen/o.py"]
    result = asyncio.run(rig.call("headwaters_ask", {"question": "q", "paths": paths}, proj))
    stats = _statuses(structured(result))
    assert stats[".env"] == "ignored"
    assert stats["gen/o.py"] == "ignored"
    sent = json.dumps(list(rig.backend.requests[0].messages))
    assert "zq-secret" not in sent
    assert "GEN-MARKER" not in sent


def test_when_nothing_is_readable_the_error_lists_why_and_no_model_is_called(
    tmp_path: Path,
) -> None:
    proj = _proj(tmp_path)
    rig = Rig(tmp_path)
    args = {"task": "t", "paths": [".env", "nope.py"]}
    result = asyncio.run(rig.call("headwaters_do", args, proj))
    data = structured(result)
    assert result.is_error is True
    assert data["error"]["code"] == "no_readable_files"
    assert _statuses(data) == {".env": "ignored", "nope.py": "missing"}
    assert rig.backend.requests == []


def test_too_many_paths_is_refused(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    args = {"task": "t", "paths": [f"f{n}.py" for n in range(101)]}
    result = asyncio.run(Rig(tmp_path).call("headwaters_do", args, proj))
    assert structured(result)["error"]["code"] == "too_many_paths"


def test_an_image_needs_a_vision_model_and_is_not_silently_dropped(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    (proj / "shot.png").write_bytes(PNG)
    rig = Rig(tmp_path)  # the seeded local model is text-only
    args = {"task": "what is this", "images": ["shot.png"]}
    result = asyncio.run(rig.call("headwaters_do", args, proj))
    assert structured(result)["ok"] is False
    assert result.is_error is True
    assert rig.backend.requests == []


def test_a_non_image_file_is_not_sent_as_an_image(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    args = {"task": "t", "images": ["a.py"]}
    data = structured(asyncio.run(Rig(tmp_path).call("headwaters_do", args, proj)))
    assert data["error"]["code"] == "no_readable_images"
    assert _statuses(data) == {"a.py": "not_an_image"}


def test_the_text_never_echoes_file_contents(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    rig = Rig(tmp_path, reply="done")
    result = asyncio.run(rig.call("headwaters_ask", {"question": "q", "paths": ["a.py"]}, proj))
    assert "x = 1" not in text_of(result)


def test_the_project_rule_can_only_tighten(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    (proj / ".headwaters.yaml").write_text("privacy: local\n", encoding="utf-8")
    rig = Rig(tmp_path, cloud=True)
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK}, proj)))
    assert data["tier"] == "local"
    assert rig.cloud_calls == 0
    loosen = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "cloud"}, proj))
    assert structured(loosen)["error"]["code"] == "project_rule_conflict"


def test_an_invalid_project_file_is_a_clear_error(tmp_path: Path) -> None:
    proj = _proj(tmp_path)
    (proj / ".headwaters.yaml").write_text("privacy: nonsense\n", encoding="utf-8")
    result = asyncio.run(Rig(tmp_path).call("headwaters_do", {"task": "t"}, proj))
    assert structured(result)["error"]["code"] == "bad_project_config"
