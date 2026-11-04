"""HDW-521: one watchdog tick and the deliberate-stop mark it respects.

No task, no daemon and no waiting: every effect is an injected callable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core import daemon_hold, daemon_watchdog, paths
from headwaters.core.daemon_control import ControlResult


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))


def _started(outcome: str = "started") -> ControlResult:
    return ControlResult(outcome, "The daemon is up.")


def test_a_held_daemon_is_left_alone() -> None:
    def never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a deliberate stop must not be touched")

    result = daemon_watchdog.tick(held=lambda: True, find=never_called, start=never_called)

    assert result.action == "held"
    assert result.ok
    assert result.message == daemon_watchdog.HELD_MESSAGE
    assert "headwaters start" in result.message


def test_a_running_daemon_is_not_started_again() -> None:
    def never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the daemon is running")

    result = daemon_watchdog.tick(held=lambda: False, find=lambda: object(), start=never_called)

    assert result.action == "running"
    assert result.ok


def test_a_dead_daemon_is_started() -> None:
    starts: list[bool] = []

    def start() -> ControlResult:
        starts.append(True)
        return _started()

    result = daemon_watchdog.tick(held=lambda: False, find=lambda: None, start=start)

    assert result.action == "started"
    assert result.ok
    assert starts == [True]


def test_a_failed_start_is_reported_not_swallowed() -> None:
    result = daemon_watchdog.tick(
        held=lambda: False,
        find=lambda: None,
        start=lambda: ControlResult("failed", "The daemon exited with code 2."),
    )

    assert result.action == "failed"
    assert not result.ok
    assert "code 2" in result.message


def test_the_mark_lives_beside_the_daemon_state_and_round_trips() -> None:
    assert daemon_hold.hold_path().parent == paths.state_dir()
    assert not daemon_hold.is_held()

    written = daemon_hold.hold("stopped")

    assert written is not None
    assert daemon_hold.is_held()
    assert written.read_text(encoding="utf-8").startswith("{")
    assert daemon_hold.clear()
    assert not daemon_hold.is_held()
    assert not daemon_hold.clear()  # nothing left to remove


def test_a_missing_state_dir_only_makes_the_mark_absent() -> None:
    assert daemon_hold.is_held() is False
    assert daemon_hold.clear() is False
