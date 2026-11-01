"""HDW-206: spend confirmation and scope loosening; the elicitation and its force-flag fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tests.run_support import (
    CODE_TASK,
    PAID,
    Rig,
    agree,
    deny,
    paid_rig,
    structured,
    text_of,
)


def _paid_calls(rig: Rig) -> int:
    return rig.cloud_calls


def test_a_call_over_the_limit_asks_the_user_and_runs_on_yes(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK}, elicit=agree)))
    assert data["ok"] is True
    assert data["model"] == PAID
    assert data["answer"] == "Plain answer."
    assert rig.cloud_calls == 1


def test_a_call_over_the_limit_that_the_user_declines_runs_nothing(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    result = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK}, elicit=deny))
    data = structured(result)
    assert data["ok"] is False
    assert data["error"]["code"] == "declined"
    assert result.is_error is True
    assert rig.cloud_calls == 0  # no cloud client was ever built
    assert "nothing was spent" in text_of(result)


def test_an_elicitation_the_user_cancels_is_a_no(tmp_path: Path) -> None:
    from mcp import types

    async def cancel(context: object, params: object) -> types.ElicitResult:
        return types.ElicitResult(action="cancel")

    rig = paid_rig(tmp_path)
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK}, elicit=cancel)))
    assert data["error"]["code"] == "declined"
    assert rig.cloud_calls == 0


def test_a_host_that_cannot_be_asked_gets_confirmation_required_and_nothing_runs(
    tmp_path: Path,
) -> None:
    rig = paid_rig(tmp_path)
    result = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK}))  # no elicitation support
    data = structured(result)
    assert data["ok"] is False
    assert data["confirmation_required"] is True
    assert result.is_error is True
    assert data["error"]["code"] == "confirmation_required"
    confirm = data["confirm"]
    assert confirm["reasons"] == ["spend"]
    assert confirm["model"] == PAID
    assert confirm["estimated_cost_usd"] > confirm["confirm_above_usd"] == 0.25
    assert confirm["retry_with"] == {"force": True}  # the exact follow-up
    assert "force: true" in text_of(result)
    assert rig.cloud_calls == 0  # nothing was spent
    assert data["answer"] is None


def test_the_force_flag_stands_in_when_there_is_nobody_to_ask(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    args = {"task": CODE_TASK, "force": True}
    data = structured(asyncio.run(rig.call("headwaters_do", args)))
    assert data["ok"] is True
    assert data["model"] == PAID
    assert rig.cloud_calls == 1


def test_force_does_not_skip_the_question_for_a_host_that_can_be_asked(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    args = {"task": CODE_TASK, "force": True}
    result = asyncio.run(rig.call("headwaters_do", args, elicit=deny))
    assert structured(result)["error"]["code"] == "declined"  # the user still had the say
    assert rig.cloud_calls == 0


def test_a_cheap_call_is_never_asked_about(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path, price_out=0.001)  # a few thousandths of a cent
    asked: list[int] = []

    async def spy(context: object, params: object):  # type: ignore[no-untyped-def]
        asked.append(1)
        return await agree(context, params)

    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK}, elicit=spy)))
    assert data["ok"] is True
    assert asked == []  # under the limit: no question, no flag


def test_a_free_or_local_call_is_never_asked_about(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert data["ok"] is True
    assert data["confirmation_required"] is False


def test_the_limit_is_the_users_setting(tmp_path: Path) -> None:
    high = paid_rig(tmp_path / "a", limit=1000.0)
    assert structured(asyncio.run(high.call("headwaters_do", {"task": CODE_TASK})))["ok"] is True
    zero = paid_rig(tmp_path / "b", limit=0.0)
    again = structured(asyncio.run(zero.call("headwaters_do", {"task": CODE_TASK})))
    assert again["confirmation_required"] is True


def test_the_input_schemas_show_force_and_async_but_not_the_internal_approval(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)

    async def listed() -> dict[str, dict[str, Any]]:
        async with rig.client() as client:
            return {t.name: dict(t.input_schema) for t in (await client.list_tools()).tools}

    schemas = asyncio.run(listed())
    for name in ("headwaters_do", "headwaters_ask", "headwaters_think"):
        props = set(schemas[name]["properties"])
        assert {"force", "async"} <= props
        assert "approval" not in props
        assert "approval" not in schemas[name].get("required", [])


def test_ask_and_think_are_guarded_the_same_way(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    asked = structured(asyncio.run(rig.call("headwaters_ask", {"question": CODE_TASK})))
    assert asked["confirmation_required"] is True
    assert asked["confirm"]["retry_with"] == {"force": True}
    thought = structured(asyncio.run(rig.call("headwaters_think", {"question": CODE_TASK})))
    assert thought["ok"] is False  # think needs a reasoning-capable model; none here, so refused
    assert rig.cloud_calls == 0
    assert asyncio.run(rig.call("headwaters_ask", {"question": CODE_TASK}, elicit=agree))
    assert rig.cloud_calls == 1
