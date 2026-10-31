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
SUBPACKAGES = ["core", "cli", "features", "gateway", "mcp", "control", "installer", "daemon"]
CORE_FORBIDDEN = ["features", "gateway", "mcp", "daemon", "control", "installer", "cli"]


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
    assert text.count("[importlinter:contract:") == 7


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
        ("core.bad", "import starlette\n"),
        ("core.bad", "from starlette.applications import Starlette\n"),
        ("core.bad", "import uvicorn\n"),
        ("mcp.bad", "import uvicorn\n"),
        ("daemon.bad", "import starlette\n"),
        ("daemon.bad", "import uvicorn\n"),
        ("daemon.bad", "import mcp\n"),
        ("daemon.bad", "from mcp.server import MCPServer\n"),
        ("cli.bad", "import uvicorn\n"),
        ("control.bad", "import fastapi\n"),
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
        ("gateway.server", "import uvicorn\nfrom starlette.routing import Route\n"),
        ("daemon.service", "from hwfake.gateway import server\nfrom hwfake.mcp import endpoint\n"),
    ],
)
def test_allowed_external_imports_pass(tmp_path: Path, module: str, source: str) -> None:
    result = _fake_repo(tmp_path, module, source)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("package", "statement"),
    [
        ("core", "import starlette"),
        ("core", "import uvicorn"),
        ("core", "import mcp"),
        ("mcp", "import starlette"),
    ],
)
def test_contracts_bite_in_the_real_repo_with_the_daemon_code_in_place(
    tmp_path: Path, package: str, statement: str
) -> None:
    """HDW-201 accept-when: a throwaway web-framework or SDK import in the real tree fails.

    The probe is written into a copy of ``headwaters`` so the working tree is never touched, and
    the daemon, gateway and mcp modules that actually import the frameworks stay in place.
    """
    copy = tmp_path / "headwaters"
    shutil.copytree(REPO_ROOT / "headwaters", copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / package / "_probe.py").write_text(statement + "\n", encoding="utf-8")
    shutil.copy(CONFIG, tmp_path / ".importlinter")
    result = _lint_imports(tmp_path, tmp_path / ".importlinter")
    assert result.returncode != 0, result.stdout
    assert "BROKEN" in result.stdout
    assert f"headwaters.{package}._probe" in result.stdout


def test_real_daemon_code_imports_the_frameworks_only_through_gateway_and_mcp() -> None:
    text = (REPO_ROOT / "headwaters" / "daemon" / "service.py").read_text(encoding="utf-8")
    assert "import starlette" not in text
    assert "import uvicorn" not in text
    assert "from mcp" not in text.replace("from headwaters.mcp", "")
    server = (REPO_ROOT / "headwaters" / "gateway" / "server.py").read_text(encoding="utf-8")
    assert "import starlette" in server or "from starlette" in server
    endpoint = (REPO_ROOT / "headwaters" / "mcp" / "endpoint.py").read_text(encoding="utf-8")
    assert "from mcp.server import MCPServer" in endpoint
