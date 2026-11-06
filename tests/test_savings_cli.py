"""HDW-501: ``headwaters savings`` prints a labelled estimate, or says why it has none.

The command must never look like a balance: the label says estimate, the reference model's dated
rate is named, and requests the catalog cannot price are counted rather than costed at zero. With
the ledger feature off, the command still exits zero and explains itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.state import Database
from headwaters.core.state_decisions import DecisionRow, OutcomeRow, insert_decision, save_outcome
from tests.cli_support import home, invoke, invoke_json, seed_models, write_rules

SONNET = "claude-sonnet-4-5"


def _spend(db: Database, *, tokens_in: int = 1_000_000, cost: float | None = 0.0) -> None:
    decision = insert_decision(
        db, DecisionRow(surface="cli", scope="local", strategy="balanced", chosen="qwen:14b")
    )
    save_outcome(
        db,
        OutcomeRow(
            decision_id=decision,
            status="ok",
            tokens_in=tokens_in,
            tokens_out=0,
            cost_usd=cost,
        ),
    )


def test_savings_prints_a_labelled_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home(tmp_path, monkeypatch)
    _spend(seed_models(tmp_path))
    result, payload = invoke_json("savings")
    assert result.exit_code == 0, result.stdout
    assert payload["ok"] is True
    assert payload["available"] is True
    assert payload["kind"] == "estimate"
    assert payload["reference"]["model"] == SONNET
    assert payload["reference"]["price_date"]
    assert payload["totals"]["estimated_savings_usd"] == 3.0  # 1M in at $3
    assert "Estimate, not money in hand" in payload["label"]


def test_the_text_output_names_the_reference_and_says_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home(tmp_path, monkeypatch)
    _spend(seed_models(tmp_path))
    result = invoke("savings")
    assert result.exit_code == 0, result.stdout
    assert SONNET in result.stdout
    assert "Estimate, not money in hand" in result.stdout
    assert "$3.000000" in result.stdout


def test_unpriced_requests_are_named_in_the_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home(tmp_path, monkeypatch)
    db = seed_models(tmp_path)
    _spend(db)
    _spend(db, cost=None)
    _, payload = invoke_json("savings")
    assert payload["totals"]["requests"] == 2
    assert payload["totals"]["priced_requests"] == 1
    assert payload["totals"]["unpriced_requests"] == 1
    assert "1 of 2 requests" in payload["label"]


def test_savings_explains_itself_when_the_feature_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home(tmp_path, monkeypatch)
    _spend(seed_models(tmp_path))
    write_rules(tmp_path, "features:\n  ledger: false\n")
    result, payload = invoke_json("savings")
    assert result.exit_code == 0, result.stdout
    assert payload["ok"] is True
    assert payload["available"] is False
    assert "no estimate" in payload["reason"].lower()
