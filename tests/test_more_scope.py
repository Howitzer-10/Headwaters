"""HDW-205: headwaters_scope (tighten, loosen with and without a user) and headwaters_status."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp import types

from headwaters.core.rules import Rules
from tests.frontdoor_support import Rig, structured, text_of


async def _accept(context: Any, params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="accept", content={"confirm": True})


async def _decline(context: Any, params: Any) -> types.ElicitResult:
    return types.ElicitResult(action="decline")


def test_tightening_applies_at_once_and_status_shows_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path)  # default scope auto
    data = structured(asyncio.run(rig.call("headwaters_scope", {"scope": "local"})))
    assert data["ok"] is True
    assert data["changed"] == ["default_scope"]
    assert rig.edits == [("default_scope", "local")]
    assert data["scope"]["effective"] == "local"
    status = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert status["scope"]["default_scope"] == "local"  # the change is visible in status
    assert status["scope"]["tiers"] == ["local"]


def test_the_private_lock_can_be_turned_on_and_shows_in_status(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    data = structured(asyncio.run(rig.call("headwaters_scope", {"private_lock": True})))
    assert data["ok"] is True
    status = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert status["scope"]["private_lock"] is True
    assert "private_lock" in status["scope"]["locks"]


def test_the_strategy_can_be_set(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    asyncio.run(rig.call("headwaters_scope", {"strategy": "quality_first"}))
    assert rig.rules.strategy == "quality_first"


def test_loosening_without_a_way_to_ask_changes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local", private_lock=True))
    result = asyncio.run(rig.call("headwaters_scope", {"scope": "auto", "private_lock": False}))
    data = structured(result)
    assert result.is_error is True
    assert data["confirmation_required"] is True
    assert data["error"]["code"] == "confirmation_required"
    assert "headwaters scope auto" in data["user_command"]
    assert "headwaters private off" in data["user_command"]
    assert "Do not run it yourself" in text_of(result)
    assert rig.edits == []  # nothing was written
    assert rig.rules.private_lock is True


def test_loosening_with_a_user_who_accepts_is_applied(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local"))
    result = asyncio.run(rig.call("headwaters_scope", {"scope": "free"}, elicit=_accept))
    data = structured(result)
    assert data["ok"] is True
    assert rig.edits == [("default_scope", "free")]


def test_loosening_with_a_user_who_declines_changes_nothing(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local"))
    result = asyncio.run(rig.call("headwaters_scope", {"scope": "free"}, elicit=_decline))
    data = structured(result)
    assert data["confirmation_required"] is True
    assert "declined" in " ".join(data["notes"])
    assert rig.edits == []


def test_a_mixed_call_applies_nothing_until_the_loosening_is_confirmed(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="free"))
    # strategy is a ranking choice (applies); the wider scope needs the user.
    result = asyncio.run(
        rig.call("headwaters_scope", {"scope": "auto", "strategy": "quality_first"})
    )
    assert structured(result)["confirmation_required"] is True
    assert rig.edits == []


def test_a_scope_the_private_lock_overrides_is_reported(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(private_lock=True))
    data = structured(asyncio.run(rig.call("headwaters_scope", {"scope": "local"})))
    assert data["scope"]["effective"] == "local"
    assert "private_lock" in data["scope"]["locks"]


def test_asking_for_what_is_already_set_is_a_no_op(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local"))
    data = structured(asyncio.run(rig.call("headwaters_scope", {"scope": "local"})))
    assert data["ok"] is True
    assert data["changed"] == []
    assert rig.edits == []


def test_a_call_with_no_arguments_reports_the_current_state(tmp_path: Path) -> None:
    data = structured(asyncio.run(Rig(tmp_path).call("headwaters_scope", {})))
    assert data["ok"] is True
    assert data["changed"] == []
    assert data["scope"]["default_scope"] == "auto"


def test_an_invalid_rules_file_is_reported_not_raised(tmp_path: Path) -> None:
    from headwaters.core.rules_io import ConfigError

    rig = Rig(tmp_path)

    def broken(key: str, value: bool | str) -> tuple[Any, Any]:
        raise ConfigError(tmp_path / "rules.yaml", ["airplane: not a bool"])

    rig.context.edit_rules = broken
    result = asyncio.run(rig.call("headwaters_scope", {"scope": "local"}))
    assert structured(result)["error"]["code"] == "invalid_rules"
    assert result.is_error is True
