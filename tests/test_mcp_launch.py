"""HDW-202: ``headwaters mcp-path`` and the absolute launch command for host configs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from headwaters.core import mcp_launch
from tests.cli_support import invoke, invoke_json


def test_script_next_to_the_interpreter_is_preferred(tmp_path: Path) -> None:
    suffix = ".exe" if sys.platform == "win32" else ""
    python = tmp_path / ("python" + suffix)
    python.write_text("", encoding="utf-8")
    script = tmp_path / (mcp_launch.SCRIPT_NAME + suffix)
    script.write_text("", encoding="utf-8")
    options = mcp_launch.launch_options(str(python))
    assert options[0].kind == "script"
    assert Path(options[0].command) == script.resolve()
    assert options[-1].kind == "module"
    assert options[-1].args == ("-m", "headwaters.mcp")
    assert Path(options[-1].command).is_absolute()


def test_without_a_script_the_module_form_is_used(tmp_path: Path) -> None:
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    only = mcp_launch.primary(str(python))
    assert only.kind == "module"
    assert Path(only.command).is_absolute()


def test_config_snippet_shape() -> None:
    launch = mcp_launch.McpLaunch("module", "C:/x/python.exe", ("-m", "headwaters.mcp"))
    snippet = mcp_launch.config_snippet(launch)
    assert snippet == {
        "mcpServers": {
            "headwaters": {"command": "C:/x/python.exe", "args": ["-m", "headwaters.mcp"]}
        }
    }


def test_mcp_path_json_gives_absolute_paths() -> None:
    result, data = invoke_json("mcp-path")
    assert result.exit_code == 0
    assert data["ok"] is True
    assert Path(data["command"]).is_absolute()
    assert all(Path(o["command"]).is_absolute() for o in data["options"])
    assert data["config"]["mcpServers"]["headwaters"]["command"] == data["command"]


def test_mcp_path_text_output() -> None:
    result = invoke("mcp-path")
    assert result.exit_code == 0
    assert "Command:" in result.stdout
    assert "mcpServers" in result.stdout
    json.loads(result.stdout[result.stdout.index("{") :])  # the snippet is valid JSON


def test_mcp_path_does_not_import_the_sdk_or_web_framework() -> None:
    code = (
        "import sys\n"
        "from typer.testing import CliRunner\n"
        "from headwaters.cli.main import app\n"
        "CliRunner().invoke(app, ['mcp-path'])\n"
        "bad = [m for m in ('mcp', 'starlette', 'uvicorn', 'fastapi') if m in sys.modules]\n"
        "print(bad)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.stdout.strip().endswith("[]"), done.stdout + done.stderr


@pytest.mark.parametrize("name", ["headwaters-mcp"])
def test_entry_point_is_declared(name: str) -> None:
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'{name} = "headwaters.mcp.shim:main"' in text
