"""HDW-505: the accept-when walkthrough — all eight steps, in order, with nothing real behind them.

The point of this file is the *sequence*: step 1's diff is applied with the fingerprint the page
showed, step 2's pull is the daemon's job, step 3's editor config is re-planned before it is
written, step 5 writes two settings, step 6 creates a scheduled task, step 7 records consent for
the payload the page previewed, step 8 runs the checks. Then the whole walk is repeated, and the
second pass has to change nothing: no second env write, no duplicate editor entry, no second task,
no second default, no lost record.

Everything external is a fake from ``tests.wizard_support``: the environment writer is a dict,
Ollama is three methods, ``schtasks`` is a recorder, the key store is a list of names, the pull is
a scripted iterator, and the dashboard's own route is reached through ``TestClient`` rather than a
socket. The scheduler fake is *stateful* so that "run it twice" means what it says.

Honest limit, stated rather than implied: this walks the wizard on the daemon's dashboard surface,
so it proves the steps, not a first-run path. A machine whose daemon is not yet running has no
dashboard, and therefore no wizard (see ``docs/reports/HDW-505.md``).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from html import escape as html_escape
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.core.executor.ollama import PullLine
from headwaters.core.rules_io import load_rules, rules_path
from headwaters.core.wizard import WIZARD_PATHS
from headwaters.daemon.wizard_backend import WizardBackend
from headwaters.features.wizard import steps
from headwaters.features.wizard.state import step_records
from headwaters.gateway.server import build_status_page_app
from headwaters.packaging import autostart
from tests.wizard_support import (
    CSRF,
    DASHBOARD_PORT,
    FakeOllama,
    FakeRunner,
    attach_report,
    built,
    detected_machine,
    installed_task,
    written_files,
)

BASE = f"http://127.0.0.1:{DASHBOARD_PORT}"


class SchedulerRunner(FakeRunner):
    """``schtasks`` that remembers what it created, so a second run is not a second creation."""

    def __init__(self) -> None:
        super().__init__()
        self.exists = False
        self.enabled = False

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if "/Create" in argv:
            self.exists = True
            self.enabled = True
            return subprocess.CompletedProcess(list(argv), 0, "", "")
        if "/Change" in argv and "/ENABLE" in argv:
            self.enabled = True
        if "/Change" in argv and "/DISABLE" in argv:
            self.enabled = False
        if "/Delete" in argv:
            self.exists = False
        if "/Query" in argv:
            if not self.exists:
                return subprocess.CompletedProcess(list(argv), 1, "ERROR: task does not exist", "")
            xml = autostart.task_xml(user="someone", command="headwaters tray")
            if not self.enabled:
                xml = xml.replace("<Enabled>true</Enabled>", "<Enabled>false</Enabled>", 1)
            return subprocess.CompletedProcess(list(argv), 0, installed_task(xml), "")
        return subprocess.CompletedProcess(list(argv), 0, "", "")


class Walk:
    """The dashboard as a person drives it: a GET of a step, a POST of one enumerated action."""

    def __init__(self, harness: Any) -> None:
        self.h = harness
        self.client = TestClient(
            build_status_page_app(
                render=lambda section, error: "<h1>the dashboard</h1>",
                csrf=CSRF,
                port=DASHBOARD_PORT,
                wizard=WizardBackend(lambda: harness.registry),
            ),
            base_url=BASE,
        )

    def page(self, step: int | None = None) -> str:
        response = self.client.get("/wizard" if step is None else f"/wizard?step={step}")
        assert response.status_code == 200, response.status_code
        return response.text

    def post(self, path: str, **form: str) -> str:
        response = self.client.post(path, data={"csrf": CSRF, **form}, follow_redirects=False)
        assert response.status_code == 303, (response.status_code, response.text[:400])
        landing = self.client.get(response.headers["location"])
        assert landing.status_code == 200, landing.status_code
        return landing.text


def value_of(html: str, name: str) -> str:
    found = re.findall(rf'name="{name}"[^>]*value="([^"]+)"', html)
    assert found, f"the page does not carry a {name} field"
    return found[0]


def test_all_eight_steps_in_order_and_then_again_changing_nothing(tmp_path: Path) -> None:
    machine = detected_machine()
    runner = SchedulerRunner()
    h = built(
        tmp_path,
        runner=runner,
        hardware=lambda: machine,
        ollama=FakeOllama(
            lines=[
                PullLine(status="pulling manifest"),
                PullLine(status="pulling digest", digest="sha256:aa", total=1000, completed=250),
                PullLine(status="pulling digest", digest="sha256:aa", total=1000, completed=1000),
                PullLine(status="success"),
            ]
        ),
    )
    attach_report(h.registry)
    walk = Walk(h)
    wizard = h.plugin()

    # the page a person lands on
    landing = walk.page()
    assert "step 1 of 8" in landing
    assert "the daemon" in landing, "the page says the wizard needs the daemon to be up"

    # 1. detect, then apply only the diff the page showed
    html = walk.page(1)
    assert "OLLAMA_FLASH_ATTENTION" in html
    assert "OLLAMA_NO_CLOUD" in html
    assert "never written" in html, "the no-cloud setting is described, never written"
    fingerprint = value_of(html, "fingerprint")
    planned = wizard._view("detect")["env"]
    assert fingerprint == planned["fingerprint"]
    assert h.writer.writes == [], "reading the page must not write"
    applied = walk.post("/wizard/env/apply", step="detect", fingerprint=fingerprint)
    assert "changed" in applied
    assert "restart the ollama tray app" in applied.lower()
    assert len(h.writer.writes) == len(planned["changes"]), "one write per shown change"
    for line in planned["changes"]:
        assert html_escape(line, quote=True) in html, "the page shows the exact line written"
        name, rest = line.split(":", 1)
        wanted = rest.split("->", 1)[1].split("(", 1)[0].strip()
        assert h.writer.values[name] == wanted, line
    assert "OLLAMA_NO_CLOUD" not in h.writer.values, "the privacy switch is never written"
    assert wizard.act("mark", {"step": "detect", "state": "done"})["ok"] is True

    # 2. models: the recommendation, then the pull, which runs in the daemon
    html = walk.page(2)
    model = value_of(html, "model")
    started = walk.post("/wizard/pull", step="models", model=model)
    assert f"pulling {model}" in started
    wizard._pull.wait(10.0)
    job = wizard._pull.snapshot()
    assert job is not None
    assert job.state == "done", job
    assert job.percent == 100
    assert h.ollama.pulled
    assert h.ollama.pulled[0][1] == model
    assert wizard.act("mark", {"step": "models", "state": "done"})["ok"] is True

    # 3. editors: plan every host, write one, and only the diff that was shown
    html = walk.page(3)
    view = next(item for item in wizard._view("ides")["hosts"] if item["host"] == "vscode")
    assert view["fingerprint"] in html, "the fingerprint of the diff that will be written is shown"
    wrote = walk.post(
        "/wizard/host/apply", step="ides", host="vscode", fingerprint=view["fingerprint"]
    )
    assert ": wrote " in wrote, wrote[-300:]
    configs = written_files(tmp_path)
    assert configs, "the editor config has to exist afterwards"
    assert wizard.act("mark", {"step": "ides", "state": "done"})["ok"] is True

    # 4. keys: read-only, plain provider names, and nothing written
    html = walk.page(4)
    assert "openrouter" in html
    assert "/?section=keys" in html, "adding a key stays one click away in the dashboard"
    assert "HF_TOKEN" not in html
    assert "NVIDIA_API_KEY" not in html
    assert wizard.act("mark", {"step": "keys", "state": "done"})["ok"] is True

    # 5. defaults, from the literals the product already uses
    saved = walk.post("/wizard/mode/set", step="mode", scope="local", strategy="cost_saver")
    assert "defaults saved" in saved
    written = load_rules(rules_path())
    assert written.default_scope == "local"
    assert written.strategy == "cost_saver"
    assert wizard.act("mark", {"step": "mode", "state": "done"})["ok"] is True

    # 6. the at-logon task, shown before it is created
    html = walk.page(6)
    assert "schtasks" in html
    created = walk.post("/wizard/autostart/set", step="autostart", on="1")
    assert "created" in created
    assert runner.exists
    assert runner.enabled
    assert wizard.act("mark", {"step": "autostart", "state": "done"})["ok"] is True

    # 7. the report: the preview is on the page before the consent is recorded
    html = walk.page(7)
    assert "the exact payload" in html.lower()
    consented = walk.post("/wizard/report/consent", step="report", on="1")
    assert "consented for payload version" in consented
    assert wizard._view("report")["enabled"] is True
    assert wizard.act("mark", {"step": "report", "state": "done"})["ok"] is True

    # 8. the checks, which assert what they ran and do not call a model
    ran = walk.post("/wizard/selftest", step="selftest")
    assert "checks ran" in ran
    view = wizard._view("selftest")
    assert len(view["checks"]) == 8
    assert view["runs_model"] is False
    assert wizard.act("mark", {"step": "selftest", "state": "done"})["ok"] is True

    status = wizard.status()
    assert status["recorded"] == 8
    assert {step["state"] for step in status["steps"]} == {"done"}
    assert sorted(step_records(h.db)) == list(range(1, 9))

    # --------------------------------------------------------------- second pass: nothing changes
    env_writes = len(h.writer.writes)
    creates = len([call for call in runner.calls if "/Create" in call])
    rules_text = rules_path().read_text(encoding="utf-8")
    files = written_files(tmp_path)

    html = walk.page(1)
    assert "Nothing to change" in html, "the second pass has no diff left to show"
    assert 'name="fingerprint"' not in html, "no form is offered when there is no diff"
    again = walk.post("/wizard/env/apply", step="detect", fingerprint=fingerprint)
    assert "nothing to change" in again, "a second env apply has nothing left to do"
    assert len(h.writer.writes) == env_writes, "the second pass wrote the environment again"

    html = walk.page(3)
    fresh = next(item for item in wizard._view("ides")["hosts"] if item["host"] == "vscode")
    assert fresh["status"] == "installed", "the second pass sees the entry it wrote"
    again = walk.post(
        "/wizard/host/apply", step="ides", host="vscode", fingerprint=fresh["fingerprint"]
    )
    assert "already has this entry" in again
    assert written_files(tmp_path) == files, "the second pass added or removed a config"

    again = walk.post("/wizard/mode/set", step="mode", scope="local", strategy="cost_saver")
    assert "defaults saved" in again
    assert rules_path().read_text(encoding="utf-8") == rules_text, "rules.yaml changed twice"

    again = walk.post("/wizard/autostart/set", step="autostart", on="1")
    assert "already on" in again
    assert len([call for call in runner.calls if "/Create" in call]) == creates

    again = walk.post("/wizard/report/consent", step="report", on="1")
    assert "consented for payload version" in again
    assert wizard._view("report")["enabled"] is True, "consent is not undone by repeating it"

    assert wizard.status()["recorded"] == 8


def test_every_step_can_be_skipped_through_the_dashboard_and_the_machine_is_untouched(
    tmp_path: Path,
) -> None:
    machine = detected_machine()
    runner = SchedulerRunner()
    h = built(tmp_path, runner=runner, hardware=lambda: machine)
    walk = Walk(h)
    h.plugin()

    for definition in steps.STEPS:
        skipped = walk.post("/wizard/mark", step=definition.id, state="skipped")
        assert f"step {definition.n}" in skipped

    assert {record.state for record in step_records(h.db).values()} == {"skipped"}
    assert h.writer.writes == []
    assert [call for call in runner.calls if "/Query" not in call] == [], (
        "skipping asks schtasks nothing but whether the task exists"
    )
    assert written_files(tmp_path) == []
    assert not rules_path().exists(), "skipping step 5 must not create rules.yaml"

    # and every step can be taken back to unrecorded, which is what "re-runnable" needs
    for definition in steps.STEPS:
        cleared = walk.post("/wizard/mark", step=definition.id, state="clear")
        assert f"step {definition.n}" in cleared
    assert step_records(h.db) == {}


@pytest.mark.parametrize(
    "action",
    ["env_apply", "pull", "host_apply", "mode_set", "autostart_set", "report_consent"],
)
def test_a_write_without_the_csrf_token_is_refused(tmp_path: Path, action: str) -> None:
    h = built(tmp_path, hardware=lambda: detected_machine())
    client = Walk(h).client
    path = next(path for path, name in WIZARD_PATHS.items() if name == action)
    response = client.post(path, data={"model": "qwen3:14b"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_csrf"
    assert h.writer.writes == []
    assert h.runner.calls == []
