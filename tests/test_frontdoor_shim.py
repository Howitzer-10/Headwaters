"""HDW-204: the shim sends its folder with every call and forwards resources (FU-202-2)."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from mcp import Client, MCPError

from headwaters.mcp.link import DaemonUnavailable
from headwaters.mcp.meta import META_CWD
from headwaters.mcp.shim import build_shim_server, workdir
from headwaters.mcp.tools.context import ToolContext
from tests.frontdoor_support import CODE_TASK, Rig, make_repo, structured
from tests.mcp_shim_support import FakeUpstream, make_link


def test_every_tool_call_carries_the_shims_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    upstream = FakeUpstream()

    async def scenario() -> None:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            await client.call_tool("headwaters_do", {"task": "t"})
        await link.close()

    asyncio.run(scenario())
    assert upstream.metas[0][META_CWD] == os.getcwd() == workdir()


def test_resources_are_forwarded_to_the_daemon() -> None:
    upstream = FakeUpstream()

    async def scenario() -> tuple[list[str], list[str], str]:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            listed = await client.list_resources()
            templates = await client.list_resource_templates()
            read = await client.read_resource("headwaters://result/abc")
            body = read.contents[0].text  # type: ignore[union-attr]
        await link.close()
        return (
            [str(r.uri) for r in listed.resources],
            [t.uri_template for t in templates.resource_templates],
            body,
        )

    resources, templates, body = asyncio.run(scenario())
    assert resources == []
    assert templates == ["headwaters://result/{result_id}"]
    assert body == "{}"
    assert upstream.reads == ["headwaters://result/abc"]


def test_resource_calls_report_a_missing_daemon_as_a_clear_error() -> None:
    async def ensure() -> object:
        raise DaemonUnavailable("not running")

    from headwaters.mcp.link import DaemonLink

    async def scenario() -> list[str]:
        link = DaemonLink(ensure)  # type: ignore[arg-type]
        errors: list[str] = []
        async with Client(build_shim_server(link)) as client:
            for call in (
                client.list_resources(),
                client.list_resource_templates(),
                client.read_resource("headwaters://result/x"),
            ):
                try:
                    await call
                except MCPError as exc:
                    errors.append(str(exc))
        await link.close()
        return errors

    errors = asyncio.run(scenario())
    assert len(errors) == 3
    assert all("headwaters start" in e for e in errors)


def test_the_full_path_host_to_shim_to_tools_uses_the_hosts_folder(tmp_path: Path) -> None:
    """The shim's cwd reaches the tools: a path resolves in the project the host opened."""
    repo = make_repo(tmp_path / "proj", {"app.py": "def run():\n    return 1\n"})
    rig = Rig(tmp_path, reply="It returns 1.")

    class Upstream(FakeUpstream):
        async def call_tool(self, name, arguments=None, *, meta=None):  # type: ignore[no-untyped-def,override]
            async with rig.client() as inner:
                return await inner.call_tool(name, arguments, meta=meta)

    async def scenario() -> dict[str, object]:
        link = make_link([Upstream()])
        async with Client(build_shim_server(link)) as client:
            os.chdir(repo)
            result = await client.call_tool(
                "headwaters_ask", {"question": CODE_TASK, "paths": ["app.py"]}
            )
        await link.close()
        return structured(result)

    previous = Path.cwd()
    try:
        data = asyncio.run(scenario())
    finally:
        os.chdir(previous)
    assert data["ok"] is True
    assert data["files"][0]["status"] == "included"  # type: ignore[index]


def test_a_tool_context_without_an_engine_says_it_is_starting(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.context = ToolContext(engine=lambda: None, rules=lambda: rig.rules)
    from mcp.server import MCPServer

    from headwaters.mcp.endpoint import build_endpoint

    server = MCPServer(name="t", version="0")
    build_endpoint("0", server=server, tools=rig.context)

    async def go() -> dict[str, object]:
        async with Client(server) as client:
            return structured(await client.call_tool("headwaters_do", {"task": "t"}))

    data = asyncio.run(go())
    assert data["ok"] is False
    assert data["error"]["code"] == "starting_up"  # type: ignore[index]
