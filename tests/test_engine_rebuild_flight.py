"""HDW-148: a rebuild never drops a request in flight, and privacy switches apply at once.

Same rig as ``test_engine_rebuild.py``. Rules change by saving ``rules.yaml``: no ``reload()``
call and no sleep before the next request.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from headwaters.core.router_types import RouteRequest
from tests.rebuild_support import ASK, DaemonRig


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DaemonRig]:
    made = DaemonRig(tmp_path, monkeypatch)
    yield made
    made.close()


def test_a_request_in_flight_finishes_on_the_old_engine(rig: DaemonRig) -> None:
    async def scenario() -> tuple[str, str]:
        old = rig.engine()
        gate = asyncio.Event()
        rig.backends[0].gate = gate
        request = RouteRequest(messages=ASK, scope="local", surface="mcp")
        running = asyncio.create_task(old.router.run(request))
        while not rig.backends[0].requests:  # the old engine is now inside its model call
            await asyncio.sleep(0.01)
        rig.save("strategy: quality_first\n")
        fresh = rig.engine()  # reading the rules notices the edit and swaps the engine
        assert fresh is not old
        newer = await fresh.router.run(request)  # the new engine answers while the old one waits
        assert not running.done()
        gate.set()
        finished = await running
        return finished.result.content, newer.result.content

    old_answer, new_answer = asyncio.run(scenario())
    assert old_answer == "answer from engine 1"  # not dropped, not re-run on the new engine
    assert new_answer == "answer from engine 2"
    assert len(rig.backends[0].requests) == 1  # the old engine's call ran exactly once


def test_a_rebuild_leaves_the_old_engine_usable(rig: DaemonRig) -> None:
    old = rig.engine()
    rig.save("strategy: quality_first\n")
    assert rig.engine() is not old
    assert rig.ask(old, scope="local").result.content == "answer from engine 1"


def test_a_catalog_rebuild_keeps_the_rules_in_force(rig: DaemonRig) -> None:
    rig.save("strategy: quality_first\n")
    rig.engine()
    rig.daemon.rebuild_engine()  # what the catalog refresh calls: catalog from disk, same rules
    assert len(rig.builds) == 3
    assert rig.daemon._built_with == rig.manager.current
    assert rig.manager.current.strategy == "quality_first"


def test_the_rebuild_hook_reuses_the_loaded_catalog(
    rig: DaemonRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headwaters.daemon import service

    reads: list[int] = []

    def load() -> tuple[object, str]:
        reads.append(1)
        return rig.catalog, "seed"

    monkeypatch.setattr(service.updater, "load_active", load)
    rig.save("strategy: quality_first\n")
    rig.engine()
    assert reads == []  # a rules edit never re-reads the catalog from disk
    rig.daemon.rebuild_engine()
    assert reads == [1]  # a catalog refresh does


def test_private_lock_by_hand_edit_stops_the_next_cloud_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = DaemonRig(tmp_path, monkeypatch, cloud=True)
    try:
        assert rig.ask().decision.tier == "free_cloud"
        assert rig.cloud_clients == 1
        rig.save("private_lock: true\n")
        after = rig.ask()  # the very next request: no sleep, no reload call
        assert after.decision.tier == "local"
        assert rig.cloud_clients == 1  # no new cloud client was built
    finally:
        rig.close()


def test_airplane_by_hand_edit_stops_the_next_cloud_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = DaemonRig(tmp_path, monkeypatch, cloud=True)
    try:
        rig.save("airplane: true\n")
        assert rig.ask().decision.tier == "local"
        assert rig.cloud_clients == 0
    finally:
        rig.close()


def test_the_lock_applies_even_to_a_request_that_reads_only_the_old_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router and executor read the rules per request, so a stale engine still obeys."""
    rig = DaemonRig(tmp_path, monkeypatch, cloud=True)
    try:
        stale = rig.engine()
        rig.save("private_lock: true\n")
        assert rig.ask(stale).decision.tier == "local"  # never asked the daemon for a new engine
        assert rig.cloud_clients == 0
    finally:
        rig.close()
