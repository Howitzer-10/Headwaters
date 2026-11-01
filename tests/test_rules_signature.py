"""HDW-148 (FU-131-4): ``RulesManager.current`` sees a hand edit at once, with no watcher wait.

No test here starts the watcher or sleeps: the file's ``(mtime_ns, size)`` is the only trigger.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from headwaters.core import rules_io, rules_watch
from headwaters.core.rules import Rules
from headwaters.core.rules_watch import RulesManager


def _write(path: Path, text: str) -> None:
    """Write ``text`` and move the mtime forward, so two saves never share a signature."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text, encoding="utf-8", newline="\n")
    os.utime(path, ns=(before + 10_000_000, before + 10_000_000))


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts how often the file is parsed."""
    calls: list[int] = []
    real = rules_io.load_rules

    def counting(path: Path | None = None) -> Rules:
        calls.append(1)
        return real(path)

    monkeypatch.setattr(rules_watch, "load_rules", counting)
    return calls


def test_a_hand_edit_is_seen_by_the_very_next_read(tmp_path: Path) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")  # no watcher started
    assert manager.current.private_lock is False
    _write(manager.path, "private_lock: true\n")
    assert manager.current.private_lock is True  # no sleep, no reload() call


def test_the_edit_applies_each_time_it_changes(tmp_path: Path) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")
    _write(manager.path, "airplane: true\n")
    assert manager.current.airplane is True
    _write(manager.path, "airplane: false\ndefault_scope: local\n")
    assert (manager.current.airplane, manager.current.default_scope) == (False, "local")


def test_many_reads_of_an_unchanged_file_parse_it_once(tmp_path: Path, parses: list[int]) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")
    start = len(parses)
    _write(manager.path, "strategy: quality_first\n")
    for _ in range(200):
        assert manager.current.strategy == "quality_first"
    assert len(parses) - start == 1


def test_a_half_written_file_keeps_the_last_good_rules_and_is_parsed_once(
    tmp_path: Path, parses: list[int]
) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")
    _write(manager.path, "private_lock: true\n")
    assert manager.current.private_lock is True
    start = len(parses)
    _write(manager.path, "private_lock: tr\nstrategy: [unfinished\n")  # an unfinished save
    for _ in range(50):
        assert manager.current.private_lock is True  # the last good rules stay
    assert manager.last_error is not None
    assert len(parses) - start == 1  # the bad signature was recorded: no re-parse per request


def test_the_next_save_after_a_bad_one_is_read_again(tmp_path: Path) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")
    _write(manager.path, "strategy: quality_first\n")
    assert manager.current.strategy == "quality_first"
    _write(manager.path, "strategy: nonsense\n")
    assert manager.current.strategy == "quality_first"
    assert manager.last_error is not None
    _write(manager.path, "strategy: primary_first\n")
    assert manager.current.strategy == "primary_first"
    assert manager.last_error is None


def test_a_deleted_file_keeps_the_last_good_rules(tmp_path: Path) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")
    _write(manager.path, "private_lock: true\n")
    assert manager.current.private_lock is True
    manager.path.unlink()
    assert manager.current.private_lock is True
    assert manager.last_error is not None


def test_an_unchanged_save_does_not_run_the_hook(tmp_path: Path) -> None:
    seen: list[Rules] = []
    manager = RulesManager(tmp_path / "rules.yaml", on_reload=seen.append)
    _write(manager.path, "airplane: true\n")
    assert manager.current.airplane is True
    assert len(seen) == 1
    _write(manager.path, "airplane: true  # same rules, new signature\n")
    assert manager.current.airplane is True
    assert len(seen) == 1  # parsed, but nothing changed: no engine rebuild


def test_reload_and_the_next_read_do_not_parse_twice(tmp_path: Path, parses: list[int]) -> None:
    manager = RulesManager(tmp_path / "rules.yaml")
    _write(manager.path, "advice: inline\n")
    start = len(parses)
    assert manager.reload() is True
    assert manager.current.advice == "inline"
    assert len(parses) - start == 1


def test_the_hook_runs_on_the_reading_thread_and_readers_wait_for_it(tmp_path: Path) -> None:
    """The new rules are never visible before the hook (the engine rebuild) has finished."""
    entered, release = threading.Event(), threading.Event()
    done: list[str] = []

    def hook(rules: Rules) -> None:
        entered.set()
        release.wait(5)
        done.append("hook")

    manager = RulesManager(tmp_path / "rules.yaml", on_reload=hook)
    _write(manager.path, "private_lock: true\n")
    first = threading.Thread(target=lambda: manager.current)
    first.start()
    assert entered.wait(5)
    seen: list[bool] = []

    def other() -> None:
        seen.append(manager.current.private_lock)
        done.append("reader")

    second = threading.Thread(target=other)
    second.start()
    second.join(0.3)
    assert second.is_alive()  # held back while the hook runs
    release.set()
    first.join(5)
    second.join(5)
    assert done == ["hook", "reader"]
    assert seen == [True]


def test_a_failing_hook_does_not_fail_the_read(tmp_path: Path) -> None:
    def hook(rules: Rules) -> None:
        raise RuntimeError("rebuild failed")

    manager = RulesManager(tmp_path / "rules.yaml", on_reload=hook)
    _write(manager.path, "private_lock: true\n")
    assert manager.current.private_lock is True  # the rules are in force; the read still answers
    assert manager.current.private_lock is True
