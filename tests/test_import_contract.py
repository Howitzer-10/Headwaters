"""The import-linter contracts must pass on the real repo and fail on deliberate violations.

The violation tests run the real `.importlinter` text against a throwaway package, so a contract
that is weakened or removed in the real file makes them fail.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / ".importlinter"
SUBPACKAGES = ["core", "cli", "features", "gateway", "mcp", "control", "installer"]
CORE_FORBIDDEN = ["features", "gateway", "mcp", "control", "installer", "cli"]


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


def _fake_repo(tmp_path: Path, module: str, source: str) -> subprocess.CompletedProcess[str]:
    """Build `hwfake` with one extra module and lint it with the real contract text."""
    pkg = tmp_path / "hwfake"
    (pkg).mkdir()
    (pkg / "__init__.py").write_text("")
    for sub in SUBPACKAGES:
        (pkg / sub).mkdir()
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "core" / "executor").mkdir()
    (pkg / "core" / "executor" / "__init__.py").write_text("")
    target = pkg.joinpath(*module.split(".")).with_suffix(".py")
    target.write_text(source)

    config = tmp_path / ".importlinter"
    text = CONFIG.read_text(encoding="utf-8").replace("headwaters", "hwfake")
    config.write_text(text, encoding="utf-8")
    return _lint_imports(tmp_path, config)


def test_repo_contract_passes() -> None:
    result = _lint_imports(REPO_ROOT, CONFIG)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 broken" in result.stdout


def test_repo_declares_every_contract() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert "include_external_packages = True" in text
    assert text.count("[importlinter:contract:") == 4


@pytest.mark.parametrize("target", CORE_FORBIDDEN)
def test_core_importing_a_forbidden_package_fails(tmp_path: Path, target: str) -> None:
    result = _fake_repo(tmp_path, "core.bad", f"from hwfake import {target}\n")
    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("core.bad", "import litellm\n"),
        ("core.executor.cloud_map", "import litellm\n"),
        ("gateway.bad", "import litellm\n"),
        ("cli.bad", "from litellm import acompletion\n"),
        ("core.bad", "import mcp\n"),
        ("cli.bad", "from mcp.server import MCPServer\n"),
        ("gateway.bad", "import mcp\n"),
        ("core.bad", "import fastapi\n"),
        ("mcp.bad", "from starlette.responses import Response\n"),
        ("cli.bad", "import starlette\n"),
    ],
)
def test_external_package_violations_fail(tmp_path: Path, module: str, source: str) -> None:
    result = _fake_repo(tmp_path, module, source)
    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout


@pytest.mark.parametrize(
    ("module", "source"),
    [
        ("core.executor.cloud", "import litellm\n"),
        ("mcp.server", "import mcp\n"),
        ("gateway.app", "import fastapi\nimport starlette\n"),
    ],
)
def test_allowed_external_imports_pass(tmp_path: Path, module: str, source: str) -> None:
    result = _fake_repo(tmp_path, module, source)
    assert result.returncode == 0, result.stdout + result.stderr
