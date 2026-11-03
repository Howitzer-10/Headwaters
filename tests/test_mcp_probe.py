"""doctor-host-probe: the live probe reads the structured result the SDK actually exposes.

mcp 2.3 exposes ``structured_content`` (snake_case) and these tools render prose, so the probe's
``tool_call`` and ``scope`` rows failed on a healthy install while it looked only for
``structuredContent``. The fakes here carry the snake_case field only, so the bug cannot return
silently.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.mcp import link
from headwaters.mcp.probe import _answered, _payload, run

STATUS = {
    "ok": True,
    "version": "0.1.0",
    "scope": {"default_scope": "local", "effective": "local"},
}
SCOPE = {"ok": True, "changed": [], "scope": {"default_scope": "local", "effective": "local"}}
PROSE = "Scope: local (tiers: local); default local, strategy cost_saver"


def snake_only(payload: dict[str, Any]) -> Any:
    """A result shaped like mcp 2.3: only ``structured_content``, plus its prose rendering."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=PROSE)],
        is_error=False,
        structured_content=payload,
    )


def test_the_status_payload_comes_from_the_snake_case_field() -> None:
    result = snake_only(STATUS)
    assert not hasattr(result, "structuredContent")
    assert _payload(result) == STATUS


def test_the_scope_payload_comes_from_the_snake_case_field() -> None:
    result = snake_only(SCOPE)
    assert not hasattr(result, "structuredContent")
    assert _payload(result) == SCOPE


def test_the_camel_case_field_still_works_for_older_shapes() -> None:
    result = SimpleNamespace(content=[], is_error=False, structuredContent=STATUS)
    assert _payload(result) == STATUS


def test_a_json_text_block_is_the_last_resort() -> None:
    result = SimpleNamespace(content=[SimpleNamespace(text='{"ok": true}')], is_error=False)
    assert _payload(result) == {"ok": True}


def test_prose_only_is_an_answer_but_not_a_payload() -> None:
    result = SimpleNamespace(content=[SimpleNamespace(text=PROSE)], is_error=False)
    assert _payload(result) == {}
    assert _answered(result) is True


class FakeClient:
    def __init__(self, results: dict[str, Any]) -> None:
        self._results = results
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=[SimpleNamespace(name="headwaters_do")])

    async def call_tool(self, name: str, arguments: dict[str, Any], meta: Any = None) -> Any:
        self.calls.append((name, arguments))
        return self._results[name]


def fake_link(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    async def ensure_daemon() -> Any:
        return object()

    @asynccontextmanager
    async def open_daemon_client(daemon: Any) -> AsyncIterator[FakeClient]:
        yield client

    monkeypatch.setattr(link, "ensure_daemon", ensure_daemon)
    monkeypatch.setattr(link, "open_daemon_client", open_daemon_client)


def rows_of(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in report["checks"]}


def test_both_live_rows_pass_when_the_sdk_exposes_snake_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        {"headwaters_status": snake_only(STATUS), "headwaters_scope": snake_only(SCOPE)}
    )
    fake_link(monkeypatch, client)
    report = asyncio.run(run("cline"))
    assert report["ok"] is True
    rows = rows_of(report)
    assert rows["round_trip"]["ok"] is True
    assert rows["tool_call"] == {
        "name": "tool_call",
        "ok": True,
        "detail": "headwaters_status answered (0.1.0)",
    }
    assert rows["scope"] == {"name": "scope", "ok": True, "detail": "effective scope local"}


def test_a_prose_only_scope_answer_fails_the_scope_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prose = SimpleNamespace(content=[SimpleNamespace(text=PROSE)], is_error=False)
    client = FakeClient({"headwaters_status": snake_only(STATUS), "headwaters_scope": prose})
    fake_link(monkeypatch, client)
    report = asyncio.run(run("cline"))
    rows = rows_of(report)
    assert rows["tool_call"]["ok"] is True  # the daemon answered without an error
    assert rows["scope"]["ok"] is False  # but it did not report the scope as data
    assert rows["scope"]["detail"] == "headwaters_scope did not report an effective scope"
    assert report["ok"] is False


def test_an_error_result_fails_the_tool_call_row(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = SimpleNamespace(content=[SimpleNamespace(text=PROSE)], is_error=True)
    client = FakeClient({"headwaters_status": failed, "headwaters_scope": snake_only(SCOPE)})
    fake_link(monkeypatch, client)
    report = asyncio.run(run("cline"))
    assert rows_of(report)["tool_call"]["ok"] is False
    assert report["ok"] is False
