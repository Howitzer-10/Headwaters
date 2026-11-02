"""Carry the daemon's questions to the shim's own host (HDW-146, D9: a host that can be asked is
always asked).

The shim sits between a host and the shared daemon. The daemon asks the user to confirm a spend or
a wider scope with MCP elicitation. Two cases reach the shim's host:

- A host on a handshake-era protocol (before 2026-07-28, the common case today) can be sent a
  request in the middle of a tool call. The daemon client's elicitation callback
  (``forward_elicitation``) puts the daemon's form to that host and returns the answer.
- A host on 2026-07-28 or later cannot be sent requests; the daemon's ``InputRequiredResult`` goes
  back to it unchanged and the host retries the call with its answers. The shim keeps no state for
  this: the daemon's own ``request_state`` carries it.

A host that does not declare elicitation is never asked: the daemon client does not declare it
either, so the daemon takes its ``force`` text fallback. No question text is logged here.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from mcp import Client, MCPError, types
from mcp.client.session import ClientRequestContext
from mcp.server import ServerRequestContext
from mcp.server.session import ServerSession
from mcp.shared.dispatcher import ProgressFnT

from headwaters.core.logging import get_logger

__all__ = [
    "MODERN_PROTOCOL",
    "HostLine",
    "asking_through",
    "call_tool_round",
    "forward_elicitation",
    "host_can_ask",
    "host_is_modern",
]


# The first protocol revision without server-to-client requests. Revisions are ISO dates, so a
# plain string comparison orders them.
MODERN_PROTOCOL = "2026-07-28"

_log = get_logger("mcp.forward")


@dataclass(frozen=True)
class HostLine:
    """The shim's host session and the host request a question belongs to."""

    session: ServerSession
    request_id: types.RequestId | None


_HOST: ContextVar[HostLine | None] = ContextVar("headwaters_host_line", default=None)
# A shim has one host. When the SDK runs the callback outside the request's own task (a question
# the daemon sends in the middle of a call), the variable is not visible there and the newest line
# stands in: it is the same session, only the related request id may be less exact.
_LATEST: list[HostLine] = []


def host_can_ask(session: ServerSession) -> bool:
    """True when the shim's host declared the elicitation capability (the daemon rule, D9)."""
    caps = session.client_capabilities
    return caps is not None and caps.elicitation is not None


def host_is_modern(session: ServerSession) -> bool:
    """True when the host's protocol cannot take a request from the shim in the middle of a call."""
    return session.protocol_version >= MODERN_PROTOCOL


@contextlib.contextmanager
def asking_through(ctx: ServerRequestContext[Any]) -> Iterator[None]:
    """While a host request is being served, the daemon's questions go to that host.

    The line is a context variable, so it is seen by the daemon client's callback when the callback
    runs inside the request (the client's input-required driver does) and never by another
    request that is served at the same time.
    """
    line = HostLine(ctx.session, ctx.request_id)
    token = _HOST.set(line)
    _LATEST.append(line)
    try:
        yield
    finally:
        _HOST.reset(token)
        with contextlib.suppress(ValueError):
            _LATEST.remove(line)


async def forward_elicitation(
    context: ClientRequestContext, params: types.ElicitRequestParams
) -> types.ElicitResult | types.ErrorData:
    """The daemon client's elicitation callback: ask the host, return what the user answered.

    A host that fails to ask or to answer counts as a cancel (nobody said yes), which the daemon
    reads as a no: nothing runs and nothing is spent.
    """
    line = _HOST.get() or (_LATEST[-1] if _LATEST else None)
    if line is None or not isinstance(params, types.ElicitRequestFormParams):
        return types.ErrorData(
            code=types.INVALID_REQUEST,
            message="Headwaters has no host to ask for this question.",
        )
    try:
        answer = await line.session.elicit_form(
            params.message, params.requested_schema, related_request_id=line.request_id
        )
    except Exception as exc:
        _log.info("the host could not be asked", extra={"error": type(exc).__name__})
        return types.ElicitResult(action="cancel")
    return types.ElicitResult(action=answer.action, content=answer.content)


async def call_tool_round(
    client: Client,
    params: types.CallToolRequestParams,
    meta: Any,
    progress: ProgressFnT,
) -> types.CallToolResult | types.InputRequiredResult:
    """One ``tools/call`` on the daemon, for a host that cannot be sent requests (2026-07-28).

    The daemon's ``InputRequiredResult`` is returned as it is, so the host asks its user and
    retries the call; the retry's answers and the daemon's ``request_state`` are passed back here
    from the host's params. The shim keeps nothing between the two rounds.
    """

    async def send() -> types.CallToolResult | types.InputRequiredResult:
        result = await client.session.call_tool(
            params.name,
            params.arguments,
            progress_callback=progress,
            input_responses=params.input_responses,
            request_state=params.request_state,
            meta=meta,
            allow_input_required=True,
        )
        if isinstance(result, types.CallToolResult | types.InputRequiredResult):
            return result
        raise MCPError(types.INTERNAL_ERROR, "The daemon answered with an unexpected result.")

    try:
        return await send()
    except MCPError as exc:
        if exc.code != types.HEADER_MISMATCH:
            raise
        # The daemon wants the tool listing first (the SDK's own recovery): list it, send once more.
        await client.list_tools(cache_mode="refresh")
        return await send()
