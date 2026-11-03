"""HDW-205: recommend_model, should_switch_host, list_local_models, quota_status."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.quotas import model_subject
from headwaters.core.rules import Rules
from headwaters.core.state_limits import record_wall
from headwaters.installer.names import OPENCODE_PROVIDER_ID
from tests.cli_support import add_model
from tests.frontdoor_support import Rig, make_repo, structured, text_of


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


def test_recommend_uses_the_installed_provider_id_for_a_local_model(tmp_path: Path) -> None:
    data = _call(Rig(tmp_path), "headwaters_recommend_model", {"task": "write a function"})
    command = next(s["command"] for s in data["switch"] if s["host"] == "opencode")
    assert command == f"opencode --model {OPENCODE_PROVIDER_ID}/qwen:14b"


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


def test_quota_status_lists_local_models_and_names_the_wall(tmp_path: Path) -> None:
    """FU-205-9: local models are in the answer; a wall's provider and key scope are separate."""
    rig = Rig(tmp_path, cloud=True)
    record_wall(rig.db, "provider:openrouter:paid", "exhausted", until=time.time() + 60)
    data = _call(rig, "headwaters_quota_status")
    wall = data["walls"][0]
    assert wall["label"] == "OpenRouter: paid credits"
    assert (wall["provider"], wall["credit"]) == ("OpenRouter", "paid")
    local = next(row for row in data["models"] if row["tier"] == "local")
    assert local["model"] == "qwen:14b"
    assert local["status"] == "local"  # no provider quota is spent, but it is what is left
    assert local["warm"] is True


def _switch(rig: Rig, current: str) -> dict[str, Any]:
    return _call(rig, "headwaters_should_switch_host", {"current_model": current, "scope": "local"})


def test_should_switch_matches_a_wall_through_a_catalog_alias(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    record_wall(rig.db, model_subject("claude-sonnet-4-5"), "exhausted", until=time.time() + 600)
    data = _switch(rig, "openrouter/claude-sonnet-4-5-20250929")
    assert data["severity"] == "warn"
    assert "has a wall" in data["reason"]


def test_should_switch_does_not_match_a_wall_by_suffix(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    until = time.time() + 600
    record_wall(rig.db, model_subject("vendor/x"), "exhausted", until=until)
    record_wall(rig.db, "provider:x", "rate_limit", until=until)
    record_wall(rig.db, "key:x", "rate_limit", until=until)
    data = _switch(rig, "other/x")
    assert data["severity"] == "info"
    assert "has a wall" not in data["reason"]


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0,
    reason="git is not installed",
)
def test_recommend_sizes_the_request_by_paths_without_returning_their_text(
    tmp_path: Path,
) -> None:
    marker = "zq-recommend-marker-9"
    repo = make_repo(
        tmp_path / "p", {"big.py": f"# {marker}\n" + "x = 1\n" * 4000, "tiny.py": "y = 2\n"}
    )
    rig = Rig(tmp_path)
    args = {"task": "refactor the parser", "scope": "local"}
    small = structured(asyncio.run(rig.call("headwaters_recommend_model", args, repo)))
    result = asyncio.run(
        rig.call("headwaters_recommend_model", {**args, "paths": ["big.py"]}, repo)
    )
    big = structured(result)
    assert big["ok"] is True
    assert not any("Sized with" in n for n in small["notes"])
    (sized,) = (n for n in big["notes"] if "Sized with" in n)
    assert int(sized.split("about ")[1].split(" ")[0]) > 1000
    assert marker not in json.dumps(big)
    assert marker not in text_of(result)
    assert rig.backend.requests == []


def test_recommend_with_unreadable_paths_is_a_structured_error(tmp_path: Path) -> None:
    repo = tmp_path / "p"
    repo.mkdir()
    rig = Rig(tmp_path)
    data = structured(
        asyncio.run(
            rig.call("headwaters_recommend_model", {"task": "x", "paths": ["missing.py"]}, repo)
        )
    )
    assert data["ok"] is False
    assert data["error"]["code"] == "no_readable_files"
