"""HDW-202 accept-when: stdout carries JSON-RPC and nothing else (in-process; live test is slow)."""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import pytest

from headwaters.core.stdio import guard_stdout
from headwaters.mcp.shim import build_shim_server, serve_stdio
from tests.mcp_shim_support import FakeUpstream, make_link

REQUESTS = (
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25",'
    '"capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n'
    '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
)


class _Stdin:
    """Async stdin that stays open until the last reply is written, then reaches EOF."""

    def __init__(self, done: asyncio.Event) -> None:
        self._lines = iter(REQUESTS.splitlines(keepends=True))
        self._done = done

    def __aiter__(self) -> _Stdin:
        return self

    async def __anext__(self) -> str:
        line = next(self._lines, None)
        if line is not None:
            return line
        await asyncio.wait_for(self._done.wait(), timeout=20)
        raise StopAsyncIteration


def _serve(upstream: FakeUpstream) -> str:
    """Run the shim over a canned stdin; return everything that reached the real stdout."""

    async def scenario() -> None:
        link = make_link([upstream])
        server = build_shim_server(link)
        done = asyncio.Event()
        stdin = _Stdin(done)
        with guard_stdout(strict=False) as emit:
            print("stray text from a handler")  # a stray print inside the guard

            def watch(text: str) -> None:
                emit(text)
                if '"id":2' in text.replace(" ", ""):
                    done.set()

            await serve_stdio(server, watch, stdin=stdin)
        await link.close()

    real = sys.stdout
    sys.stdout = io.StringIO()
    try:
        asyncio.run(scenario())
        return sys.stdout.getvalue()
    finally:
        sys.stdout = real


def test_stdout_is_json_rpc_only_and_a_stray_print_goes_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    protocol = _serve(FakeUpstream())
    assert "stray text" not in protocol
    assert "stray text" in capsys.readouterr().err
    lines = [line for line in protocol.splitlines() if line.strip()]
    assert lines, "the shim wrote no protocol frames"
    messages = [json.loads(line) for line in lines]
    assert all(m["jsonrpc"] == "2.0" for m in messages)
    assert any(m.get("id") == 1 for m in messages)  # initialize answered
    tools = next(m for m in messages if m.get("id") == 2)["result"]["tools"]
    assert [t["name"] for t in tools] == ["headwaters_status"]


def test_non_ascii_text_survives_as_utf8() -> None:
    upstream = FakeUpstream()
    upstream.tool_description = "Gr\u00f6\u00dfe \u2013 \u65e5\u672c\u8a9e"
    protocol = _serve(upstream)
    assert "Gr\u00f6\u00dfe" in protocol


def test_shim_sources_never_print_to_stdout() -> None:
    root = Path(__file__).resolve().parents[1] / "headwaters" / "mcp"
    for name in ("shim.py", "link.py", "__main__.py"):
        assert "print(" not in (root / name).read_text(encoding="utf-8"), name
