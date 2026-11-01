"""HDW-205: pin_model, feedback, job_status and job_result."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from headwaters.core.rules import Rules
from headwaters.core.state_misc import create_job, kv_get, update_job
from headwaters.mcp.tools.status import PIN_KEY
from tests.cli_support import add_model
from tests.frontdoor_support import Rig, structured


def _call(rig: Rig, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return structured(asyncio.run(rig.call(name, args or {})))


def test_pin_model_persists_and_is_used_by_later_calls(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    add_model(rig.db, "other:7b", "local", 60.0, agentic=60.0, warm=True, size_vram_gb=5.0)
    data = _call(rig, "headwaters_pin_model", {"model": "other:7b"})
    assert data["pinned_model"] == "other:7b"
    assert kv_get(rig.db, PIN_KEY) == "other:7b"
    answer = structured(asyncio.run(rig.call("headwaters_ask", {"question": "hi"})))
    assert answer["model"] == "other:7b"
    assert _call(rig, "headwaters_status")["pinned_model"] == "other:7b"


def test_a_pin_can_be_cleared_and_an_unknown_model_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _call(rig, "headwaters_pin_model", {"model": "qwen:14b"})
    cleared = _call(rig, "headwaters_pin_model", {})
    assert cleared["pinned_model"] is None
    assert kv_get(rig.db, PIN_KEY) is None
    refused = asyncio.run(rig.call("headwaters_pin_model", {"model": "nope:1b"}))
    assert structured(refused)["error"]["code"] == "unknown_model"
    assert refused.is_error is True


def test_a_pin_cannot_widen_a_locked_scope(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(private_lock=True), cloud=True)
    _call(rig, "headwaters_pin_model", {"model": "vendor/free:free"})
    result = asyncio.run(rig.call("headwaters_ask", {"question": "hi"}))
    assert result.is_error is True  # the hard filter refuses the pin; nothing went to cloud
    assert rig.cloud_calls == 0


def test_feedback_is_recorded_on_a_real_decision(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    answer = structured(asyncio.run(rig.call("headwaters_ask", {"question": "hi"})))
    decision_id = answer["decision_id"]
    assert isinstance(decision_id, int)
    data = _call(rig, "headwaters_feedback", {"decision_id": decision_id, "rating": "down"})
    assert data["recorded"] is True
    with rig.db.read() as c:
        assert c.execute("SELECT user_feedback FROM outcomes").fetchone()[0] == "down"


def test_feedback_for_an_unknown_decision_or_a_bad_rating_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    unknown = asyncio.run(rig.call("headwaters_feedback", {"decision_id": 999, "rating": "up"}))
    assert structured(unknown)["error"]["code"] == "unknown_decision"
    bad = asyncio.run(rig.call("headwaters_feedback", {"decision_id": 1, "rating": "meh"}))
    assert structured(bad)["error"]["code"] == "bad_rating"


def test_job_status_and_result_for_a_known_and_an_unknown_job(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    create_job(rig.db, "j1", "running")
    update_job(rig.db, "j1", progress=0.5)
    status = _call(rig, "headwaters_job_status", {"job_id": "j1"})
    assert (status["status"], status["progress"]) == ("running", 0.5)
    assert _call(rig, "headwaters_job_result", {"job_id": "j1"})["result"] is None
    update_job(rig.db, "j1", status="done", result_ref="headwaters://result/abc")
    done = _call(rig, "headwaters_job_result", {"job_id": "j1"})
    assert done["result"] == {"result_uri": "headwaters://result/abc"}
    missing = asyncio.run(rig.call("headwaters_job_status", {"job_id": "nope"}))
    assert structured(missing)["error"]["code"] == "unknown_job"
    assert missing.is_error is True
