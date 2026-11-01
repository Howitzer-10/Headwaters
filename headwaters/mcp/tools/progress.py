"""Progress of a long call (HDW-206, v3 5.2.2, v3.4 B3).

The router reports each step of a request through ``RouteRequest.on_progress`` (a plain callback
that runs on the event loop). ``Progress`` takes those steps, turns them into short fixed messages
(a stage and a model id, never request text) and hands them to one or more sinks: the MCP
``notifications/progress`` of a call that is still open, and the ``jobs`` row of a background job.

Three rules keep it safe and useful:

- MCP needs ``progress`` to rise with every notification, so the stream carries a step counter
  (1, 2, 3 ...), not a percentage; the job row gets the 0..1 fraction, which never goes down.
- While one step takes long (a cold model loading, a slow draft) the last message is repeated
  every ``heartbeat_s`` seconds, so a host that waits for sign of life does not time the call out.
- A sink that fails is ignored: reporting progress must never break the call it reports on.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Any

from headwaters.core.logging import get_logger
from headwaters.core.router_types import ProgressEvent

__all__ = ["STAGE_FRACTION", "Progress", "Sink", "Update", "describe"]

_log = get_logger("mcp.progress")

# How far along each router stage is, as a share of the whole request. Rough by design: only the
# order matters, and the fraction never goes back when a request is retried or cascaded.
STAGE_FRACTION: dict[str, float] = {
    "analyzing": 0.05,
    "scope": 0.10,
    "deciding": 0.15,
    "executing": 0.30,
    "retrying": 0.30,
    "cascading": 0.30,
    "verifying": 0.85,
    "escalating": 0.90,
    "done": 1.0,
}


@dataclass(frozen=True)
class Update:
    step: int  # 1, 2, 3 ...: rises with every update, heartbeats included
    fraction: float  # 0..1, never goes down
    message: str
    stage: str
    model: str | None = None


Sink = Callable[[Update], Awaitable[None]]


def _words(code: str) -> str:
    return code.replace("_", " ").strip()


def describe(event: ProgressEvent, *, cold: bool = False) -> str:
    """The one-line message for a router step. ``cold``: this is the first call to a model that
    has to be loaded into the GPU first."""
    model = event.model or "a model"
    detail = _words(event.detail)
    stage = event.stage
    if stage == "analyzing":
        return "Reading the request"
    if stage == "scope":
        return "Applying the scope and locks"
    if stage == "deciding":
        return "Choosing a model"
    if stage == "executing":
        if event.detail == "first_token":
            return f"{model} is answering"
        if cold:
            return f"Loading {model} into the GPU (cold start); this can take a while"
        return f"Waiting for {model}"
    if stage == "retrying":
        return f"Retrying {model}" + (f" after {detail}" if detail else "")
    if stage == "cascading":
        return f"{model} hit a limit" + (f" ({detail})" if detail else "") + "; trying another"
    if stage == "verifying":
        return "Checking the answer"
    if stage == "escalating":
        return f"The check failed; trying a stronger model than {model}"
    if event.detail == "verify_failed":
        return "Done (the answer failed its check)"
    return "Done"


class _Stop:
    pass


_STOP = _Stop()


class Progress:
    """Use as ``async with Progress(sinks, heartbeat_s=5) as progress`` and pass ``progress`` as
    ``RouteRequest.on_progress``. Leaving the block flushes what is queued and stops the pump."""

    def __init__(
        self, sinks: Sequence[Sink], *, heartbeat_s: float, cold_model: str | None = None
    ) -> None:
        self._sinks = tuple(sinks)
        self._heartbeat = heartbeat_s if heartbeat_s > 0 else None
        self._cold_model = cold_model
        self._queue: asyncio.Queue[ProgressEvent | _Stop] = asyncio.Queue()
        self._pump_task: asyncio.Task[None] | None = None

    def __call__(self, event: ProgressEvent) -> None:
        """The router's callback: runs on the event loop, so it only queues."""
        self._queue.put_nowait(event)

    async def __aenter__(self) -> Progress:
        self._pump_task = asyncio.get_running_loop().create_task(self._pump())
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        task, self._pump_task = self._pump_task, None
        if task is None:
            return
        if exc_type is not None:  # the call failed or was cancelled: nobody waits for the rest
            task.cancel()
        else:
            self._queue.put_nowait(_STOP)
        with contextlib.suppress(BaseException):
            await task

    async def _emit(self, update: Update) -> None:
        for sink in self._sinks:
            try:
                await sink(update)
            except Exception as exc:  # a broken channel must not fail the call
                _log.debug("progress sink failed", extra={"error": type(exc).__name__})

    async def _pump(self) -> None:
        """Send each queued step as an update; repeat the last one while nothing new arrives.

        One ``get()`` stays pending across heartbeats (``asyncio.wait`` does not cancel it on a
        timeout), so a step that arrives at the moment of a heartbeat is never lost.
        """
        last: Update | None = None
        step = 0
        fraction = 0.0
        cold_pending = self._cold_model
        getter: asyncio.Future[Any] = asyncio.ensure_future(self._queue.get())
        try:
            while True:
                done, _ = await asyncio.wait({getter}, timeout=self._heartbeat)
                if not done:  # nothing new for a while: show the call is still alive
                    if last is not None:
                        step += 1
                        last = replace(last, step=step)
                        await self._emit(last)
                    continue
                item = getter.result()
                if isinstance(item, _Stop):
                    return
                getter = asyncio.ensure_future(self._queue.get())
                event: ProgressEvent = item
                step += 1
                fraction = max(fraction, STAGE_FRACTION.get(event.stage, fraction))
                cold = (
                    cold_pending is not None
                    and event.stage == "executing"
                    and event.detail != "first_token"
                    and event.model == cold_pending
                )
                if cold:
                    cold_pending = None  # only the first call to that model loads it
                last = Update(step, fraction, describe(event, cold=cold), event.stage, event.model)
                await self._emit(last)
        finally:
            getter.cancel()
