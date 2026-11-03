"""HDW-118: the database-backed candidate source and the score lookup under it."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.candidates import CandidateBuilder, fit_for_row
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.hardware.fit import FitResult
from headwaters.core.hardware.residency import ResidencyInputs
from headwaters.core.hardware.state import HardwareState
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_models import (
    ModelRow,
    ScoreRow,
    effective_scores,
    set_score,
    upsert_model,
)
from tests.cli_support import LOCAL_ARCH, add_model, gpu_state
from tests.router_support import requirements

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


def builder(db: Database, hw: HardwareState | None, rules: Rules | None = None) -> CandidateBuilder:
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    return CandidateBuilder(db, rules or Rules(), catalog, lambda: hw)


def by_id(candidates: list[Candidate]) -> dict[str, Candidate]:
    return {c.id: c for c in candidates}


def fit_of(candidate: Candidate) -> FitResult:
    assert candidate.fit is not None
    return candidate.fit


def residency_of(candidate: Candidate) -> ResidencyInputs:
    assert candidate.residency is not None
    return candidate.residency


def test_effective_scores_prefer_override_then_learned_then_seed(db: Database) -> None:
    add_model(db, "m", "free_cloud", None)
    set_score(db, ScoreRow("m", "code", seed=50.0))
    set_score(db, ScoreRow("m", "chat", seed=50.0, learned_ema=60.0))
    set_score(db, ScoreRow("m", "reasoning", seed=50.0, learned_ema=60.0, override=90.0))
    set_score(db, ScoreRow("m", "extract"))  # no value at all: left out
    assert effective_scores(db) == {"m": {"code": 50.0, "chat": 60.0, "reasoning": 90.0}}


def test_cloud_candidates_carry_scores_and_no_fit(db: Database) -> None:
    add_model(db, "vendor/free:free", "free_cloud", 77.0)
    found = by_id(builder(db, gpu_state())(requirements()))["vendor/free:free"]
    assert found.scores["code"] == 77.0
    assert found.fit is None
    assert found.tier == "free_cloud"


def test_unknown_context_uses_catalog_default_and_is_flagged(db: Database) -> None:
    upsert_model(db, ModelRow("vendor/noctx", "free_cloud", "p", ["text"], ctx=None))
    upsert_model(db, ModelRow("vendor/ctx", "free_cloud", "p", ["text"], ctx=8192))
    found = by_id(builder(db, gpu_state())(requirements()))
    default = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH).unknown_model_defaults.ctx
    assert found["vendor/noctx"].row.ctx == default
    assert found["vendor/noctx"].ctx_assumed is True
    assert found["vendor/ctx"].row.ctx == 8192
    assert found["vendor/ctx"].ctx_assumed is False


def test_local_candidates_get_fit_and_residency(db: Database) -> None:
    add_model(db, "qwen:14b", "local", 80.0, warm=True, size_vram_gb=8.0)
    add_model(db, "other:7b", "local", 60.0)
    found = by_id(builder(db, gpu_state(16))(requirements()))
    warm, cold = found["qwen:14b"], found["other:7b"]
    assert fit_of(warm).status == "fits"
    assert residency_of(warm).warm is True
    assert residency_of(cold).warm is False
    assert residency_of(cold).evicts == ("qwen:14b",)  # one slot: loading it evicts the warm one


def test_catalog_speed_is_used_for_known_models(db: Database) -> None:
    add_model(db, "qwen2.5-coder:14b", "local", 70.0)
    found = by_id(builder(db, gpu_state(16))(requirements()))["qwen2.5-coder:14b"]
    assert found.expected_tps == 35.0  # desktop_16gb in the seed
    add_model(db, "mystery:1b", "local", 10.0)
    assert by_id(builder(db, gpu_state(16))(requirements()))["mystery:1b"].expected_tps is None


def test_without_a_gpu_local_models_do_not_fit(db: Database) -> None:
    add_model(db, "qwen:14b", "local", 80.0)
    found = by_id(builder(db, None)(requirements()))["qwen:14b"]
    assert fit_of(found).usable is False


def test_paid_primary_models_are_flagged(db: Database) -> None:
    add_model(db, "vendor/big", "paid", 95.0)
    add_model(db, "vendor/other", "paid", 90.0)
    rules = Rules.model_validate({"tiers": {"paid": {"models": ["vendor/big"]}}})
    found = by_id(builder(db, gpu_state(), rules)(requirements()))
    assert found["vendor/big"].is_primary is True
    assert found["vendor/other"].is_primary is False


def test_fit_for_row_uses_decimal_gb_correctly(db: Database) -> None:
    row = ModelRow("m", "local", "ollama", ["text"], ctx=32768, size_gb=9.0, arch=LOCAL_ARCH)
    assert fit_for_row(row, 14.0, 1000, "q8_0").status == "fits"
    assert fit_for_row(row, 6.0, 1000, "q8_0").status == "offload"
    assert fit_for_row(row, 1.0, 1000, "q8_0").status == "no_fit"


def test_rows_without_architecture_data_do_not_fit(db: Database) -> None:
    row = ModelRow("m", "local", "ollama", ["text"], ctx=32768, size_gb=9.0, arch={"n_layers": 0})
    result = fit_for_row(row, 14.0, 1000, "q8_0")
    assert (result.status, result.reason) == ("no_fit", "missing_catalog_data")
    upsert_model(db, row)  # the builder must tolerate such a row too
    assert fit_of(builder(db, gpu_state())(requirements())[0]).usable is False


def test_a_stale_local_row_for_a_cloud_model_is_never_a_candidate(db: Database) -> None:
    """R4 / FU-121-4: a ``local`` row whose name matches the cloud patterns is dropped here."""
    add_model(db, "gpt-oss:120b-cloud", "local", 99.0)
    add_model(db, "qwen2.5-coder:14b", "local", 70.0)
    found = by_id(builder(db, gpu_state(16))(requirements()))
    assert "gpt-oss:120b-cloud" not in found
    assert "qwen2.5-coder:14b" in found


def test_cloud_tier_rows_are_unaffected_by_the_remote_name_check(db: Database) -> None:
    add_model(db, "vendor/x-cloud", "free_cloud", 70.0)
    found = by_id(builder(db, gpu_state(16))(requirements()))
    assert "vendor/x-cloud" in found
