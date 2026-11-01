"""Make a running daemon confirm a rules.yaml change (HDW-148, FU-131-4).

``headwaters private`` and ``headwaters scope`` write ``rules.yaml``, then call ``confirm`` so the
command returns only after the daemon reports the new value. A privacy switch must be true when
it answers.

- No daemon running: say so, exit 0. The file is the truth and the daemon reads it at start.
- A daemon process that is alive but does not answer, answers with an error, rejects the file or
  reports another value: exit non-zero with "saved, but the running daemon has not confirmed it".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from headwaters.cli.output import EXIT_FAILED, fail
from headwaters.core.daemon_client import DaemonClient, DaemonError, find_running
from headwaters.core.daemon_control import pid_alive
from headwaters.core.daemon_info import read_info

__all__ = ["NOT_CONFIRMED", "NO_DAEMON", "Confirmation", "confirm"]

NOT_CONFIRMED = "saved, but the running daemon has not confirmed it"
NO_DAEMON = "No daemon is running; it reads rules.yaml when it starts."


@dataclass(frozen=True)
class Confirmation:
    daemon_running: bool
    message: str
    live: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"running": self.daemon_running, "confirmed": self.daemon_running, **self.live}


def confirm(
    expect: Mapping[str, Any],
    *,
    as_json: bool = False,
    find: Callable[[], DaemonClient | None] | None = None,
    alive: Callable[[int], bool] | None = None,
) -> Confirmation:
    """Ask the daemon to reload now and check that each ``expect`` key is live with its value."""
    client = (find or find_running)()  # resolved here, so tests can replace the module names
    if client is None:
        info = read_info()
        if info is not None and (alive or pid_alive)(info.pid):
            _not_confirmed("the daemon process is running but did not answer", as_json)
        return Confirmation(False, NO_DAEMON, {})
    try:
        reply = client.reload()
    except DaemonError as exc:
        _not_confirmed(str(exc), as_json)
    if reply.get("ok") is not True or reply.get("applied") is not True:
        _not_confirmed(str(reply.get("error") or "the daemon rejected rules.yaml"), as_json)
    live = {k: reply.get(k) for k in ("private_lock", "airplane", "default_scope")}
    for key, wanted in expect.items():
        if live.get(key) != wanted:
            _not_confirmed(f"the daemon reports {key} = {live.get(key)!r}, not {wanted!r}", as_json)
    return Confirmation(True, "The running daemon confirmed it.", live)


def _not_confirmed(reason: str, as_json: bool) -> NoReturn:
    fail(
        "daemon_not_confirmed",
        f"{NOT_CONFIRMED} ({reason}).",
        fix="Run `headwaters daemon-status`, or restart it with `headwaters stop` then "
        "`headwaters start`. The setting is in rules.yaml and applies when the daemon starts.",
        exit_code=EXIT_FAILED,
        as_json=as_json,
    )
