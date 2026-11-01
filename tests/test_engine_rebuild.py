"""HDW-148 (FU-131-1, FU-131-2): the daemon rebuilds the engine when the rules change.

Every test runs a real ``Daemon`` over a real ``RulesManager`` (``tests/rebuild_support.py``).
Rules are changed the way a user changes them: by saving ``rules.yaml``, with no ``reload()`` call
and no sleep. The next request must already use the new value.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.engine import Engine
from headwaters.core.hardware.fit import FitConfig
from headwaters.core.policy.filters import Candidate
from headwaters.daemon.service import Daemon
from tests.executor_support import decision
from tests.rebuild_support import DaemonRig
from tests.router_support import requirements


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DaemonRig]:
    made = DaemonRig(tmp_path, monkeypatch)
    yield made
    made.close()


def _local_candidate(engine: Engine) -> Candidate:
    source: Any = engine.router._candidates  # the ``_RememberingSource`` around the builder
    return next(c for c in source._inner(requirements()) if c.id == "qwen:14b")


def test_the_startup_engine_is_built_once(rig: DaemonRig) -> None:
    assert len(rig.builds) == 1
    assert rig.engine() is rig.builds[0]


def test_editing_paid_models_marks_the_primary_on_the_next_request(rig: DaemonRig) -> None:
    assert _local_candidate(rig.engine()).is_primary is False
    rig.save("tiers:\n  paid:\n    models: [qwen:14b]\n")
    engine = rig.engine()
    assert engine is rig.builds[1]
    assert _local_candidate(engine).is_primary is True


def test_editing_the_local_host_reaches_the_next_local_call(rig: DaemonRig) -> None:
    rig.ask()
    rig.save("tiers:\n  local:\n    host: http://127.0.0.1:11999\n")
    rig.ask()
    assert rig.hosts == ["http://localhost:11434", "http://127.0.0.1:11999"]


def test_editing_kv_cache_type_changes_the_next_fit(rig: DaemonRig) -> None:
    before = _local_candidate(rig.engine()).fit
    assert before is not None
    assert before.usable
    rig.save("tiers:\n  local:\n    kv_cache_type: f16\n")
    after = _local_candidate(rig.engine()).fit
    assert after is not None
    assert after.usable
    assert FitConfig.from_rules(rig.manager.current).kv_type == "f16"
    # f16 needs twice the bytes per token of q8_0, so fewer tokens fit the same VRAM budget.
    assert before.max_ctx_tokens is not None
    assert after.max_ctx_tokens is not None
    assert after.max_ctx_tokens < before.max_ctx_tokens
    assert before.num_ctx is not None
    assert after.num_ctx is not None
    assert after.num_ctx < before.num_ctx


def test_editing_features_reaches_the_registry(rig: DaemonRig) -> None:
    assert rig.engine().registry._flags == {}
    rig.save("features:\n  ledger: true\n  dashboard: false\n")
    assert rig.engine().registry._flags == {"ledger": True, "dashboard": False}


def test_editing_endpoints_reaches_the_target_builder(rig: DaemonRig) -> None:
    free = decision("free_cloud", "myfree/some-model")
    assert rig.engine().targets(free).route == "myfree/some-model"  # no endpoint yet
    rig.save(
        "models:\n  openai_compatible:\n"
        "    - name: myfree\n      base_url: https://api.example.com/v1/\n      api_key_env: K\n"
    )
    target = rig.engine().targets(free)
    assert (target.route, target.api_base) == ("openai/some-model", "https://api.example.com/v1")


def test_an_unchanged_or_invalid_save_does_not_rebuild(rig: DaemonRig) -> None:
    rig.engine()
    rig.save("# only a comment, same rules\n")
    rig.engine()
    rig.save("strategy: nonsense\n")
    rig.engine()
    assert len(rig.builds) == 1


def test_many_threads_noticing_one_edit_build_one_engine(rig: DaemonRig) -> None:
    rig.save("strategy: quality_first\n")
    seen: list[Engine] = []
    gate = threading.Barrier(8)

    def reader() -> None:
        gate.wait()
        seen.append(rig.engine())

    threads = [threading.Thread(target=reader) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert len(rig.builds) == 2
    assert {id(e) for e in seen} == {id(rig.builds[1])}  # every reader got the new engine


def test_nothing_reads_a_startup_snapshot_of_the_rules() -> None:
    assert "rules" not in {f.name for f in dataclasses.fields(Engine)}
    assert "rules" not in {f.name for f in dataclasses.fields(Daemon)}
