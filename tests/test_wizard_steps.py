"""HDW-505: each of the eight steps — what it shows, and that skipping or repeating it is safe.

Every test runs against ``tests/wizard_support.harness``: a ``tmp_path`` database and
``rules.yaml``, an environment writer that is a dict, Ollama as three methods, ``schtasks`` as a
recorder, the credential store as a list of provider names and the pull as a scripted iterator.
Nothing here reaches the network, the registry, the Credential Manager or the real install.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core.executor.ollama import PullLine
from headwaters.core.rules import Rules
from headwaters.core.rules_io import load_rules
from headwaters.features.wizard import envfix, steps
from headwaters.features.wizard.pull import explain, recommendations
from headwaters.features.wizard.state import record_step, step_records
from headwaters.packaging import autostart
from tests.contracts.harness import run_contract
from tests.wizard_support import (
    CSRF,
    LOW_ENV,
    DeadTransport,
    FakeOllama,
    FakeRunner,
    FakeWriter,
    UnwritableWriter,
    attach_report,
    built,
    detected_machine,
    hardware_only,
    progress,
    seed_catalog,
    written_files,
)

NOW = 1_800_000_000.0


@pytest.fixture
def hw(tmp_path: Path) -> Any:
    return detected_machine()


# -- 1. every step renders, and every step can be skipped --------------------------------------
def test_every_step_renders_with_a_skip_form(tmp_path: Path, hw: Any) -> None:
    wizard = built(tmp_path, hardware=hardware_only(hw)).plugin()
    for definition in steps.STEPS:
        html = wizard.page(csrf=CSRF, step=definition.n)
        assert 'action="/wizard/mark"' in html
        assert f">{definition.title}</h1>" in html
        assert "Skip this step" in html
        assert f"step {definition.n} of 8" in html
        assert f'name="csrf" value="{CSRF}"' in html


def test_every_step_can_be_marked_skipped_and_the_rest_still_run(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    for definition in steps.STEPS:
        outcome = wizard.act("mark", {"step": definition.id, "state": "skipped"})
        assert outcome["ok"] is True, outcome
        assert outcome["step"] == definition.n
    recorded = step_records(h.db)
    assert sorted(recorded) == list(range(1, 9))
    assert {record.state for record in recorded.values()} == {"skipped"}
    status = wizard.status()
    assert [step["state"] for step in status["steps"]] == ["skipped"] * 8
    assert status["recorded"] == 8


def test_skipping_every_step_writes_nothing_to_the_machine(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    for definition in steps.STEPS:
        assert wizard.act("mark", {"step": definition.id, "state": "skipped"})["ok"] is True
    assert h.writer.writes == []
    assert h.runner.calls == []
    assert not written_files(tmp_path)
    assert not (tmp_path / "state" / "rules.yaml").exists()
    assert sorted(step_records(h.db)) == list(range(1, 9))


def test_clearing_a_record_forgets_it_and_changes_nothing_else(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    record_step(h.db, 3, "done")
    cleared = wizard.act("mark", {"step": "ides", "state": "clear"})
    assert cleared["ok"] is True, cleared
    assert step_records(h.db) == {}
    again = wizard.act("mark", {"step": "ides", "state": "clear"})
    assert again["ok"] is True, "clearing an unrecorded step is a no-op, not an error"


def test_an_unknown_step_or_state_or_action_is_refused_in_words(tmp_path: Path) -> None:
    wizard = built(tmp_path).plugin()
    for form, needle in (
        ({"step": "nope", "state": "done"}, "no step called 'nope'"),
        ({"step": "detect", "state": "half"}, "done or skipped"),
    ):
        outcome = wizard.act("mark", form)
        assert outcome["ok"] is False
        assert needle in str(outcome["error"])
    unknown = wizard.act("delete_everything", {})
    assert unknown["ok"] is False
    assert "no wizard action called 'delete_everything'" in str(unknown["error"])


# -- 2. the env fix: shown first, written once, exactly undoable --------------------------------
def test_detect_shows_the_diff_and_writes_nothing(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    view = h.plugin().page(csrf=CSRF, step=1)
    assert "OLLAMA_FLASH_ATTENTION: 0 -&gt; 1" in view
    assert h.writer.writes == [], "looking at the diff must not write it"
    assert h.writer.values == LOW_ENV


def test_env_apply_needs_the_fingerprint_that_was_shown(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    view = wizard._view("detect")
    stale = wizard.act("env_apply", {"fingerprint": "0000000000000000"})
    assert stale["ok"] is False
    assert "changed since the page was shown" in str(stale["error"])
    assert h.writer.writes == []
    good = wizard.act("env_apply", {"fingerprint": view["env"]["fingerprint"]})
    assert good["ok"] is True, good


def test_env_apply_writes_each_value_once_and_a_second_apply_changes_nothing(
    tmp_path: Path, hw: Any
) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    fingerprint = wizard._view("detect")["env"]["fingerprint"]
    assert wizard.act("env_apply", {"fingerprint": fingerprint})["ok"] is True
    written = list(h.writer.writes)
    assert sorted(name for name, _ in written) == [
        "OLLAMA_FLASH_ATTENTION",
        "OLLAMA_KV_CACHE_TYPE",
    ]
    assert h.writer.values["OLLAMA_FLASH_ATTENTION"] == "1"
    second = wizard.act("env_apply", {"fingerprint": fingerprint})
    assert second["ok"] is False
    assert "nothing to change" in str(second["error"])
    assert h.writer.writes == written, "a re-run must not write a value a second time"


def test_env_undo_puts_back_exactly_what_was_there_and_is_not_repeatable(
    tmp_path: Path, hw: Any
) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    fingerprint = wizard._view("detect")["env"]["fingerprint"]
    assert wizard.act("env_apply", {"fingerprint": fingerprint})["ok"] is True
    undo = wizard.act("env_undo", {})
    assert undo["ok"] is True, undo
    assert h.writer.values == LOW_ENV
    assert wizard._view("detect")["env_undoable"] is False
    again = wizard.act("env_undo", {})
    assert again["ok"] is False
    assert "nothing recorded to put back" in str(again["error"])


def test_a_platform_that_cannot_write_refuses_and_offers_the_lines(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw), writer=UnwritableWriter(LOW_ENV), keys=())
    wizard = h.plugin()
    view = wizard._view("detect")
    assert view["env"]["writable"] is False
    assert any("no supported way" in note for note in view["env"]["notes"])
    refusal = wizard.act("env_apply", {"fingerprint": view["env"]["fingerprint"]})
    assert refusal["ok"] is False
    assert "no supported way" in str(refusal["error"])
    assert h.writer.writes == []


def test_the_env_step_never_names_a_provider_key_variable(tmp_path: Path, hw: Any) -> None:
    html = built(tmp_path, hardware=hardware_only(hw)).plugin().page(csrf=CSRF, step=1)
    for forbidden in ("HF_TOKEN", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        assert forbidden not in html
    assert "OLLAMA_NO_CLOUD" in html, "the person's own switch is shown, just never written"


# -- 3. the editor install: a diff first, then one write ---------------------------------------
def test_ides_lists_every_host_and_shows_the_diff_before_anything_is_written(
    tmp_path: Path, hw: Any
) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    views = h.plugin()._hosts.views()
    assert {view.host for view in views} == {"claude-desktop", "cline", "opencode", "vscode"}
    assert all(view.errors == () for view in views), [view.errors for view in views]
    html = h.plugin().page(csrf=CSRF, step=3)
    assert "headwaters" in html
    assert 'action="/wizard/host/apply"' in html
    assert not written_files(tmp_path), "planning must not write a config"


def test_host_apply_refuses_a_diff_that_is_not_the_one_shown(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    refusal = h.plugin().act("host_apply", {"host": "vscode", "fingerprint": "deadbeefdeadbeef"})
    assert refusal["ok"] is False
    assert "changed since the page was shown" in str(refusal["error"])
    assert not written_files(tmp_path)


def test_host_apply_writes_once_and_then_reports_there_is_nothing_left(
    tmp_path: Path, hw: Any
) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    view = next(item for item in wizard._view("ides")["hosts"] if item["host"] == "vscode")
    first = wizard.act("host_apply", {"host": "vscode", "fingerprint": view["fingerprint"]})
    assert first["ok"] is True, first
    assert "wrote" in str(first["notice"])
    written = written_files(tmp_path)
    assert written, "the install has to actually write the config"
    second = wizard.act("host_apply", {"host": "vscode", "fingerprint": view["fingerprint"]})
    assert second["ok"] is True, "a stale fingerprint after our own write is still safe"
    assert "nothing was written" in str(second["notice"])
    assert written_files(tmp_path) == written


def test_host_apply_refuses_an_unknown_host(tmp_path: Path) -> None:
    refusal = built(tmp_path).plugin().act("host_apply", {"host": "emacs", "fingerprint": ""})
    assert refusal["ok"] is False
    assert "no host called 'emacs'" in str(refusal["error"])


# -- 4. keys: read-only, plain provider names --------------------------------------------------
def test_keys_lists_provider_names_and_never_a_variable_or_a_value(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw), keys=("openrouter", "nvidia"))
    view = h.plugin()._view("keys")
    assert view["providers"] == ["openrouter", "nvidia"]
    assert view["manage_url"] == "/?section=keys"
    html = h.plugin().page(csrf=CSRF, step=4)
    assert "nvidia" in html
    for forbidden in ("HF_TOKEN", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "sk-"):
        assert forbidden not in html


def test_keys_step_writes_nothing_and_has_no_write_form(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    html = h.plugin().page(csrf=CSRF, step=4)
    assert "/wizard/keys" not in html
    assert h.writer.writes == []
    assert not (tmp_path / "state" / "rules.yaml").exists()


# -- 5. mode: the two editable settings --------------------------------------------------------
def test_mode_set_saves_both_values_once(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    wizard = h.plugin()
    first = wizard.act("mode_set", {"scope": "local", "strategy": "quality_first"})
    assert first["ok"] is True, first
    saved = load_rules()
    assert saved.default_scope == "local"
    assert saved.strategy == "quality_first"
    again = wizard.act("mode_set", {"scope": "local", "strategy": "quality_first"})
    assert again["ok"] is True, "saving the value already in force is not an error"
    assert load_rules().default_scope == "local"


def test_mode_set_refuses_a_scope_or_strategy_the_product_does_not_have(
    tmp_path: Path, hw: Any
) -> None:
    wizard = built(tmp_path, hardware=hardware_only(hw)).plugin()
    bad_scope = wizard.act("mode_set", {"scope": "whatever", "strategy": "cost_saver"})
    assert bad_scope["ok"] is False
    assert "is not a scope" in str(bad_scope["error"])
    bad_strategy = wizard.act("mode_set", {"scope": "local", "strategy": "vibes"})
    assert bad_strategy["ok"] is False
    assert "is not a strategy" in str(bad_strategy["error"])
    assert not (tmp_path / "state" / "rules.yaml").exists()


def test_mode_options_come_from_the_rules_literals_not_a_hand_written_list(
    tmp_path: Path, hw: Any
) -> None:
    view = built(tmp_path, hardware=hardware_only(hw)).plugin()._view("mode")
    assert {"auto", "free", "local", "cloud"} <= set(view["scope_options"])
    assert set(view["strategy_options"]) == {"cost_saver", "quality_first", "primary_first"}


# -- 6. autostart -------------------------------------------------------------------------------
def test_autostart_shows_the_task_and_runs_nothing(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    view = h.plugin()._view("autostart")
    assert view["supported"] is True
    assert "/Create" in " ".join(view["argv"])
    assert "<Task" in view["xml"]
    assert h.runner.calls
    assert set(h.runner.calls[0][:2]) == {"schtasks", "/Query"}
    assert not any("/Create" in call for call in h.runner.calls)


def test_autostart_set_creates_the_task_when_the_person_asks(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    outcome = h.plugin().act("autostart_set", {"on": "1"})
    assert outcome["ok"] is True, outcome
    assert "created" in str(outcome["notice"])
    assert any("/Create" in call for call in h.runner.calls)
    written = list(tmp_path.rglob("*.xml"))
    assert written, "the task definition has to be written into this install"


def test_autostart_is_refused_in_words_off_windows(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw), autostart_platform="linux")
    view = h.plugin()._view("autostart")
    assert view["supported"] is False
    assert "Windows only" in view["note"]
    refusal = h.plugin().act("autostart_set", {"on": "1"})
    assert refusal["ok"] is False
    assert "Windows only" in str(refusal["error"])


def test_a_refused_schtasks_becomes_a_sentence_not_a_crash(tmp_path: Path, hw: Any) -> None:
    h = built(
        tmp_path,
        hardware=hardware_only(hw),
        runner=FakeRunner(code=1),
    )
    refusal = h.plugin().act("autostart_set", {"on": "1"})
    assert refusal["ok"] is False
    assert "schtasks refused" in str(refusal["error"])


# -- 7. the usage report: the shipped seam, its preview, its consent ---------------------------
def test_report_step_says_so_when_the_package_is_not_installed(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    view = h.plugin()._view("report")
    assert view["discovered"] is False
    assert "not installed" in view["note"]
    refusal = h.plugin().act("report_consent", {"on": "1"})
    assert refusal["ok"] is False
    assert "no payload to consent to" in str(refusal["error"])


def test_report_step_shows_the_preview_before_consent_and_records_the_version(
    tmp_path: Path, hw: Any
) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    attach_report(h.registry)
    wizard = h.registry.plugin("wizard", "WizardPlugin")
    view = wizard._view("report")
    assert view["discovered"] is True
    assert view["enabled"] is False, "the report starts off"
    assert view["preview"]
    assert view["preview"]["json"]
    assert view["preview"]["payload_version"]
    html = wizard.page(csrf=CSRF, step=7)
    assert 'action="/wizard/report/consent"' in html
    assert str(view["preview"]["payload_version"]) in html
    outcome = wizard.act("report_consent", {"on": "1"})
    assert outcome["ok"] is True, outcome
    assert f"payload version {view['preview']['payload_version']}" in str(outcome["notice"])
    after = wizard._view("report")
    assert after["enabled"] is True
    assert after["consent_current"] is True


def test_turning_the_report_off_again_is_allowed_and_sends_nothing(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    attach_report(h.registry)
    wizard = h.registry.plugin("wizard", "WizardPlugin")
    assert wizard.act("report_consent", {"on": "1"})["ok"] is True
    off = wizard.act("report_consent", {"on": "0"})
    assert off["ok"] is True, off
    assert wizard._view("report")["enabled"] is False


# -- 8. self-test: what it checked, and what it did not ----------------------------------------
def test_selftest_reports_one_check_per_thing_it_did_and_no_model_call(
    tmp_path: Path, hw: Any
) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    attach_report(h.registry)
    wizard = h.registry.plugin("wizard", "WizardPlugin")
    view = wizard._view("selftest")
    assert view["runs_model"] is False
    assert len(view["checks"]) == 8
    names = [check["name"] for check in view["checks"]]
    assert "the state database round-trips" in names
    assert "Ollama answers" in names
    outcome = wizard.act("selftest", {})
    assert outcome["ok"] is True


def test_selftest_marks_the_checks_it_could_not_run_as_not_applicable(
    tmp_path: Path, hw: Any
) -> None:
    h = built(
        tmp_path,
        hardware=hardware_only(hw),
        ollama=FakeOllama(
            version=None,
            version_error="Ollama did not answer at 127.0.0.1",
            installed_error="Ollama did not answer at 127.0.0.1",
        ),
    )
    view = h.plugin()._view("selftest")
    by_name = {check["name"]: check for check in view["checks"]}
    assert by_name["Ollama answers"]["ok"] is None
    assert by_name["models are installed"]["ok"] is None
    assert view["skipped"] >= 2
    assert view["failed"] <= 1, "an unreachable Ollama is not a failure of everything else"


# -- step 2's pull, including the cloud refusal ------------------------------------------------
def test_recommendations_come_from_the_catalog_profile(tmp_path: Path, hw: Any) -> None:
    catalog = seed_catalog()
    found = recommendations(catalog=catalog, hardware=hw, rules=Rules())
    assert found, "the seed catalog recommends something for a 12 GB profile"
    assert all(rec.model for rec in found)
    assert all(rec.profile == "laptop_12gb" for rec in found)
    assert not any(rec.installed for rec in found)
    marked = recommendations(
        catalog=catalog, hardware=hw, rules=Rules(), installed=(found[0].model,)
    )
    assert any(rec.installed for rec in marked), "an installed model is marked, not hidden"


def test_recommendations_say_nothing_when_there_is_no_profile(tmp_path: Path, hw: Any) -> None:
    catalog = seed_catalog().model_copy(update={"gpu_profiles": {}})
    assert recommendations(catalog=catalog, hardware=hw, rules=Rules()) == ()


def test_a_catalog_cloud_model_is_recommended_with_a_refusal_and_never_pulled(
    tmp_path: Path, hw: Any
) -> None:
    catalog = seed_catalog()
    name = "laptop_12gb"
    profile = catalog.gpu_profiles[name]
    cloud = replace_profile(profile, {"general": ["gpt-oss:120b-cloud"]})
    models = [
        *list(catalog.models),
        type(catalog.models[0])(id="gpt-oss:120b-cloud", provider="ollama", size_gb=65.0),
    ]
    patched = catalog.model_copy(
        update={
            "gpu_profiles": {**catalog.gpu_profiles, name: cloud},
            "models": models,
        }
    )
    found = recommendations(catalog=patched, hardware=hw, rules=Rules())
    clouded = [rec for rec in found if rec.model == "gpt-oss:120b-cloud"]
    assert clouded, "the catalog profile does name the cloud model"
    assert clouded[0].refusal, "and the recommendation carries the refusal"
    assert clouded[0].pullable is False

    h = built(tmp_path, hardware=hardware_only(hw), catalog=patched)
    refusal = h.plugin().act("pull", {"model": "gpt-oss:120b-cloud"})
    assert refusal["ok"] is False
    assert "cloud-proxied model" in str(refusal["error"])
    assert h.ollama.pulled == [], "no request is made for a cloud tag"


def test_a_pull_of_a_catalog_model_hands_the_request_to_ollama(tmp_path: Path, hw: Any) -> None:
    h = built(
        tmp_path,
        hardware=hardware_only(hw),
        ollama=FakeOllama(lines=[progress(5, 10), progress(10, 10)]),
    )
    wizard = h.plugin()
    model = wizard._view("models")["recommendations"][0]["model"]
    outcome = wizard.act("pull", {"model": model})
    assert outcome["ok"] is True, outcome
    wizard._pull.wait(5.0)
    snapshot = wizard._pull.snapshot()
    assert snapshot is not None
    assert snapshot.state == "done"
    assert snapshot.percent == 100.0
    assert h.ollama.pulled
    assert h.ollama.pulled[0][1] == model


def test_a_pull_of_something_that_is_not_in_the_catalog_is_refused(tmp_path: Path, hw: Any) -> None:
    h = built(tmp_path, hardware=hardware_only(hw))
    refusal = h.plugin().act("pull", {"model": "made-up:1b"})
    assert refusal["ok"] is False
    assert "not a model in the catalog" in str(refusal["error"])
    assert h.ollama.pulled == []


def test_a_pull_with_no_ollama_listening_is_an_error_the_person_is_told(tmp_path: Path) -> None:
    from headwaters.core.executor.errors import ProviderError
    from headwaters.core.executor.ollama import pull_stream

    with pytest.raises(ProviderError) as raised:
        list(pull_stream("http://127.0.0.1:11434", "qwen3:14b", transport=DeadTransport()))
    assert raised.value.kind == "connection"
    assert "Ollama did not answer" in explain(raised.value, "http://127.0.0.1:11434")


def test_a_cloud_tag_reaches_no_socket_even_without_a_catalog(tmp_path: Path) -> None:
    h = built(tmp_path, no_catalog=True, pull_lines=())
    refusal = h.plugin().act("pull", {"model": "gpt-oss:120b-cloud"})
    assert refusal["ok"] is False
    assert "not a model in the catalog" in str(refusal["error"])
    manager = h.plugin()._pull
    assert manager.start("http://127.0.0.1:11434", "qwen3:14b-cloud").state == "refused"


# -- helpers ------------------------------------------------------------------------------------
def replace_profile(profile: Any, recommended: dict[str, list[str]]) -> Any:
    """One catalog profile with a different role recommendation, without re-validating it."""
    return profile.model_copy(update={"recommended": recommended})


def test_pull_line_progress_is_none_until_a_layer_reports_a_size() -> None:
    from headwaters.features.wizard.pull import PullJob

    job = PullJob(model="qwen3:14b")
    job.fed(PullLine("pulling manifest"))
    assert job.percent is None, "no size yet is not 0%"
    job.fed(PullLine("downloading", "sha256:a", 100, 25))
    job.fed(PullLine("downloading", "sha256:a", 100, 25))
    assert job.completed == 25, "a repeated digest must not double count"
    assert job.percent == 25.0
    job.fed(PullLine("downloading", "sha256:a", 0, 0))
    assert job.total == 100, "a later line with no size must not erase the known total"


def test_state_records_are_per_step_and_round_trip(tmp_path: Path) -> None:
    h = built(tmp_path)
    record_step(h.db, 2, "done")
    record_step(h.db, 2, "skipped")
    assert step_records(h.db)[2].state == "skipped", "one record per step, the latest wins"


def test_env_plan_is_the_doctors_three_values_and_never_no_cloud() -> None:
    plan = envfix.plan_env(Rules(), {"OLLAMA_NO_CLOUD": "0"}, writer=FakeWriter())
    assert {change.name for change in plan.changes} <= set(envfix.WRITABLE)
    assert "OLLAMA_NO_CLOUD" not in {change.name for change in plan.changes}
    assert any("OLLAMA_NO_CLOUD" in note for note in plan.notes)


def test_autostart_create_is_what_the_gate_calls(tmp_path: Path) -> None:
    runner = FakeRunner(stdout=autostart.task_xml(user="someone", command="headwaters tray"))
    state = autostart.status(runner=runner)
    assert state == autostart.STATUS_ENABLED
    assert runner.calls[0][:3] == ["schtasks", "/Query", "/TN"]


def test_the_wizard_satisfies_its_published_interface(tmp_path: Path) -> None:
    run_contract("WizardPlugin", built(tmp_path).plugin())
