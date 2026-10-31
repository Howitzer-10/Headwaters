"""HDW-118: the ``pick`` flags (--image, --tools, --tokens) and wall/ETA reporting."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.state import open_database
from headwaters.core.state_limits import record_wall
from tests.cli_support import CODE_PROMPT, add_model, home, invoke, invoke_json, seed_models

FAR_FUTURE = 4_102_444_800.0  # 2100-01-01


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch)
    seed_models(tmp_path)
    return tmp_path


def test_image_flag_requires_a_vision_model(cli_home: Path) -> None:
    result, data = invoke_json("pick", "what is in this picture", "--image")
    assert result.exit_code == 4
    reasons = {r["reason"] for r in data["error"]["rejected"]}
    assert "no_vision" in reasons
    add_model(open_database(), "vendor/eyes:free", "free_cloud", 90.0, modalities=["text", "image"])
    _, found = invoke_json("pick", "what is in this picture", "--image")
    assert found["model"] == "vendor/eyes:free"
    assert found["requirements"]["vision"] is True


def test_tools_flag_filters_models_without_tool_support(cli_home: Path) -> None:
    add_model(open_database(), "vendor/notools:free", "free_cloud", 99.0, tools_ok=False)
    _, plain = invoke_json("pick", CODE_PROMPT)
    assert plain["model"] == "vendor/notools:free"
    _, with_tools = invoke_json("pick", CODE_PROMPT, "--tools")
    assert with_tools["requirements"]["tools"] is True
    assert with_tools["model"] == "vendor/free:free"
    assert {"model": "vendor/notools:free", "reason": "no_tools"} in with_tools["rejected"]


def test_tokens_flag_sizes_the_request(cli_home: Path) -> None:
    _, small = invoke_json("pick", CODE_PROMPT)
    result, big = invoke_json("pick", CODE_PROMPT, "--tokens", "100000")
    assert result.exit_code == 0, big
    assert big["requirements"]["est_tokens"] == 100000
    assert big["requirements"]["complexity"] > small["requirements"]["complexity"]
    assert big["floor"] > small["floor"]  # a bigger request raises the quality floor
    # The local model's 32k context cannot hold it: it drops out of the fallbacks.
    assert {"model": "qwen:14b", "reason": "no_fit_context_too_large"} in big["rejected"]
    assert big["fallbacks"] == []


def test_tokens_must_be_positive(cli_home: Path) -> None:
    assert invoke("pick", "hi", "--tokens", "0").exit_code == 2


def test_no_models_yet_says_how_to_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    result = invoke("pick", "hi")
    assert result.exit_code == 4
    assert "headwaters models refresh" in result.output


def test_walled_model_is_skipped_and_status_shows_its_eta(cli_home: Path) -> None:
    record_wall(open_database(), "model:vendor/free:free", "rate_limit", until=FAR_FUTURE)
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "free")
    assert data["model"] == "qwen:14b"
    assert {"model": "vendor/free:free", "reason": "unavailable_cooldown"} in data["rejected"]
    _, status = invoke_json("status")
    wall = status["walls"][0]
    assert wall["subject"] == "model:vendor/free:free"
    assert wall["kind"] == "rate_limit"
    assert wall["eta_s"] > 0
    assert "rate limited" in invoke("status").stdout
