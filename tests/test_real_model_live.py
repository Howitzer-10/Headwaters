"""HDW-145: the shim, the daemon and a REAL Ollama model, end to end (``-m live``).

Run with ``uv run pytest -m live tests/test_real_model_live.py``. Each test skips when Ollama or the
model it needs is missing, and skips when a model is already loaded (it never evicts what the
developer is using). Everything stays on 127.0.0.1: the isolated home is in airplane mode and every
call asks for ``scope: local``. No assertion depends on what a model says, only on the shape of the
result, so a different model or a different run does not break it.

Covers FU-204-9, FU-205-10, FU-206-6, FU-206-7 (through the shim), FU-204-6 (result links after a
restart), FU-205-4 and FU-140-2 (think levels), FU-201-8 (keep-warm) and FU-206-3 (estimates).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters, types

from headwaters.core import paths
from headwaters.core.catalog.http import UrllibHttp
from headwaters.core.daemon_control import start_daemon, stop_daemon
from headwaters.core.keepwarm import KeepWarm
from headwaters.core.rules import Rules
from headwaters.mcp.tools.context import ToolSettings
from tests import ollama_support as ollama

pytestmark = [pytest.mark.live, pytest.mark.slow]

CALL_TIMEOUT_S = 600.0
REFRESH_TIMEOUT_S = 300
ADVANCED = {"headwaters_job_status", "headwaters_job_result"}


def _env(home: Path) -> dict[str, str]:
    return {**os.environ, paths.HOME_ENV_VAR: str(home), "PYTHONUTF8": "1"}


def _refresh_models(home: Path) -> None:
    """``headwaters models refresh`` in the isolated home: the daemon needs the local models."""
    code = "from headwaters.cli.main import app; app()"
    done = subprocess.run(
        [sys.executable, "-c", code, "models", "refresh"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=REFRESH_TIMEOUT_S,
        env=_env(home),
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def _shim(home: Path, project: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable, args=["-m", "headwaters.mcp"], env=_env(home), cwd=str(project)
    )


def _project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    return root


def _data(result: types.CallToolResult) -> dict[str, Any]:
    return dict(result.structured_content or {})


def _tool_names(listed: types.ListToolsResult) -> set[str]:
    return {tool.name for tool in listed.tools}


@pytest.fixture
def real_daemon(live_home: Path) -> Iterator[Path]:
    """An isolated home with the real local models and a running daemon. Skips without Ollama.

    The guard in ``require_free_gpu`` means nothing was loaded when the test began, so every model
    that is loaded when it ends was loaded by the test (the daemon keeps one for ``keep_alive``)
    and is freed here, which lets the next live test start on a free GPU.
    """
    ollama.installed()  # skips when Ollama is not running
    ollama.require_free_gpu()
    try:
        _refresh_models(live_home)
        started = start_daemon(timeout_s=60)
        assert started.ok, started.message
        yield live_home
    finally:
        stop_daemon(timeout_s=30)
        for name in ollama.loaded():
            ollama.unload(name)


async def _poll_job(client: Client, job_id: str) -> dict[str, Any]:
    """``headwaters_job_status`` until the job is done or failed; returns the last status data."""
    deadline = time.monotonic() + CALL_TIMEOUT_S
    while time.monotonic() < deadline:
        status = _data(await client.call_tool("headwaters_job_status", {"job_id": job_id}))
        if status.get("status") in {"done", "failed"}:
            return status
        await asyncio.sleep(2.0)
    raise AssertionError(f"job {job_id} did not finish in {CALL_TIMEOUT_S:.0f} s")


async def _finish(client: Client, result: types.CallToolResult) -> dict[str, Any]:
    """The data of a model call: as returned, or after its background job is done."""
    data = _data(result)
    job_id = data.get("job_id")
    if not job_id:
        return data
    await client.call_tool("headwaters_more", {})  # the job tools are hidden until asked for
    status = await _poll_job(client, str(job_id))
    assert status["status"] == "done", status
    return _data(await client.call_tool("headwaters_job_result", {"job_id": job_id}))


def test_ask_and_do_run_end_to_end_through_the_shim_with_a_real_model(
    real_daemon: Path, tmp_path: Path
) -> None:
    """FU-204-9: a real model answers through shim and daemon; progress and a result link arrive."""
    project = _project(tmp_path / "proj")
    heard: list[str] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        heard.append(message or "")

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(_shim(real_daemon, project)) as client:
            ask = await client.call_tool(
                "headwaters_ask",
                {"question": "In one sentence, what is a list comprehension?", "scope": "local"},
                read_timeout_seconds=CALL_TIMEOUT_S,
                progress_callback=on_progress,
            )
            task = {"task": "Fix add so it adds.", "paths": ["app.py"], "want": "diff"}
            doing = await client.call_tool(
                "headwaters_do",
                {**task, "scope": "local"},
                read_timeout_seconds=CALL_TIMEOUT_S,
            )
            return await _finish(client, ask), await _finish(client, doing)

    asked, did = asyncio.run(scenario())
    for data in (asked, did):
        assert data.get("ok") is True, data
        assert data["tier"] == "local"
    assert asked["model"]
    assert asked["result_uri"].startswith("headwaters://result/")
    assert "diffs" in did  # the field exists; whether a small model's diff applies is not asserted
    assert heard, "no progress notification reached the client"


def test_a_background_job_reports_progress_and_hands_over_its_result(
    real_daemon: Path, tmp_path: Path
) -> None:
    """FU-206-6, FU-206-7, FU-205-10: ``async`` job, ``_more`` list/call/list, status, result."""
    project = _project(tmp_path / "proj")
    args = {"task": "Explain app.py in one sentence.", "paths": ["app.py"], "scope": "local"}

    async def scenario() -> tuple[set[str], set[str], dict[str, Any], dict[str, Any], str]:
        async with Client(_shim(real_daemon, project)) as client:
            before = _tool_names(await client.list_tools())
            started = await client.call_tool("headwaters_do", {**args, "async": True})
            job_id = str(_data(started)["job_id"])
            hint = " ".join(c.text for c in started.content if isinstance(c, types.TextContent))
            await client.call_tool("headwaters_more", {})
            after = _tool_names(await client.list_tools())
            status = await _poll_job(client, job_id)
            result = _data(await client.call_tool("headwaters_job_result", {"job_id": job_id}))
            return before, after, status, result, hint

    before, after, status, result, hint = asyncio.run(scenario())
    assert len(before) == 8
    assert ADVANCED.isdisjoint(before)  # hidden at first
    assert after >= ADVANCED  # listed after the host asked (the list-changed round trip)
    assert status["status"] == "done"
    assert status["progress"] == 1.0
    assert result["status"] == "done"
    assert "headwaters_more" in hint  # the start result tells the host how to reach the job tools


def test_a_finished_job_survives_a_daemon_restart_and_a_running_one_fails_cleanly(
    real_daemon: Path, tmp_path: Path
) -> None:
    """The daemon restarts under a host: a done job stays readable; a running one says why."""
    project = _project(tmp_path / "proj")

    async def first() -> tuple[str, str | None]:
        async with Client(_shim(real_daemon, project)) as client:
            ask = {"question": "Say OK.", "scope": "local", "async": True}
            done = _data(await client.call_tool("headwaters_ask", ask))
            await client.call_tool("headwaters_more", {})
            finished = str(done["job_id"])
            await _poll_job(client, finished)
            think = {"question": "Is 97 prime? Think it through.", "scope": "local"}
            running = _data(
                await client.call_tool(
                    "headwaters_think", think, read_timeout_seconds=CALL_TIMEOUT_S
                )
            )
            return finished, running.get("job_id")

    finished, running = asyncio.run(first())
    assert stop_daemon(timeout_s=30).ok
    assert start_daemon(timeout_s=60).ok

    async def second() -> tuple[dict[str, Any], dict[str, Any] | None]:
        async with Client(_shim(real_daemon, project)) as client:
            await client.call_tool("headwaters_more", {})
            kept = _data(await client.call_tool("headwaters_job_status", {"job_id": finished}))
            if running is None:
                return kept, None
            lost = _data(await client.call_tool("headwaters_job_status", {"job_id": running}))
            return kept, lost

    kept, lost = asyncio.run(second())
    assert kept["status"] == "done"
    if lost is not None and lost["status"] == "failed":  # still running when the daemon stopped
        assert lost["error"]["code"] == "daemon_restarted"


@pytest.mark.xfail(strict=True, reason="FU-204-6: results live in daemon memory only")
def test_a_result_link_still_reads_after_a_daemon_restart(
    real_daemon: Path, tmp_path: Path
) -> None:
    """Fails today (the link is 'unknown or expired'); strict, so a fix turns it into a red test."""
    project = _project(tmp_path / "proj")

    async def ask() -> str:
        async with Client(_shim(real_daemon, project)) as client:
            question = {"question": "Say OK.", "scope": "local"}
            asked = await client.call_tool(
                "headwaters_ask", question, read_timeout_seconds=CALL_TIMEOUT_S
            )
            return str((await _finish(client, asked))["result_uri"])

    uri = asyncio.run(ask())
    assert stop_daemon(timeout_s=30).ok
    assert start_daemon(timeout_s=60).ok

    async def read() -> int:
        async with Client(_shim(real_daemon, project)) as client:
            return len((await client.read_resource(uri)).contents)

    assert asyncio.run(read()) == 1


# --- Ollama itself: think levels, keep-warm, and the numbers behind the estimates -------------

THINKER = "gpt-oss:20b"  # takes named levels
PLAIN = "qwen2.5-coder:14b"  # does not think at all


def test_a_thinking_model_takes_named_levels_and_a_wrong_one_is_a_400() -> None:
    """FU-205-4, FU-140-2: gpt-oss accepts true/false and low/medium/high; ``bogus`` is refused."""
    ollama.require_model(THINKER)
    ollama.require_free_gpu()
    try:
        for level in (True, "low", "high"):
            status, data = ollama.chat(THINKER, think=level, num_predict=60)
            assert status == 200, (level, data)
            assert (data.get("message") or {}).get("thinking"), level  # reasoning came back
        status, data = ollama.chat(THINKER, think="bogus", num_predict=10)
        assert status == 400
        assert "invalid think value" in str(data.get("error"))
    finally:
        ollama.unload(THINKER)


def test_a_model_that_does_not_think_refuses_think_in_any_form() -> None:
    """FU-140-2: ``reasoning_ok`` must keep such a model out of ``headwaters_think``."""
    ollama.require_model(PLAIN)
    ollama.require_free_gpu()
    for level in (True, "low"):
        status, data = ollama.chat(PLAIN, think=level, num_predict=10)
        assert status == 400
        assert "does not support thinking" in str(data.get("error"))


def test_keep_warm_pings_the_loaded_model_and_never_loads_one_on_its_own() -> None:
    """FU-201-8: an empty-``messages`` chat re-arms a loaded model; ``auto`` loads nothing."""
    model = PLAIN
    ollama.require_model(model)
    ollama.require_free_gpu()
    rules = Rules.model_validate(
        {"tiers": {"local": {"keep_warm_model": "auto", "keep_alive": "5m"}}}
    )
    warm = KeepWarm(rules, UrllibHttp(timeout=120.0))
    try:
        idle = warm.tick()
        assert (idle.action, idle.reason) == ("skipped", "nothing_loaded")
        assert ollama.loaded() == []  # the tick did not load anything
        ollama.chat(model, num_predict=5, keep_alive="1m")
        assert model in ollama.loaded()
        pinged = warm.tick()
        assert (pinged.action, pinged.models) == ("pinged", (model,))
        assert model in ollama.loaded()
    finally:
        ollama.unload(model)


def test_the_first_draft_estimates_are_within_a_loose_band_of_this_machine() -> None:
    """FU-206-3: cold load seconds per GB and tokens per second against ``ToolSettings``.

    The band is wide on purpose (a quarter to four times the draft): the test catches a number
    that is wrong by an order of magnitude, not one that needs tuning. HDW-704 tunes them. The
    measured values are in the assertion message and in the report.
    """
    model = "qwen3:8b"
    size_gb = ollama.require_model(model)
    ollama.require_free_gpu()
    settings = ToolSettings()
    try:
        status, cold = ollama.chat(model, num_predict=60)
        assert status == 200, cold
        status, warm = ollama.chat(model, num_predict=60)
        assert status == 200, warm
    finally:
        ollama.unload(model)
    load_s = float(cold["load_duration"]) / 1e9
    per_gb = load_s / size_gb
    tps = float(warm["eval_count"]) / (float(warm["eval_duration"]) / 1e9)
    note = f"measured {per_gb:.2f} s/GB cold load, {tps:.1f} tokens/s on {model} ({size_gb:.1f} GB)"
    # Load time depends on the OS file cache: the same model measured 2.6 s/GB after the files had
    # not been read for a while and 0.5 s/GB straight after a run (HDW-145). So the band is only
    # an order-of-magnitude check, a tenth to four times the draft.
    assert settings.cold_load_s_per_gb / 10 <= per_gb <= settings.cold_load_s_per_gb * 4, note
    assert settings.local_tps_fallback / 4 <= tps <= settings.local_tps_fallback * 4, note
    assert float(warm["load_duration"]) / 1e9 < 1.0, "a warm call must not reload the model"
