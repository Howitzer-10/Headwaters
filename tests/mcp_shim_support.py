"""Fakes for the shim tests (HDW-202): a stand-in for the daemon-side MCP client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import types

from headwaters.core.daemon_client import DaemonClient
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from headwaters.mcp.link import DaemonLink

INFO = DaemonInfo(77, 1.0, "0.0.0", DaemonPorts(4002))


class FakeUpstream:
    """Stands in for the MCP client connected to the daemon."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.metas: list[Any] = []
        self.list_metas: list[Any] = []
        self.reads: list[str] = []
        self.fail: Exception | None = None
        self.tool_description = "status"

    async def list_tools(
        self, *, cursor: str | None = None, meta: Any = None, cache_mode: str = "use"
    ) -> types.ListToolsResult:
        self.list_metas.append(meta)
        if self.fail is not None:
            raise self.fail
        tool = types.Tool(
            name="headwaters_status",
            description=self.tool_description,
            input_schema={"type": "object"},
        )
        return types.ListToolsResult(tools=[tool])

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None, *, meta: Any = None
    ) -> types.CallToolResult:
        self.calls.append((name, arguments))
        self.metas.append(meta)
        if self.fail is not None:
            raise self.fail
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])

    async def list_resources(self, *, cursor: str | None = None) -> types.ListResourcesResult:
        if self.fail is not None:
            raise self.fail
        return types.ListResourcesResult(resources=[])

    async def list_resource_templates(
        self, *, cursor: str | None = None
    ) -> types.ListResourceTemplatesResult:
        if self.fail is not None:
            raise self.fail
        template = types.ResourceTemplate(name="r", uri_template="headwaters://result/{result_id}")
        return types.ListResourceTemplatesResult(resource_templates=[template])

    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        self.reads.append(uri)
        if self.fail is not None:
            raise self.fail
        content = types.TextResourceContents(uri=uri, text="{}", mime_type="application/json")
        return types.ReadResourceResult(contents=[content])


def make_link(upstreams: list[FakeUpstream], ensured: list[int] | None = None) -> DaemonLink:
    """A link whose every (re)connection hands out the next fake upstream."""

    async def ensure() -> DaemonClient:
        if ensured is not None:
            ensured.append(1)
        return DaemonClient(INFO, "t")

    @asynccontextmanager
    async def opener(daemon: DaemonClient) -> AsyncIterator[Any]:
        yield upstreams.pop(0)

    return DaemonLink(ensure, opener)
