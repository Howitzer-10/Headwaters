"""Progressive disclosure of the advanced tools (HDW-205, v3.4 B1).

Eight tools are always listed. The advanced ones stay out of ``tools/list`` until the host calls
``headwaters_more``; from then on that host sees them. A host is told apart by the id its shim
sends in the request ``_meta`` (``headwaters/client``, one random id per shim process). A client
that sends none (a direct HTTP client) shares the id ``anon``.

This is about the size of the list, not about safety: an advanced tool that is called without being
listed still runs, and none of them can widen a scope or a lock. The state is in memory, per daemon
run, so a daemon restart starts every host with the short list again.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

from headwaters.mcp.meta import META_CLIENT

__all__ = ["ANONYMOUS", "Disclosure", "host_id"]

ANONYMOUS = "anon"


def host_id(meta: Mapping[str, Any] | None) -> str:
    value = (meta or {}).get(META_CLIENT)
    return value if isinstance(value, str) and value.strip() else ANONYMOUS


class Disclosure:
    """Server middleware that hides the advanced tools from hosts that have not asked."""

    def __init__(self, enabled: set[str], advanced: Collection[str]) -> None:
        self._enabled = enabled
        self._advanced = frozenset(advanced)

    def enable(self, host: str) -> bool:
        """Show the advanced tools to ``host``. True when this changed what it sees."""
        if host in self._enabled:
            return False
        self._enabled.add(host)
        return True

    def enabled_for(self, host: str) -> bool:
        return host in self._enabled

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "tools/list" or not isinstance(result, dict):
            return result
        if host_id(ctx.meta) in self._enabled:
            return result
        tools = [t for t in result.get("tools", []) if t.get("name") not in self._advanced]
        return {**result, "tools": tools}
