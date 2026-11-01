"""HDW-205: headwaters_think routes to a reasoning-capable model in scope, never shows reasoning."""

from __future__ import annotations

import asyncio
from pathlib import Path

from headwaters.core.rules import Rules
from headwaters.core.state_models import ModelRow, ScoreRow, set_score, upsert_model
from tests.cli_support import LOCAL_ARCH
from tests.frontdoor_support import Rig, make_repo, structured, text_of


def _add(rig: Rig, mid: str, tier: str, *, thinks: bool, reasoning: float = 90.0) -> None:
    fields: dict[str, object] = {"arch": LOCAL_ARCH, "size_gb": 9.0} if tier == "local" else {}
    upsert_model(
        rig.db,
        ModelRow(
            id=mid,
            tier=tier,
            provider="ollama" if tier == "local" else "vendor",
            modalities=["text"],
            tools_ok=True,
            json_ok=True,
            ctx=32768,
            reasoning_ok=thinks,
            source="test",
            warm=tier == "local",
            **fields,  # type: ignore[arg-type]
        ),
    )
    for task in ("code", "chat", "agentic_tools", "reasoning"):
        set_score(rig.db, ScoreRow(mid, task, seed=reasoning))


def test_think_uses_a_reasoning_capable_model_and_returns_only_the_answer(tmp_path: Path) -> None:
    rig = Rig(tmp_path, reply="Because the lock order differs.")
    _add(rig, "think:14b", "local", thinks=True)
    result = asyncio.run(rig.call("headwaters_think", {"question": "why deadlock?"}))
    data = structured(result)
    assert data["ok"] is True
    assert data["model"] == "think:14b"
    assert data["answer"] == "Because the lock order differs."
    assert "reasoning" not in data  # the reasoning text is never part of the result
    assert rig.backend.requests[-1].options.reasoning is True  # reasoning was switched on


def test_think_skips_a_model_that_cannot_reason(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # seeds qwen:14b (reasoning_ok unknown: not capable)
    _add(rig, "plain:70b", "local", thinks=False, reasoning=99.0)
    _add(rig, "think:8b", "local", thinks=True, reasoning=70.0)
    data = structured(asyncio.run(rig.call("headwaters_think", {"question": "hard one"})))
    assert data["model"] == "think:8b"


def test_think_with_no_reasoning_model_in_scope_is_a_structured_refusal(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # only a model that is not known to reason
    result = asyncio.run(rig.call("headwaters_think", {"question": "hard one"}))
    data = structured(result)
    assert result.is_error is True
    assert data["ok"] is False
    assert data["error"]["code"]
    assert rig.cloud_calls == 0


def test_think_in_local_scope_never_builds_a_cloud_client(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(private_lock=True), cloud=True)
    _add(rig, "think:14b", "local", thinks=True)
    _add(rig, "cloud:free", "free_cloud", thinks=True, reasoning=99.0)
    data = structured(asyncio.run(rig.call("headwaters_think", {"question": "q"})))
    assert data["model"] == "think:14b"
    assert data["scope"] == "local"
    assert rig.cloud_calls == 0


def test_think_sends_named_files_to_the_model_not_the_host(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {"pool.py": "SECRET_TOKEN_VALUE = 1\n"})
    rig = Rig(tmp_path)
    _add(rig, "think:14b", "local", thinks=True)
    result = asyncio.run(
        rig.call("headwaters_think", {"question": "review", "paths": ["pool.py"]}, repo)
    )
    data = structured(result)
    assert data["files"][0]["status"] == "included"
    assert "SECRET_TOKEN_VALUE" not in text_of(result)
    assert "SECRET_TOKEN_VALUE" in str(rig.backend.requests[-1].messages)


def test_think_is_read_only_and_never_returns_a_diff(tmp_path: Path) -> None:
    rig = Rig(tmp_path, reply="```diff\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n```")
    _add(rig, "think:14b", "local", thinks=True)
    data = structured(asyncio.run(rig.call("headwaters_think", {"question": "q"})))
    assert data["relay"] != "apply_diff"
    assert "diffs" not in data
