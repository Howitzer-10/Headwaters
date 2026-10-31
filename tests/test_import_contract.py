"""The import-linter contract must pass on the real repo and fail on a deliberate violation."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ["features", "gateway", "mcp", "control", "installer"]


def _lint_imports(cwd: Path, config: Path) -> subprocess.CompletedProcess[str]:
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    assert exe, "lint-imports not installed in the active environment"
    return subprocess.run(
        [exe, "--config", str(config)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def test_repo_contract_passes() -> None:
    result = _lint_imports(REPO_ROOT, REPO_ROOT / ".importlinter")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("target", FORBIDDEN)
def test_deliberate_violation_fails(tmp_path: Path, target: str) -> None:
    # A throwaway package with the real contract's shape; the real tree is never touched.
    pkg = tmp_path / "hwfake"
    for sub in ["core", *FORBIDDEN]:
        (pkg / sub).mkdir(parents=True)
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "core" / "bad.py").write_text(f"from hwfake import {target}\n")

    config = tmp_path / ".importlinter"
    forbidden = "\n".join(f"    hwfake.{name}" for name in FORBIDDEN)
    config.write_text(
        "[importlinter]\nroot_package = hwfake\n\n"
        "[importlinter:contract:core-is-a-leaf]\nname = core is a leaf\ntype = forbidden\n"
        f"source_modules =\n    hwfake.core\nforbidden_modules =\n{forbidden}\n"
    )

    result = _lint_imports(tmp_path, config)
    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout
