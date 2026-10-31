import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from headwaters.core.state import LATEST_VERSION, Database, open_database
from headwaters.core.state_decisions import (
    DecisionRow,
    OutcomeRow,
    get_decision,
    get_outcome,
    insert_decision,
    recent_decisions,
    save_outcome,
    set_user_feedback,
)
from headwaters.core.state_limits import get_quota
from headwaters.core.state_misc import (
    add_handoff,
    create_job,
    get_handoff,
    get_job,
    get_session,
    kv_delete,
    kv_get,
    kv_set,
    latest_handoff_id,
    pin_session,
    update_job,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "test.db")


def test_decisions_and_outcomes(db: Database) -> None:
    did = insert_decision(
        db,
        DecisionRow(
            surface="gateway",
            scope="free",
            strategy="cost_saver",
            locks_applied=["project_rule"],
            requirements={"task": "code", "prompt": "SECRET USER TEXT"},
            chosen="qwen",
            alternatives=["a", "b"],
            floor=75.0,
            score=80.0,
            reasons=["cheapest"],
        ),
    )
    d = get_decision(db, did)
    assert d is not None
    assert (d.scope, d.strategy, d.locks_applied) == ("free", "cost_saver", ["project_rule"])
    assert d.requirements == {"task": "code"}  # prompt text never stored
    assert get_decision(db, 9999) is None
    assert get_outcome(db, did) is None

    save_outcome(db, OutcomeRow(did, "ok", latency_ms=120, tokens_in=10, tokens_out=20))
    save_outcome(db, OutcomeRow(did, "ok", cost_usd=0.0, verify_result="pass"))
    o = get_outcome(db, did)
    assert o is not None
    assert (o.counterfactual_usd, o.tokens_in, o.verify_result) == (None, None, "pass")
    assert set_user_feedback(db, did, "good")
    assert not set_user_feedback(db, 9999, "good")
    insert_decision(db, DecisionRow("mcp", "auto", "quality_first"))
    assert [x.surface for x in recent_decisions(db, 1)] == ["mcp"]


def test_sessions_jobs_handoffs_kv(db: Database) -> None:
    assert get_session(db, "s") is None
    s = pin_session(db, "s", "m1", "local")
    assert (s.pinned_model, s.last_switch) == ("m1", None)
    s = pin_session(db, "s", "m2", "cloud", handoff_packet_id=3)
    assert (s.pinned_model, s.handoff_packet_id, s.last_switch is not None) == ("m2", 3, True)

    create_job(db, "j1")
    assert update_job(db, "j1", status="running", progress=0.5)
    assert update_job(db, "j1", result_ref="r")
    j = get_job(db, "j1")
    assert j is not None
    assert (j.status, j.progress, j.result_ref) == ("running", 0.5, "r")
    assert not update_job(db, "nope", status="x")
    assert get_job(db, "nope") is None

    assert latest_handoff_id(db, "s") is None
    first = add_handoff(db, "s", {"goal": "g", "messages": ["private"]})
    second = add_handoff(db, "s", {"goal": "h"})
    assert latest_handoff_id(db, "s") == second
    assert get_handoff(db, first) == {"goal": "g"}
    assert get_handoff(db, 999) is None

    assert kv_get(db, "k", "dflt") == "dflt"
    kv_set(db, "k", {"a": 1})
    kv_set(db, "k", [1, 2])
    assert kv_get(db, "k") == [1, 2]
    assert kv_delete(db, "k")
    assert not kv_delete(db, "k")


WORKER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from headwaters.core.state import Database, open_database
    from headwaters.core.state_decisions import DecisionRow, insert_decision
    from headwaters.core.state_limits import add_quota_usage
    from headwaters.core.state_misc import kv_set

    path, tag, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    open_database(path)  # concurrent first-run migrate must be safe
    db = Database(path)
    for i in range(n):
        insert_decision(db, DecisionRow("gateway", "auto", "cost_saver", session=tag))
        add_quota_usage(db, "shared", "day")
        kv_set(db, f"{tag}-{i}", i)
    """
)


def test_two_processes_write_concurrently(tmp_path: Path) -> None:
    path = tmp_path / "shared.db"
    n = 60
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(path), tag, str(n)],
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        for tag in ("a", "b")
    ]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err

    d = Database(path)
    assert d.schema_version() == LATEST_VERSION
    with d.read() as c:
        assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 2 * n
        for tag in ("a", "b"):
            q = "SELECT COUNT(*) FROM decisions WHERE session = ?"
            assert c.execute(q, (tag,)).fetchone()[0] == n
        assert c.execute("SELECT COUNT(*) FROM kv").fetchone()[0] == 2 * n
        assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == LATEST_VERSION
    quota = get_quota(d, "shared", "day")
    assert quota is not None
    assert quota.count == 2 * n  # atomic increments: none lost
