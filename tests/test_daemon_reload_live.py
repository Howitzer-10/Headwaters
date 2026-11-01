"""HDW-148: a real daemon process confirms a rules.yaml change (hand edit and ``private``).

Starts ``python -m headwaters.daemon`` in an isolated ``HEADWATERS_HOME`` (airplane mode, see
``tests/live_support.py``). Nothing leaves 127.0.0.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.daemon_client import DaemonClient, find_running
from headwaters.core.daemon_control import start_daemon
from tests.cli_support import invoke_json

pytestmark = pytest.mark.slow


def _start() -> DaemonClient:
    result = start_daemon(timeout_s=45)
    assert result.ok, result.message
    client = find_running()
    assert client is not None
    return client


def test_a_hand_edit_is_live_at_the_next_status_read(live_home: Path) -> None:
    client = _start()
    assert client.status()["private_lock"] is False
    rules = live_home / "config" / "rules.yaml"
    rules.write_text("version: 1\nairplane: true\nprivate_lock: true\n", encoding="utf-8")
    assert client.status()["private_lock"] is True  # the very next read: no wait for the watcher


def test_private_commands_return_after_the_real_daemon_confirms(live_home: Path) -> None:
    client = _start()
    result, data = invoke_json("private", "on")
    assert result.exit_code == 0, result.output
    assert data["daemon"]["confirmed"] is True
    assert data["daemon"]["private_lock"] is True
    assert client.status()["private_lock"] is True
    reply = client.reload()
    assert (reply["ok"], reply["applied"], reply["private_lock"]) == (True, True, True)
    result, data = invoke_json("private", "off")
    assert result.exit_code == 0, result.output
    assert data["daemon"]["private_lock"] is False
    assert client.status()["private_lock"] is False


def test_scope_command_is_confirmed_by_the_real_daemon(live_home: Path) -> None:
    client = _start()
    result, data = invoke_json("scope", "local")
    assert result.exit_code == 0, result.output
    assert data["daemon"]["default_scope"] == "local"
    assert client.status()["scope"] == "local"
