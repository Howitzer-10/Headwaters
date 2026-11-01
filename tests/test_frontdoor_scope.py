"""HDW-204: local scope is honored through the tools, and below-floor shows its warning.

Accept when: "local scope is honored through the tools; a local-scope request below the floor
shows ``below_floor``, ``suggestions`` and the warning line in the tool result" (FU-123-2).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from headwaters.core.advice import BELOW_FLOOR_WARNING
from headwaters.core.rules import Rules
from headwaters.core.state_models import list_models, upsert_model
from tests.frontdoor_support import CODE_TASK, Rig, structured, text_of


def _local_args(**more: object) -> dict[str, object]:
    return {"task": CODE_TASK, "scope": "local", **more}


def test_local_scope_never_constructs_a_cloud_client(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)  # a better free cloud model exists, but scope is local
    data = structured(asyncio.run(rig.call("headwaters_do", _local_args())))
    assert data["ok"] is True
    assert data["tier"] == "local"
    assert data["model"] == "qwen:14b"
    assert data["scope"] == "local"
    assert rig.cloud_calls == 0


def test_ask_honors_local_scope_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    data = structured(
        asyncio.run(rig.call("headwaters_ask", {"question": CODE_TASK, "scope": "local"}))
    )
    assert data["tier"] == "local"
    assert rig.cloud_calls == 0


def test_a_private_lock_keeps_the_tool_local_even_when_cloud_is_asked_for(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True, rules=Rules(private_lock=True))
    result = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "cloud"}))
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "private_lock_conflict"
    assert "Private lock" in data["error"]["message"]
    assert result.is_error is True
    assert rig.cloud_calls == 0


def test_a_private_lock_alone_serves_the_local_model(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True, rules=Rules(private_lock=True))
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert data["tier"] == "local"
    assert rig.cloud_calls == 0


def test_the_lock_is_read_per_call_not_once_at_start(tmp_path: Path) -> None:
    rig = Rig(tmp_path, local_score=80.0, cloud=True)
    first = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert first["tier"] == "free_cloud"
    rig.rules = Rules(private_lock=True)  # the user turns Private Mode on while the daemon runs
    second = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert second["tier"] == "local"


def test_local_scope_with_no_usable_local_model_is_an_error_not_a_cloud_fallback(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, cloud=True)
    for row in list_models(rig.db):
        if row.tier == "local":
            upsert_model(rig.db, replace(row, ctx=1024))  # too small for any request
    result = asyncio.run(rig.call("headwaters_do", _local_args()))
    data = structured(result)
    assert result.is_error is True
    assert data["ok"] is False
    assert data["error"]["fix"]
    assert rig.cloud_calls == 0


def test_a_below_floor_local_request_shows_the_flag_suggestions_and_the_warning(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, local_score=60.0)  # the only local model is under the code floor
    result = asyncio.run(rig.call("headwaters_do", _local_args()))
    data = structured(result)
    assert data["ok"] is True
    assert data["below_floor"] is True
    codes = {s["code"] for s in data["suggestions"]}
    assert {"pull_model", "split_task", "use_cloud"} <= codes
    assert BELOW_FLOOR_WARNING in data["notes"]
    text = text_of(result)
    assert text.startswith(BELOW_FLOOR_WARNING)  # the warning is the first line
    assert data["relay"] == "verbatim"  # never summarized away
    assert rig.cloud_calls == 0


def test_below_floor_with_a_lock_never_suggests_cloud(tmp_path: Path) -> None:
    rig = Rig(tmp_path, local_score=60.0, rules=Rules(private_lock=True))
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert data["below_floor"] is True
    codes = {s["code"] for s in data["suggestions"]}
    assert {"pull_model", "split_task"} <= codes
    assert "use_cloud" not in codes


def test_an_answer_above_the_floor_has_no_warning(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_do", _local_args()))
    assert structured(result)["below_floor"] is False
    assert not text_of(result).startswith("Warning")
    assert BELOW_FLOOR_WARNING not in text_of(result)
