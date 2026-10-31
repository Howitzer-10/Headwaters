"""HDW-117: router verification, escalation and decision/outcome logging."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.executor.types import Delta
from headwaters.core.router_types import RouteFailed
from headwaters.core.state_decisions import get_outcome, recent_decisions
from tests.router_rig import Rig
from tests.router_support import (
    OK,
    RATE_LIMITED,
    Escalator,
    Ledger,
    Verifier,
    candidate,
    registry_with,
)
from tests.test_router import FREE, LOCAL, pool

BAD = [Delta(content="this is bad output"), Delta(finish_reason="stop")]
PROBLEM = "bad_output: contains the word bad"


def test_verify_failure_is_reported_without_a_plugin(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: BAD, LOCAL: OK}, pool(), verifiers=[Verifier()])
    result = rig.run(verify="always")
    assert result.decision.model == FREE  # no EscalationPlugin: nothing else is tried
    assert result.verify_failed
    assert result.verify is not None
    assert result.verify.problems == (PROBLEM,)
    assert rig.dispatch.calls == [FREE]
    assert [a.status for a in result.attempts] == ["verify_failed"]
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert (outcome.status, outcome.verify_result) == ("verify_failed", "fail")


def test_verify_failure_escalates_when_a_plugin_is_registered(tmp_path: Path) -> None:
    plugin = Escalator()
    rig = Rig(
        tmp_path,
        {FREE: BAD, LOCAL: OK},
        pool(local=84.0),
        verifiers=[Verifier()],
        registry=registry_with(escalation=plugin),
    )
    result = rig.run(verify="always")
    assert result.decision.model == LOCAL
    assert not result.verify_failed
    assert plugin.calls == [(1, (PROBLEM,))]
    assert [a.status for a in result.attempts] == ["verify_failed", "ok"]
    outcome = get_outcome(rig.db, result.decision_id or 0)
    assert outcome is not None
    assert outcome.escalated_to == LOCAL


def test_escalation_with_nothing_left_reports_the_failed_answer(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {FREE: BAD},
        [candidate(FREE, "free_cloud", 90.0)],
        verifiers=[Verifier()],
        registry=registry_with(escalation=Escalator()),
    )
    result = rig.run(verify="always")
    assert result.verify_failed
    assert len(recent_decisions(rig.db)) == 1  # one row, not an extra failure row


def test_verify_off_skips_the_gate(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: BAD}, pool(), verifiers=[Verifier()])
    result = rig.run(verify="off")
    assert result.verify is None
    assert not result.verify_failed


def test_requested_verify_without_any_verifier_is_reported_as_not_run(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    result = rig.run(verify="always")
    assert result.verify is not None
    assert result.verify.ran is False
    assert not result.verify_failed


def test_decision_and_outcome_rows_are_written(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED], LOCAL: OK}, pool(local=84.0))
    result = rig.run(surface="mcp")
    rows = recent_decisions(rig.db)
    assert len(rows) == 1
    row = rows[0]
    assert row.id == result.decision_id
    assert (row.surface, row.chosen, row.scope) == ("mcp", LOCAL, "free")
    assert row.session == result.session_key
    outcome = get_outcome(rig.db, row.id or 0)
    assert outcome is not None
    assert outcome.status == "ok"
    assert outcome.latency_ms is not None


def test_logged_rows_never_contain_the_prompt_or_answer(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: OK}, pool())
    rig.run(messages=[{"role": "user", "content": "please refactor zebra_widget_frobnicator"}])
    dump = repr(recent_decisions(rig.db))
    assert "zebra_widget_frobnicator" not in dump
    assert "ok answer" not in dump


def test_failed_requests_are_logged_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {FREE: [RATE_LIMITED]}, [candidate(FREE, "free_cloud", 90.0)])
    with pytest.raises(RouteFailed):
        rig.run()
    rows = recent_decisions(rig.db)
    assert len(rows) == 1
    outcome = get_outcome(rig.db, rows[0].id or 0)
    assert outcome is not None
    assert outcome.status == "no_route_left"


def test_ledger_plugins_get_metadata_only(tmp_path: Path) -> None:
    ledger = Ledger()
    rig = Rig(tmp_path, {FREE: OK}, pool(), registry=registry_with(ledger=ledger))
    result = rig.run()
    assert len(ledger.events) == 1
    event = ledger.events[0]
    assert event["decision_id"] == result.decision_id
    assert event["model"] == FREE
    assert "ok answer" not in repr(event)
