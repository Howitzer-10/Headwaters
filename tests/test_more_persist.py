"""HDW-205: a scope change made through the tool persists in rules.yaml and shows in status."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from headwaters.core import paths
from headwaters.core.rules import Rules
from headwaters.core.rules_edit import set_setting
from headwaters.core.rules_io import load_rules, rules_path
from headwaters.core.rules_watch import RulesManager
from headwaters.daemon.service import Daemon
from tests.frontdoor_support import Rig, structured


def _wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Rig, RulesManager]:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path / "home"))
    manager = RulesManager()  # creates the default rules.yaml under the isolated home
    rig = Rig(tmp_path)
    rig.rules = manager.current
    rig.context.rules = lambda: manager.current
    rig.context.edit_rules = set_setting
    rig.context.reload_rules = manager.reload
    # The router reads ``rig.rules`` per request (as the daemon reads ``manager.current``), so
    # follow the manager: a reload of rules.yaml then reaches the next routed call.
    original_reload = manager.reload

    def reload_and_follow() -> bool:
        ok = original_reload()
        rig.rules = manager.current
        return ok

    rig.context.reload_rules = reload_and_follow
    return rig, manager


def test_a_scope_change_is_written_to_rules_yaml_and_status_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _manager = _wired(tmp_path, monkeypatch)
    data = structured(asyncio.run(rig.call("headwaters_scope", {"scope": "local"})))
    assert data["ok"] is True
    assert load_rules(rules_path()).default_scope == "local"  # on disk, not only in memory
    status = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert status["scope"]["default_scope"] == "local"


def test_the_private_lock_persists_and_the_next_request_obeys_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _manager = _wired(tmp_path, monkeypatch)
    asyncio.run(rig.call("headwaters_scope", {"private_lock": True}))
    assert load_rules(rules_path()).private_lock is True
    answer = structured(asyncio.run(rig.call("headwaters_ask", {"question": "hi"})))
    assert answer["scope"] == "local"
    assert rig.cloud_calls == 0


def test_comments_in_rules_yaml_survive_a_tool_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig, _manager = _wired(tmp_path, monkeypatch)
    target = rules_path()
    text = target.read_text(encoding="utf-8")
    target.write_text("# my note\n" + text, encoding="utf-8", newline="\n")
    asyncio.run(rig.call("headwaters_scope", {"strategy": "quality_first"}))
    assert target.read_text(encoding="utf-8").startswith("# my note\n")
    assert load_rules(target).strategy == "quality_first"


def test_the_daemons_tool_context_hands_the_tools_the_database_and_the_reload() -> None:
    reloads: list[int] = []
    daemon = Daemon.__new__(Daemon)  # only tool_context() is exercised: no sockets, no engine
    daemon.rules_source = Rules
    daemon.engine = None
    daemon.db = object()  # type: ignore[assignment]
    daemon.reload_rules = lambda: reloads.append(1)
    context = daemon.tool_context()
    assert context.db is daemon.db
    context.reload_rules()
    assert reloads == [1]
