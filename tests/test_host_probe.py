"""HDW-145: ``scripts/host_probe.py`` answers MCP over raw JSON-RPC and records how it was started.

The probe is what a person registers in VS Code, Cursor or Claude Desktop to answer the host
questions of the ticket (working folder, roots, Job Object, daemon lifetime), so its own behaviour
is tested here without any host.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import psutil
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "host_probe.py"


def load_probe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("host_probe", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["host_probe"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("HEADWATERS_PROBE_LOG", str(tmp_path / "probe.jsonl"))
    return load_probe()


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines()]


def initialize(caps: dict[str, Any]) -> dict[str, Any]:
    params = {
        "protocolVersion": "2025-06-18",
        "capabilities": caps,
        "clientInfo": {"name": "Fake Host", "version": "9.9"},
    }
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}


def test_initialize_records_the_host_name_version_and_capabilities(
    probe: Any, tmp_path: Path
) -> None:
    state = probe.ProbeState()
    (reply,) = probe.handle(initialize({"roots": {"listChanged": True}}), state)
    assert reply["result"]["serverInfo"]["name"] == "headwaters-probe"
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    event = next(e for e in lines(tmp_path / "probe.jsonl") if e["event"] == "initialize")
    assert event["client"]["name"] == "Fake Host"
    assert event["client"]["version"] == "9.9"
    assert event["client"]["roots"] is True
    assert event["client"]["sampling"] is False
    assert event["cwd"] == os.getcwd()


def test_a_host_that_advertises_roots_is_asked_for_them_once(probe: Any, tmp_path: Path) -> None:
    state = probe.ProbeState()
    probe.handle(initialize({"roots": {}}), state)
    ready = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    (ask,) = probe.handle(ready, state)
    assert ask["method"] == "roots/list"
    assert probe.handle(ready, state) == []  # only once
    answer = {"jsonrpc": "2.0", "id": "probe-roots", "result": {"roots": [{"uri": "file:///p"}]}}
    assert probe.handle(answer, state) == []
    event = next(e for e in lines(tmp_path / "probe.jsonl") if e["event"] == "roots_reply")
    assert event["result"]["roots"][0]["uri"] == "file:///p"


def test_a_host_without_roots_is_never_asked(probe: Any) -> None:
    state = probe.ProbeState()
    probe.handle(initialize({}), state)
    assert probe.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, state) == []


def test_tools_list_and_a_probe_call_report_the_working_folder(
    probe: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = probe.ProbeState()
    (listed,) = probe.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, state)
    assert [t["name"] for t in listed["result"]["tools"]] == ["probe"]
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "probe"}}
    (reply,) = probe.handle(call, state)
    body = json.loads(reply["result"]["content"][0]["text"])
    assert Path(body["cwd"]) == tmp_path
    assert "job" in body
    assert "sleeper" not in body


def test_an_unknown_request_gets_an_error_and_an_unknown_notification_none(probe: Any) -> None:
    state = probe.ProbeState()
    (error,) = probe.handle({"jsonrpc": "2.0", "id": 4, "method": "nope"}, state)
    assert error["error"]["code"] == -32601
    assert probe.handle({"jsonrpc": "2.0", "method": "notifications/nope"}, state) == []


def test_serve_skips_blank_and_broken_lines_and_notes_the_close(probe: Any, tmp_path: Path) -> None:
    stdin = ["\n", "not json\n", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"]
    out = io.StringIO()
    probe.serve(stdin, out)
    (written,) = [json.loads(x) for x in out.getvalue().splitlines()]
    assert written == {"jsonrpc": "2.0", "id": 1, "result": {}}
    events = [e["event"] for e in lines(tmp_path / "probe.jsonl")]
    assert events[0] == "start"
    assert events[-1] == "stdin_closed"


def test_the_log_never_holds_environment_values(
    probe: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VSCODE_SECRET_THING", "do-not-log-me")
    probe.handle(initialize({}), probe.ProbeState())
    text = (tmp_path / "probe.jsonl").read_text(encoding="utf-8")
    assert "VSCODE_SECRET_THING" in text  # the name only
    assert "do-not-log-me" not in text


def test_process_alive_is_true_for_this_process_and_false_for_a_gone_one(probe: Any) -> None:
    assert probe.process_alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    assert probe.process_alive(child.pid) is False


def test_the_check_option_reports_a_live_and_a_dead_sleeper(
    probe: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait(timeout=30)
    log = tmp_path / "check.jsonl"
    rows = [
        {"event": "sleeper", "pid": sleeper.pid, "mode": "breakaway"},
        {"event": "sleeper", "pid": gone.pid, "mode": "no_breakaway"},
        {"event": "call"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nbroken\n", encoding="utf-8")
    try:
        assert probe.main(["--check", str(log)]) == 0
    finally:
        sleeper.kill()
        sleeper.wait(timeout=30)
    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"sleeper pid={sleeper.pid} mode=breakaway alive",
        f"sleeper pid={gone.pid} mode=no_breakaway dead",
    ]


def test_the_check_option_with_no_sleeper_says_so(
    probe: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "empty.jsonl"
    log.write_text("", encoding="utf-8")
    assert probe.main(["--check", str(log)]) == 0
    assert capsys.readouterr().out.strip() == "no sleeper recorded"


def test_the_real_script_talks_json_rpc_on_stdout_only(tmp_path: Path) -> None:
    request = (
        json.dumps(initialize({}))
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    env = {**os.environ, "HEADWATERS_PROBE_LOG": str(tmp_path / "real.jsonl")}
    done = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=request.encode("utf-8"),
        capture_output=True,
        timeout=60,
        env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    replies = [json.loads(x) for x in done.stdout.decode("utf-8").splitlines() if x.strip()]
    assert [r["id"] for r in replies] == [1, 2]
    assert lines(tmp_path / "real.jsonl")[0]["event"] == "start"


def test_a_sleeper_started_by_the_probe_outlives_the_call_that_started_it(probe: Any) -> None:
    started = probe.spawn_sleeper()
    pid = started["pid"]
    assert pid is not None, started
    try:
        assert started["mode"] in {"breakaway", "no_breakaway", "new_session"}
        assert probe.process_alive(pid) is True
    finally:
        psutil.Process(pid).kill()
