"""Run a blocking call off the event loop on a daemon thread (HDW-202, HDW-204)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = ["run_blocking"]

T = TypeVar("T")


def _deliver(future: asyncio.Future[Any], value: Any, error: BaseException | None) -> None:
    if future.done():  # the waiter was cancelled
        return
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)


async def run_blocking(fn: Callable[[], T]) -> T:
    """Run a blocking call on a daemon thread.

    Not ``asyncio.to_thread``: that uses the default executor, which ``asyncio.run`` joins at
    exit, so a process closed while the call is still running (a daemon being stopped, a shim
    closed while the daemon is starting) would hang until the call ends.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def work() -> None:
        try:
            value = fn()
            error: BaseException | None = None
        except BaseException as exc:
            value, error = None, exc  # type: ignore[assignment]
        with contextlib.suppress(RuntimeError):  # the loop is already closed
            loop.call_soon_threadsafe(_deliver, future, value, error)

    threading.Thread(target=work, daemon=True, name="headwaters-mcp-worker").start()
    return await future
