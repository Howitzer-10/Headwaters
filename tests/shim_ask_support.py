"""HDW-146: a real daemon client in front of the real tools, so the shim can be tested end to end.

``rig_link`` gives the shim a link whose connections are real ``Client`` objects connected to the
``Rig`` server. Each declares elicitation only when the link opened it with a callback, exactly as
``open_daemon_client`` does. ``FakeHost`` is the fake host that answers yes, no or cancel.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp import Client, types

from headwaters.core.daemon_client import DaemonClient
from headwaters.mcp.link import DaemonLink
from tests.frontdoor_support import Rig
from tests.mcp_shim_support import INFO

__all__ = ["FakeHost", "rig_link"]


class FakeHost:
    """The host behind the shim: records each question and answers with ``action``."""

    def __init__(self, action: str = "accept", agree: bool = True) -> None:
        self.action = action
        self.agree = agree
        self.questions: list[str] = []

    async def __call__(self, context: Any, params: Any) -> types.ElicitResult:
        self.questions.append(params.message)
        if self.action == "accept":
            return types.ElicitResult(action="accept", content={"agree": self.agree})
        return types.ElicitResult(action=self.action)  # type: ignore[arg-type]


def rig_link(rig: Rig, openings: list[Any] | None = None, *, elicitation: Any = None) -> DaemonLink:
    """A link whose connections are real clients of the rig. ``openings`` records each callback."""

    async def ensure() -> DaemonClient:
        return DaemonClient(INFO, "t")

    @asynccontextmanager
    async def opener(daemon: DaemonClient, callback: Any = None) -> AsyncIterator[Client]:
        if openings is not None:
            openings.append(callback)
        async with Client(rig.server, elicitation_callback=callback) as client:
            yield client

    return DaemonLink(ensure, opener, elicitation)
