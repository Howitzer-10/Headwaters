"""HDW-402 / FU-201-9: the doctor reads daemon state through ``core.daemon_client`` only (D14).

Proven the only way that cannot be argued with: import the module in a fresh interpreter and look
at ``sys.modules``. The doctor must load neither the daemon package, nor the web framework, nor the
MCP SDK - that is what keeps ``headwaters doctor`` able to diagnose a broken daemon.
"""

from __future__ import annotations

import json
import subprocess
import sys

IMPORT_AND_INSPECT = """
import importlib, json, sys
importlib.import_module({module!r})
banned = [
    name
    for name in sys.modules
    if name.split(".")[0] in {{"mcp", "starlette", "fastapi", "uvicorn", "litellm"}}
    or name.startswith(("headwaters.daemon", "headwaters.gateway", "headwaters.mcp"))
]
print(json.dumps(sorted(banned)))
"""


def _loaded(module: str) -> list[str]:
    code = IMPORT_AND_INSPECT.format(module=module)
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_doctor_module_loads_no_server_stack() -> None:
    assert _loaded("headwaters.control.doctor") == []


def test_the_doctor_command_module_loads_no_server_stack() -> None:
    assert _loaded("headwaters.cli.cmd_doctor") == []


def test_the_bundle_module_loads_no_server_stack() -> None:
    assert _loaded("headwaters.control.bundle") == []


def test_the_doctor_reaches_the_daemon_through_daemon_client_only() -> None:
    code = (
        "import json, sys\n"
        "import headwaters.control.doctor as doctor\n"
        "used = [name for name in sys.modules if name.startswith('headwaters.daemon')]\n"
        "print(json.dumps({'imported': used, 'client': 'headwaters.core.daemon_client' in "
        "sys.modules}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True
    )
    payload = json.loads(done.stdout.strip().splitlines()[-1])
    assert payload == {"imported": [], "client": True}
