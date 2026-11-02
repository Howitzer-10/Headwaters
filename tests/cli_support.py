"""Shared setup for the CLI tests (HDW-118): an isolated home, seeded models, a fake GPU.

Nothing here touches the network or a real GPU. ``invoke`` runs the real Typer app through
``CliRunner`` against a throwaway ``HEADWATERS_HOME``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from headwaters.cli import runtime
from headwaters.cli.main import app
from headwaters.core import paths
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.hardware.gpu import GpuInfo
from headwaters.core.hardware.state import BatteryState, HardwareState, build_state
from headwaters.core.rules import LocalTier
from headwaters.core.state import Database, open_database
from headwaters.core.state_models import ModelRow, ScoreRow, set_score, upsert_model

runner = CliRunner()

LOCAL_ARCH = {"n_layers": 48, "n_kv_heads": 8, "head_dim": 128}
CODE_PROMPT = "write a function that adds two numbers"
FAKE_KEY = "sk-Zq8Xv3Lm9Pw2Rt5Yb7Nc4Hd6Jf1Kg"  # secret-shaped, not a placeholder


def gpu_state(total_gb: int = 16, on_battery: bool = False) -> HardwareState:
    gpu = GpuInfo("Test RTX", total_gb * 1024.0, total_gb * 1024.0 - 512, True, "nvml")
    profiles = load_catalog(
        Path(__file__).resolve().parent.parent / SEED_RELATIVE_PATH
    ).gpu_profiles
    return build_state(gpu, LocalTier(), BatteryState(True, on_battery, 80.0), profiles=profiles)


def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hw: HardwareState | None = None) -> Path:
    """Point Headwaters at ``tmp_path``, run from there, and fix the hardware snapshot."""
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.chdir(tmp_path)
    state = hw if hw is not None else gpu_state()
    monkeypatch.setattr(runtime, "hardware_state", lambda rules: state)
    return tmp_path


def add_model(
    db: Database,
    model_id: str,
    tier: str,
    code: float | None = 85.0,
    *,
    agentic: float | None = None,
    **row: Any,
) -> None:
    fields: dict[str, Any] = {
        "provider": "ollama" if tier == "local" else "vendor",
        "modalities": ["text"],
        "tools_ok": True,
        "json_ok": True,
        "ctx": 32768,
        "source": "test",
    }
    if tier == "local":
        fields.update(arch=LOCAL_ARCH, size_gb=9.0)
    fields.update(row)
    upsert_model(db, ModelRow(id=model_id, tier=tier, **fields))
    for task, value in (("code", code), ("chat", code), ("agentic_tools", agentic)):
        if value is not None:
            set_score(db, ScoreRow(model_id, task, seed=value))


def seed_models(tmp_path: Path) -> Database:
    """A free cloud model (best), a warm local model that also clears the floor, and a paid one.

    The cloud model outscores the local one even with the warm bonus, so a plain pick lands on
    the cloud model with the local one as its fallback.
    """
    db = open_database()
    add_model(db, "vendor/free:free", "free_cloud", 98.0, agentic=98.0, ctx=131072)
    add_model(db, "qwen:14b", "local", 80.0, agentic=80.0, warm=True, size_vram_gb=8.0)
    add_model(db, "vendor/big", "paid", 95.0, agentic=95.0, ctx=200000, price_in=3, price_out=15)
    return db


def write_rules(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config" / "rules.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def invoke(*args: str) -> Result:
    return runner.invoke(app, list(args))


def invoke_json(*args: str) -> tuple[Result, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"])
    return result, json.loads(result.stdout)
