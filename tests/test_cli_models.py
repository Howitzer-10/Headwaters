"""HDW-118: ``headwaters models list`` shows fit and num_ctx per model; refresh keeps working."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core.state import open_database
from tests.cli_support import add_model, gpu_state, home, invoke, invoke_json, seed_models


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch, gpu_state(16))
    seed_models(tmp_path)
    return tmp_path


def _by_id(data: dict[str, Any]) -> dict[str, Any]:
    return {m["id"]: m for m in data["models"]}


def test_list_shows_fit_and_num_ctx_for_local_models(cli_home: Path) -> None:
    result, data = invoke_json("models", "list")
    assert result.exit_code == 0
    local = _by_id(data)["qwen:14b"]
    assert local["fit"] == {"status": "fits", "num_ctx": 32768, "reason": "ok"}
    assert data["gpu_budget_gb"] == 14.0  # 16 GB card minus the 2 GB display reserve
    out = invoke("models", "list").stdout
    assert "fits" in out
    assert "32768" in out
    assert "GPU budget for local models: 14.0 GB" in out


def test_cloud_models_have_no_fit(cli_home: Path) -> None:
    _, data = invoke_json("models", "list")
    models = _by_id(data)
    assert models["vendor/free:free"]["fit"] is None
    assert models["vendor/big"]["fit"] is None


def test_small_gpu_changes_the_fit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch, gpu_state(8))  # 6 GB budget: 9 GB of weights only fit offloaded
    seed_models(tmp_path)
    _, data = invoke_json("models", "list")
    assert _by_id(data)["qwen:14b"]["fit"]["status"] == "offload"
    add_model(open_database(), "huge:70b", "local", 90.0, size_gb=40.0)
    _, big = invoke_json("models", "list")
    fit = _by_id(big)["huge:70b"]["fit"]
    assert fit["status"] == "no_fit"
    assert fit["num_ctx"] is None
    assert "no_fit (" in invoke("models", "list").stdout


def test_tokens_option_changes_the_computed_context(cli_home: Path) -> None:
    _, small = invoke_json("models", "list", "--tokens", "1000")
    _, large = invoke_json("models", "list", "--tokens", "20000")
    assert _by_id(small)["qwen:14b"]["fit"]["num_ctx"] == 32768
    assert _by_id(large)["qwen:14b"]["fit"]["num_ctx"] == 32768
    _, too_big = invoke_json("models", "list", "--tokens", "200000")
    assert _by_id(too_big)["qwen:14b"]["fit"]["status"] == "no_fit"


def test_tier_filter(cli_home: Path) -> None:
    _, data = invoke_json("models", "list", "--tier", "local")
    assert list(_by_id(data)) == ["qwen:14b"]
    result = invoke("models", "list", "--tier", "nonsense")
    assert result.exit_code == 2
    assert "--tier must be one of" in result.output


def test_flags_are_shown(cli_home: Path) -> None:
    add_model(open_database(), "old:7b", "local", 40.0, unscored=True, deprecated_by="new:7b")
    out = invoke("models", "list").stdout
    assert "warm" in out
    assert "unscored" in out
    assert "deprecated, use new:7b" in out


def test_no_gpu_says_fit_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from headwaters.cli import runtime

    home(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "hardware_state", lambda rules: None)
    seed_models(tmp_path)
    out = invoke("models", "list").stdout
    assert "unknown (no GPU detected)" in out
    assert "No NVIDIA GPU detected" in out


def test_empty_list_points_to_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    assert "headwaters models refresh" in invoke("models", "list").stdout
    _, data = invoke_json("models", "list")
    assert data["models"] == []


def test_json_reports_reasoning_ok(cli_home: Path) -> None:
    add_model(open_database(), "vendor/think:free", "free_cloud", 90.0, reasoning_ok=True)
    _, data = invoke_json("models", "list")
    by_id = {m["id"]: m for m in data["models"]}
    assert by_id["vendor/think:free"]["reasoning_ok"] is True
    assert by_id["vendor/free:free"]["reasoning_ok"] is None
