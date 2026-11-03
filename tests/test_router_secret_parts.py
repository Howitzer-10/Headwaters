"""HDW-120: the secret scan covers every string leaf of every message."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.router_prep import scan_texts
from headwaters.core.router_types import RouteRequest
from headwaters.core.secrets_detect import SecretDetector
from headwaters.core.state import Database
from tests.executor_support import REPO_ROOT
from tests.net_spy import audit_egress, forbid_network
from tests.router_rig import Rig
from tests.router_support import OK
from tests.test_router import LOCAL, pool

SK = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"

USER: dict[str, Any] = {"role": "user", "content": f"my key is {SK}"}
TOOL_RESULT: dict[str, Any] = {
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": [{"type": "text", "text": f"API_KEY={SK}"}],
        }
    ],
}
OPENAI_CALL: dict[str, Any] = {
    "role": "assistant",
    "content": None,
    "tool_calls": [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "run", "arguments": json.dumps({"cmd": f"curl -H {SK}"})},
        }
    ],
}
ANTHROPIC_USE: dict[str, Any] = {
    "role": "assistant",
    "content": [
        {"type": "tool_use", "id": "t1", "name": "write", "input": {"file": {"body": SK}}},
    ],
}
SHAPES: dict[str, list[dict[str, Any]]] = {
    "user_text": [USER],
    "tool_result": [TOOL_RESULT],
    "openai_tool_call_arguments": [OPENAI_CALL],
    "anthropic_tool_use_input": [ANTHROPIC_USE],
}
INNOCENT: dict[str, Any] = {"role": "user", "content": "write a function that adds two numbers"}
READBACK: dict[str, dict[str, Any]] = {
    "content_part": {"messages": [INNOCENT, USER]},
    "tool_calls_arguments": {"messages": [INNOCENT, OPENAI_CALL]},
    "tool_use_input": {"messages": [INNOCENT, ANTHROPIC_USE]},
    "tool_result": {"messages": [INNOCENT, TOOL_RESULT]},
    "system_block": {"messages": [INNOCENT], "system": [{"type": "text", "text": f"tk {SK}"}]},
}


def stored(db: Database) -> list[dict[str, Any]]:
    """Every decision and outcome row, read straight back out of SQLite (FU-120-1)."""
    with db.read() as c:
        return [
            *(dict(r) for r in c.execute("SELECT * FROM decisions")),
            *(dict(r) for r in c.execute("SELECT * FROM outcomes")),
        ]


def detector() -> SecretDetector:
    return SecretDetector.from_catalog(load_catalog(REPO_ROOT / SEED_RELATIVE_PATH))


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_shape_is_detected(shape: str) -> None:
    request = RouteRequest(messages=[INNOCENT, *SHAPES[shape]])
    assert detector().scan_all(scan_texts(request))


def test_system_blocks_and_top_level_system_are_scanned() -> None:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": f"token {SK}"}]
    assert detector().scan_all(scan_texts(RouteRequest(messages=[INNOCENT], system=blocks)))
    sys_msg = {"role": "system", "content": [{"type": "text", "text": SK}]}
    assert detector().scan_all(scan_texts(RouteRequest(messages=[sys_msg, INNOCENT])))


def test_deeply_nested_input_is_still_scanned() -> None:
    nested: Any = SK
    for _ in range(45):
        nested = {"k": [nested]}
    use = {"role": "assistant", "content": [{"type": "tool_use", "name": "x", "input": nested}]}
    assert detector().scan_all(scan_texts(RouteRequest(messages=[INNOCENT, use])))


def test_clean_messages_give_no_matches_and_images_are_skipped() -> None:
    image = {
        "role": "user",
        "content": [{"type": "image", "source": {"type": "base64", "data": "A" * 200}}],
    }
    request = RouteRequest(messages=[INNOCENT, OPENAI_CALL_CLEAN, image])
    assert not detector().scan_all(scan_texts(request))
    assert all("A" * 200 not in t for t in scan_texts(request))


OPENAI_CALL_CLEAN: dict[str, Any] = {
    "role": "assistant",
    "content": None,
    "tool_calls": [{"id": "c", "type": "function", "function": {"name": "ls", "arguments": "{}"}}],
}


@pytest.mark.parametrize("shape", sorted(READBACK))
def test_a_secret_only_in_a_part_routes_local_without_network(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), secrets=detector())
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        result = rig.run(**READBACK[shape])
    assert result.decision.model == LOCAL
    assert rig.cloud_clients == 0
    assert not opened
    assert not remote
    assert SK not in repr(result.reasons)
    assert SK not in repr(result.decision)
    assert SK not in repr(result.attempts)


@pytest.mark.parametrize("shape", sorted(READBACK))
def test_the_stored_decision_and_outcome_rows_hold_no_secret(
    shape: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FU-120-1: the SQLite rows, not just the return value, are clean for every shape."""
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), secrets=detector())
    with audit_egress():
        opened = forbid_network(monkeypatch, allow_loopback=True)
        rig.run(**READBACK[shape])
    rows = stored(rig.db)
    assert len(rows) == 2  # one decision row, one outcome row
    for row in rows:
        for column, value in row.items():
            assert SK not in str(value), f"secret leaked into column {column}"
    assert not opened
