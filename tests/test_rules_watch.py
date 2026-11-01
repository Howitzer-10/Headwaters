import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError
from headwaters.core.rules_watch import RulesManager


def wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[RulesManager]:
    reloads: list[Rules] = []
    errors: list[ConfigError] = []
    m = RulesManager(
        tmp_path / "rules.yaml",
        debounce_s=0.1,
        on_reload=reloads.append,
        on_error=errors.append,
    )
    m.reloads = reloads  # type: ignore[attr-defined]
    m.errors = errors  # type: ignore[attr-defined]
    m.start()
    yield m
    m.stop()


def test_writes_defaults_on_first_run(manager: RulesManager) -> None:
    assert manager.path.is_file()
    assert manager.current == Rules()
    assert manager.last_error is None


def test_reload_applies_valid_edit(manager: RulesManager) -> None:
    manager.path.write_text("strategy: quality_first\n", encoding="utf-8")
    assert wait_for(lambda: manager.current.strategy == "quality_first")
    assert manager.last_error is None


def test_invalid_edit_keeps_last_good_then_recovers(manager: RulesManager) -> None:
    manager.path.write_text("strategy: quality_first\n", encoding="utf-8")
    assert wait_for(lambda: manager.current.strategy == "quality_first")

    manager.path.write_text("strategy: nonsense\n", encoding="utf-8")
    assert wait_for(lambda: manager.last_error is not None)
    assert manager.current.strategy == "quality_first"  # fallback to last good
    assert "strategy" in str(manager.last_error)

    manager.path.write_text("strategy: primary_first\n", encoding="utf-8")
    assert wait_for(lambda: manager.current.strategy == "primary_first")
    assert manager.last_error is None


def test_rapid_writes_are_debounced(manager: RulesManager) -> None:
    for i in range(5):
        manager.path.write_text(f"walls:\n  cascade_if_wait_over_s: {i + 1}\n", encoding="utf-8")
        time.sleep(0.01)
    assert wait_for(lambda: manager.current.walls.cascade_if_wait_over_s == 5)
    time.sleep(0.4)
    assert len(manager.reloads) <= 2  # type: ignore[attr-defined]


def test_invalid_at_startup_starts_local(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("gateway:\n  port: 0\n", encoding="utf-8")
    m = RulesManager(target)
    assert m.current == Rules(default_scope="local")
    assert m.last_error is not None
    assert "gateway.port" in str(m.last_error)


def test_manual_reload_and_idempotent_start_stop(tmp_path: Path) -> None:
    m = RulesManager(tmp_path / "rules.yaml")
    m.start()
    m.start()
    m.path.write_text("advice: inline\n", encoding="utf-8")
    assert m.reload() is True
    assert m.current.advice == "inline"
    m.stop()
    m.stop()
