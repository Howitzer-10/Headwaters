"""Mock dispatchers and the in-process MCP backend (HDW-208). No real model is called."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from mcp import Client

from bench.host_eval.models import HostProfile, RelayAction, Task, ToolCall, ToolSpec
from headwaters.mcp.meta import META_CLIENT, META_CWD, MORE_TOOL

__all__ = ["ClientBackend", "ScriptedDispatcher", "relay_by_contract"]

Mutate = Callable[[Task, ToolCall], ToolCall | None]


def relay_by_contract(result: Mapping[str, Any]) -> RelayAction:
    """What a host that follows the ``relay`` field does."""
    relay = result.get("relay")
    if relay == "apply_diff":
        return "apply_diff"
    return "summarize" if relay == "summarize_ok" else "show_verbatim"


class ScriptedDispatcher:
    """Follows the gold arguments. ``mutate`` degrades a call; ``relay_fn`` degrades the relay."""

    def __init__(
        self,
        id: str,
        *,
        mutate: Mutate | None = None,
        relay_fn: Callable[[Mapping[str, Any]], RelayAction] = relay_by_contract,
    ) -> None:
        self.id = id
        self._mutate = mutate
        self._relay_fn = relay_fn

    async def choose(
        self, profile: HostProfile, task: Task, tools: Sequence[ToolSpec]
    ) -> ToolCall | None:
        call = ToolCall(task.expected_tool, dict(task.gold_arguments))
        return self._mutate(task, call) if self._mutate else call

    async def relay(
        self, profile: HostProfile, task: Task, result: Mapping[str, Any]
    ) -> RelayAction:
        return self._relay_fn(result)


class ClientBackend:
    """Talks to a real server through an in-process MCP ``Client``.

    The host profile's id is sent as ``_meta`` ``headwaters/client`` so the server tells hosts
    apart; a profile with ``sees_advanced`` calls ``headwaters_more`` once before listing.
    """

    def __init__(self, client: Client, cwd: str | None = None) -> None:
        self._client = client
        self._cwd = cwd
        self._enabled: set[str] = set()

    def _meta(self, profile: HostProfile) -> Any:
        meta = {META_CLIENT: profile.id}
        if self._cwd:
            meta[META_CWD] = self._cwd
        return meta

    async def list_tools(self, profile: HostProfile) -> list[ToolSpec]:
        meta = self._meta(profile)
        if profile.sees_advanced and profile.id not in self._enabled:
            await self._client.call_tool(MORE_TOOL, {}, meta=meta)
            self._enabled.add(profile.id)
        listed = await self._client.list_tools(meta=meta, cache_mode="bypass")
        return [ToolSpec(t.name, t.description or "", dict(t.input_schema)) for t in listed.tools]

    async def call(self, profile: HostProfile, call: ToolCall) -> dict[str, Any]:
        result = await self._client.call_tool(
            call.tool, dict(call.arguments), meta=self._meta(profile)
        )
        return dict(result.structured_content or {})
