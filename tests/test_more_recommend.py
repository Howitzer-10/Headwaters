"""HDW-205: recommend_model, should_switch_host, list_local_models, quota_status."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from headwaters.core.quotas import model_subject
from headwaters.core.rules import Rules
from headwaters.core.state_limits import record_wall
from tests.cli_support import add_model
from tests.frontdoor_support import Rig, structured, text_of


def _call(rig: Rig, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return structured(asyncio.run(rig.call(name, args or {})))


def test_recommend_model_names_the_pick_and_the_switch_commands(tmp_path: Path) -> None:
    rig = Rig(tmp_path, local_score=40.0)  # the local model is below the floor: cloud wins
    add_model(
        rig.db,
        "opencode/big-free",
        "free_cloud",
        99.0,
        agentic=99.0,
        ctx=131072,
        provider="opencode",
    )
    data = _call(
        rig, "headwaters_recommend_model", {"task": "refactor the parser", "scope": "free"}
    )
    assert data["ok"] is True
    assert data["model"] == "opencode/big-free"
    commands = {s["host"]: s["command"] for s in data["switch"]}
    assert commands["opencode"] == "opencode --model opencode/big-free"
    assert "/models" in commands["opencode (running)"]
    assert commands["any host using the Headwaters gateway"] == "set the model to headwaters/free"
    assert any("dry run" in n for n in data["notes"])


def test_recommend_uses_the_ollama_provider_id_for_a_local_model(tmp_path: Path) -> None:
    data = _call(Rig(tmp_path), "headwaters_recommend_model", {"task": "write a function"})
    command = next(s["command"] for s in data["switch"] if s["host"] == "opencode")
    assert command == "opencode --model ollama/qwen:14b"


def test_recommend_calls_no_model_and_writes_no_decision(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _call(rig, "headwaters_recommend_model", {"task": "write a function"})
    assert rig.backend.requests == []
    with rig.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0


def test_recommend_in_a_locked_scope_never_offers_a_cloud_model(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(private_lock=True), cloud=True)
    data = _call(rig, "headwaters_recommend_model", {"task": "write a function"})
    assert data["tier"] == "local"
    assert all("vendor" not in s["model_id"] for s in data["switch"])


def test_recommend_with_no_usable_model_is_a_structured_refusal(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    with rig.db.write() as c:
        c.execute("DELETE FROM models")
    result = asyncio.run(rig.call("headwaters_recommend_model", {"task": "x"}))
    data = structured(result)
    assert result.is_error is True
    assert data["ok"] is False
    assert data["error"]["code"]


def test_recommend_results_never_hold_the_task_text(tmp_path: Path) -> None:
    result = asyncio.run(
        Rig(tmp_path).call("headwaters_recommend_model", {"task": "PRIVATE_TASK_WORDS_12345"})
    )
    assert "PRIVATE_TASK_WORDS_12345" not in json.dumps(result.structured_content)
    assert "PRIVATE_TASK_WORDS_12345" not in text_of(result)


def test_should_switch_says_no_when_the_host_is_already_right(tmp_path: Path) -> None:
    data = _call(Rig(tmp_path), "headwaters_should_switch_host", {"current_model": "qwen:14b"})
    assert data["switch"] is False


def test_should_switch_warns_when_the_hosts_model_has_a_wall(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    record_wall(rig.db, model_subject("vendor/paid"), "exhausted", until=time.time() + 600)
    data = _call(
        rig, "headwaters_should_switch_host", {"current_model": "vendor/paid", "scope": "local"}
    )
    assert data["switch"] is True
    assert data["severity"] == "warn"
    assert data["recommended"]["model"] == "qwen:14b"


def test_list_local_models_shows_fit_and_the_gpu(tmp_path: Path) -> None:
    data = _call(Rig(tmp_path), "headwaters_list_local_models")
    (model,) = data["models"]
    assert model["model"] == "qwen:14b"
    assert model["fit"] in ("fits", "offload")
    assert model["warm"] is True
    assert data["gpu"]["total_gb"] == 16.0


def test_quota_status_lists_walls_and_counters(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    record_wall(rig.db, model_subject("vendor/free:free"), "rate_limit", until=time.time() + 60)
    data = _call(rig, "headwaters_quota_status")
    assert data["walls"][0]["kind"] == "rate_limit"
    assert 0 < data["walls"][0]["eta_s"] <= 60
