"""HDW-124: litellm stays inside the cloud executor, and its telemetry and cost-map fetch stay off.

The import-linter contract sees static imports only. These tests catch the rest: `importlib`
strings, `__import__`, env-var names and bare `litellm` names, and they prove at run time that
importing the core router or the CLI does not load litellm.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "headwaters"
ALLOWED = {
    PACKAGE / "core" / "executor" / "cloud.py",
    PACKAGE / "core" / "executor" / "cloud_map.py",
}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of string constants that are docstrings (prose may name litellm; code may not)."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            first = body[0] if body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def litellm_references(source: str) -> list[tuple[int, str]]:
    """Code-level litellm references: imports, names, attributes and non-docstring strings."""
    tree = ast.parse(source)
    docstrings = _docstring_nodes(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            hits += [(line, a.name) for a in node.names if a.name.lower().startswith("litellm")]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").lower().startswith("litellm"):
                hits.append((line, node.module or ""))
        elif isinstance(node, ast.Name):
            if node.id.lower() == "litellm":
                hits.append((line, node.id))
        elif isinstance(node, ast.Attribute):
            if node.attr.lower() == "litellm":
                hits.append((line, node.attr))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and "litellm" in node.value.lower()
        ):
            hits.append((line, node.value))
    return hits


def test_litellm_only_referenced_in_cloud_modules() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path in ALLOWED:
            continue
        for line, text in litellm_references(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {text}")
    assert not offenders, "litellm outside executor/cloud.py and cloud_map.py:\n" + "\n".join(
        offenders
    )


@pytest.mark.parametrize(
    "source",
    [
        "import litellm\n",
        "from litellm import acompletion\n",
        "import importlib\nm = importlib.import_module('litellm')\n",
        "m = __import__('LiteLLM')\n",
        "import os\nos.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'x'\n",
        "def f(litellm):\n    return litellm.acompletion\n",
    ],
)
def test_scanner_catches_hidden_references(source: str) -> None:
    assert litellm_references(source)


def test_scanner_ignores_prose() -> None:
    source = (
        '"""Calls litellm."""\n\n\nclass LiteLLMBackend:\n    """litellm wrapper."""\n\n# litellm\n'
    )
    assert not litellm_references(source)


def _run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        check=False,
    )


@pytest.mark.parametrize("module", ["headwaters.core.router", "headwaters.cli.main"])
def test_importing_does_not_load_litellm(module: str) -> None:
    result = _run(
        f"""
        import importlib, sys
        importlib.import_module({module!r})
        loaded = sorted(m for m in sys.modules if m == 'litellm' or m.startswith('litellm.'))
        assert not loaded, loaded
        """
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_litellm_loader_keeps_telemetry_and_cost_map_fetch_off() -> None:
    """Run this after every litellm version bump (CI runs it on every push).

    With every network connect made to raise, loading litellm through our loader must still
    succeed, `telemetry` must be False, the cost map must come from the packaged copy and the
    fetch switch must be on, and no connect may even have been attempted.
    """
    result = _run(
        """
        import os, socket
        attempts = []

        def blocked(*args, **kwargs):
            attempts.append(args)
            raise OSError('network blocked by test')

        socket.socket.connect = blocked
        socket.socket.connect_ex = blocked
        socket.getaddrinfo = blocked

        os.environ.pop('LITELLM_LOCAL_MODEL_COST_MAP', None)
        from headwaters.core.executor.cloud import _load_litellm

        module = _load_litellm()
        assert module.telemetry is False, module.telemetry
        assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] == 'True'
        assert module.model_cost, 'packaged cost map is empty'
        assert not attempts, attempts
        """
    )
    assert result.returncode == 0, result.stdout + result.stderr
