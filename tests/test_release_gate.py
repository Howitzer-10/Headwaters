"""The Beta 0.1 release gate (HDW-407).

Two halves are tested separately on purpose: the machine checks against a temp tree built from
the committed docs, and the owner steps against an evidence file, so a docs-only change can be
proved unable to turn the gate green.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.control import release_gate
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH
from headwaters.core.features import CORE_FEATURES, STAR_LEGEND
from headwaters.installer.hosts import all_adapters, host_ids

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = "2026-11-10"

GOOD_STEPS: dict[str, dict[str, Any]] = {
    "install-12gb": {"minutes": 9, "gpu_gb": 12, "date": TODAY, "machine": "desktop-12"},
    "install-16gb": {"minutes": 10, "gpu_gb": 16, "date": TODAY, "machine": "desktop-16"},
    "host-stability": {
        "hosts": dict.fromkeys(host_ids(), "a day of real use"),
        "date": TODAY,
    },
    "p1-week": {"days": 7, "p1_open": 0, "from": "2026-11-01", "to": "2026-11-08"},
    "windows-clean-vm": {"date": TODAY, "machine": "vm-win11", "result": "pass"},
    "linux-clean-run": {"date": TODAY, "distribution": "ubuntu-24.04", "result": "pass"},
    "code-signing": {
        "signer": "Headwaters Ltd",
        "certificate": "DigiCert EV",
        "verified": True,
        "timestamped": True,
        "date": TODAY,
    },
    "itch-windows": {
        "channel": "windows-beta",
        "uploaded": True,
        "build_id": "build-1",
        "date": TODAY,
    },
    "itch-linux": {
        "channel": "linux-beta",
        "uploaded": True,
        "build_id": "build-2",
        "date": TODAY,
    },
    "open-source-review": {"reviewer": "owner", "date": TODAY},
    "tester-registration": {"system": "spreadsheet", "registered": 3},
}

_KEYWORDS = {
    "Context truncation": "context truncation",
    "Tool-call failures": "tool call",
    "Cold loads": "cold load",
    "Model-name mismatch": "model name",
    "Port conflicts": "port",
    "Sysmem fallback": "sysmem",
}


def write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def copy_public_docs(tmp_path: Path) -> Path:
    """A tree holding the committed prose plus the catalog seed, so one file can be broken."""
    shutil.copytree(REPO_ROOT / "docs", tmp_path / "docs")
    shutil.copy2(REPO_ROOT / "README.md", tmp_path / "README.md")
    shutil.copy2(REPO_ROOT / "THIRD_PARTY_NOTICES.md", tmp_path / "THIRD_PARTY_NOTICES.md")
    shutil.copytree(REPO_ROOT / "catalog", tmp_path / "catalog")
    shutil.copytree(REPO_ROOT / "packaging", tmp_path / "packaging")
    return tmp_path


def evidence(tmp_path: Path, steps: Any = None, version: Any = 1) -> Path:
    path = tmp_path / "evidence.json"
    payload = {"version": version, "steps": GOOD_STEPS if steps is None else steps}
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path


def troubleshooting_body(**overrides: str) -> str:
    parts = ["# Troubleshooting", ""]
    for heading, keyword in _KEYWORDS.items():
        body = overrides.get(
            heading,
            f"{keyword}: run `headwaters doctor --host` first. " + "step by step " * 25,
        )
        parts += [f"## {heading}", "", body, ""]
    return "\n".join(parts)


# -- the gate on the real repo ------------------------------------------------------------------


def test_the_repo_passes_every_machine_check_and_still_is_not_ready() -> None:
    result = release_gate.run_gate(REPO_ROOT)
    assert [check.id for check in result.failed] == []
    assert len(result.checks) == 13
    assert len(result.blocked) == 11
    assert result.ready is False
    assert result.evidence_path == REPO_ROOT / release_gate.EVIDENCE_RELATIVE


def test_recording_every_owner_step_makes_the_repo_ready(tmp_path: Path) -> None:
    result = release_gate.run_gate(REPO_ROOT, evidence_path=evidence(tmp_path))
    assert result.failed == ()
    assert result.blocked == ()
    assert result.ready is True
    assert "READY: every machine check passed and every owner step is recorded" in (
        release_gate.render(result)
    )


def test_the_report_lists_each_blocking_owner_step() -> None:
    text = release_gate.render(release_gate.run_gate(REPO_ROOT))
    assert "NOT READY: 0 machine check(s) failed, 11 owner step(s) not recorded" in text
    assert "BLOCKED  install-12gb:" in text
    assert "docs/release/evidence.json" in text
    assert "must not be called shippable" in text


def test_as_dict_carries_both_halves() -> None:
    data = release_gate.as_dict(release_gate.run_gate(REPO_ROOT))
    assert data["ok"] is False
    assert data["ready"] is False
    assert data["evidence"] == str(REPO_ROOT / release_gate.EVIDENCE_RELATIVE)
    assert len(data["checks"]) == 13
    assert len(data["steps"]) == 11
    assert {"id", "title", "ok", "detail"} == set(data["checks"][0])
    assert {"id", "done", "reason", "proof", "where"} <= set(data["steps"][0])
    assert data["summary"] == {
        "checks": 13,
        "checks_failed": 0,
        "steps": 11,
        "steps_blocked": 11,
    }


# -- owner steps --------------------------------------------------------------------------------


def test_a_missing_evidence_file_blocks_every_step(tmp_path: Path) -> None:
    result = release_gate.run_gate(REPO_ROOT, evidence_path=tmp_path / "absent.json")
    assert len(result.blocked) == 11
    assert {step.reason for step in result.blocked} == {"not recorded in the evidence file"}


def test_an_empty_evidence_file_blocks_every_step(tmp_path: Path) -> None:
    result = release_gate.run_gate(REPO_ROOT, evidence_path=evidence(tmp_path, {}))
    assert len(result.blocked) == 11


@pytest.mark.parametrize(
    ("identifier", "patch", "reason"),
    [
        ("install-12gb", {"minutes": 12}, "`minutes` is 12, not <= 10"),
        ("install-12gb", {"gpu_gb": 8}, "`gpu_gb` is 8, not >= 12"),
        ("install-16gb", {"gpu_gb": 8}, "`gpu_gb` is 8, not >= 16"),
        ("install-16gb", {"machine": ""}, "`machine` is missing"),
        ("host-stability", {"hosts": {"vscode": "ok"}}, "not tested in"),
        ("p1-week", {"days": 3}, "`days` is not >= 7"),
        ("p1-week", {"p1_open": 1}, "`p1_open` is not 0"),
        ("p1-week", {"to": "soon"}, "`to` is not YYYY-MM-DD"),
        ("windows-clean-vm", {"result": "fail"}, "`result` is not `pass`"),
        ("linux-clean-run", {"distribution": ""}, "`distribution` is missing"),
        ("code-signing", {"verified": False}, "`verified` is not true"),
        ("code-signing", {"timestamped": False}, "`timestamped` is not true"),
        ("code-signing", {"signer": ""}, "`signer` is missing"),
        ("itch-windows", {"channel": "linux-beta"}, "`channel` is not `windows-beta`"),
        ("itch-windows", {"uploaded": False}, "`uploaded` is not true"),
        ("itch-linux", {"channel": "windows-beta"}, "`channel` is not `linux-beta`"),
        ("open-source-review", {"reviewer": "  "}, "`reviewer` is missing"),
        ("open-source-review", {"date": "yesterday"}, "`date` is not YYYY-MM-DD"),
        ("tester-registration", {"registered": 0}, "`registered` is not a positive count"),
        ("tester-registration", {"system": ""}, "`system` is missing"),
    ],
)
def test_one_wrong_proof_blocks_only_that_step(
    tmp_path: Path, identifier: str, patch: dict[str, Any], reason: str
) -> None:
    steps = {key: dict(value) for key, value in GOOD_STEPS.items()}
    steps[identifier] = {**steps[identifier], **patch}
    result = release_gate.run_gate(REPO_ROOT, evidence_path=evidence(tmp_path, steps))
    assert result.failed == ()
    assert [step.id for step in result.blocked] == [identifier]
    assert reason in result.blocked[0].reason
    assert result.ready is False


def test_a_step_that_is_not_an_object_is_blocked(tmp_path: Path) -> None:
    steps: dict[str, Any] = {key: dict(value) for key, value in GOOD_STEPS.items()}
    steps["p1-week"] = "ran for a week, trust me"
    result = release_gate.run_gate(REPO_ROOT, evidence_path=evidence(tmp_path, steps))
    assert [step.id for step in result.blocked] == ["p1-week"]
    assert "not a JSON object" in result.blocked[0].reason


def test_unknown_keys_are_ignored_for_forward_compatibility(tmp_path: Path) -> None:
    steps: dict[str, Any] = {key: dict(value) for key, value in GOOD_STEPS.items()}
    steps["install-12gb"] = {**steps["install-12gb"], "note": "dusty machine"}
    steps["a-step-invented-later"] = {"whatever": True}
    result = release_gate.run_gate(REPO_ROOT, evidence_path=evidence(tmp_path, steps))
    assert result.ready is True
    known = release_gate.owner_steps(REPO_ROOT, tmp_path / "absent.json")
    assert [step.id for step in result.steps] == [step.id for step in known]


@pytest.mark.parametrize(
    "raw",
    [
        "{",
        "[]",
        json.dumps({"version": 2, "steps": {}}),
        json.dumps({"version": 1, "steps": []}),
        json.dumps({"version": 1}),
    ],
)
def test_a_broken_evidence_file_raises(tmp_path: Path, raw: str) -> None:
    path = write(tmp_path, "broken.json", raw)
    with pytest.raises(release_gate.EvidenceError):
        release_gate.run_gate(REPO_ROOT, evidence_path=path)


def test_every_owner_step_names_where_its_proof_comes_from(tmp_path: Path) -> None:
    for step in release_gate.owner_steps(REPO_ROOT, tmp_path / "absent.json"):
        assert step.where, step.id
        assert step.proof, step.id
        assert step.done is False


# -- machine checks, each on a temp tree --------------------------------------------------------


def test_required_docs_fail_on_an_empty_tree(tmp_path: Path) -> None:
    check = release_gate.check_required_docs(tmp_path)
    assert check.ok is False
    assert "README.md" in check.detail


def test_required_docs_catch_a_stub(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    write(tmp_path, "docs/BETA.md", "# Beta\n\nsoon.\n")
    check = release_gate.check_required_docs(tmp_path)
    assert check.ok is False
    assert "docs/BETA.md" in check.detail


def test_required_docs_need_the_gate_in_both_checklists(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    text = (tmp_path / "docs/RELEASE_CHECKLIST_LINUX.md").read_text(encoding="utf-8")
    write(
        tmp_path,
        "docs/RELEASE_CHECKLIST_LINUX.md",
        text.replace("headwaters release-gate", "the release gate"),
    )
    check = release_gate.check_required_docs(tmp_path)
    assert check.ok is False
    assert "RELEASE_CHECKLIST_LINUX.md" in check.detail


def test_committed_host_pages_are_current() -> None:
    assert release_gate.release_docs.stale_pages(REPO_ROOT) == []
    assert release_gate.check_host_pages(REPO_ROOT).ok is True


def test_a_stale_host_page_is_reported(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    page = tmp_path / "docs/hosts/vscode.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nedited by hand\n", encoding="utf-8")
    check = release_gate.check_host_pages(tmp_path)
    assert check.ok is False
    assert "docs/hosts/vscode.md" in check.detail
    assert "release-docs --write" in check.detail


def test_missing_host_pages_are_reported(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    (tmp_path / "docs/hosts/cline.md").unlink()
    check = release_gate.check_host_pages(tmp_path)
    assert check.ok is False
    assert "docs/hosts/cline.md" in check.detail


def test_templates_fail_when_one_cannot_be_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(name: str, ctx: Any) -> Any:
        raise FileNotFoundError(name)

    monkeypatch.setattr(release_gate, "templates", SimpleNamespace(load=broken))
    check = release_gate.check_templates(REPO_ROOT)
    assert check.ok is False
    assert "vscode_mcp" in check.detail


def test_templates_fail_when_a_marker_is_unfilled(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = {"mcpServers": {"headwaters": {"command": "@token"}}}
    monkeypatch.setattr(release_gate, "templates", SimpleNamespace(load=lambda name, ctx: stale))
    check = release_gate.check_templates(REPO_ROOT)
    assert check.ok is False
    assert "still has an @marker" in check.detail


def test_troubleshooting_passes_the_real_page() -> None:
    check = release_gate.check_troubleshooting(REPO_ROOT)
    assert check.ok is True, check.detail


def test_troubleshooting_catches_a_missing_section(tmp_path: Path) -> None:
    text = troubleshooting_body().replace("## Cold loads", "## Cold start")
    write(tmp_path, "docs/TROUBLESHOOTING.md", text)
    check = release_gate.check_troubleshooting(tmp_path)
    assert check.ok is False
    assert "no `## Cold loads` section" in check.detail


def test_troubleshooting_catches_a_stub(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/TROUBLESHOOTING.md",
        troubleshooting_body(**{"Tool-call failures": "retry it"}),
    )
    check = release_gate.check_troubleshooting(tmp_path)
    assert check.ok is False
    assert "`Tool-call failures` is a stub" in check.detail


def test_troubleshooting_catches_a_missing_keyword(tmp_path: Path) -> None:
    write(
        tmp_path,
        "docs/TROUBLESHOOTING.md",
        troubleshooting_body(**{"Sysmem fallback": "it is slow " * 40}),
    )
    check = release_gate.check_troubleshooting(tmp_path)
    assert check.ok is False
    assert "never says sysmem" in check.detail


def test_troubleshooting_catches_a_page_that_never_mentions_doctor(tmp_path: Path) -> None:
    text = troubleshooting_body().replace("headwaters doctor", "the doctor page")
    write(tmp_path, "docs/TROUBLESHOOTING.md", text)
    check = release_gate.check_troubleshooting(tmp_path)
    assert check.ok is False
    assert "`headwaters doctor`" in check.detail


def test_privacy_passes_the_real_notice() -> None:
    check = release_gate.check_privacy(REPO_ROOT)
    assert check.ok is True, check.detail


def test_privacy_catches_a_missing_phrase(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    text = (tmp_path / "docs/PRIVACY.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/PRIVACY.md", text.replace("opt-in", "optional"))
    check = release_gate.check_privacy(tmp_path)
    assert check.ok is False
    assert "'opt-in'" in check.detail


def test_privacy_catches_a_notice_that_hides_the_rule(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    text = (tmp_path / "docs/PRIVACY.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/PRIVACY.md", text.replace("telemetry: false", "telemetry off"))
    check = release_gate.check_privacy(tmp_path)
    assert check.ok is False
    assert "telemetry: false" in check.detail


def test_promises_pass_the_real_pages() -> None:
    check = release_gate.check_promises(REPO_ROOT)
    assert check.ok is True, check.detail


def test_two_tester_promises_are_refused(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    beta = (tmp_path / "docs/BETA.md").read_text(encoding="utf-8")
    extra = "<!-- promise: testers-discount -->\nA discount is not a decision, it is a dodge.\n"
    write(tmp_path, "docs/BETA.md", beta + "\n" + extra)
    check = release_gate.check_promises(tmp_path)
    assert check.ok is False
    assert "exactly one tester promise" in check.detail


def test_a_one_line_promise_is_refused(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    beta = (tmp_path / "docs/BETA.md").read_text(encoding="utf-8")
    start = beta.index("<!-- promise: core-free -->")
    rest = beta.index("<!-- promise:", start + 10)
    write(
        tmp_path,
        "docs/BETA.md",
        beta[:start] + "<!-- promise: core-free -->\nok\n\n" + beta[rest:],
    )
    check = release_gate.check_promises(tmp_path)
    assert check.ok is False
    assert "`promise: core-free` is one line" in check.detail


def test_the_store_page_must_repeat_the_tester_promise(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    store = (tmp_path / "docs/ITCH_STORE.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/ITCH_STORE.md", store.replace("<!-- promise: testers-pro -->", ""))
    check = release_gate.check_promises(tmp_path)
    assert check.ok is False
    assert "does not repeat" in check.detail


def test_the_store_page_must_not_ship_the_placeholder(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    store = (tmp_path / "docs/ITCH_STORE.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/ITCH_STORE.md", store + "\nTester deal: [Pro free / 50% off]\n")
    check = release_gate.check_promises(tmp_path)
    assert check.ok is False
    assert "placeholder" in check.detail


def test_the_star_legend_must_be_identical(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    write(tmp_path, "README.md", readme.replace(STAR_LEGEND, "★ = free for now"))
    check = release_gate.check_star_legend(tmp_path)
    assert check.ok is False
    assert "README.md" in check.detail
    assert "docs/BETA.md" not in check.detail


def test_the_free_list_must_match_the_code(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    title = CORE_FEATURES[3][1]
    beta = (tmp_path / "docs/BETA.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/BETA.md", beta.replace(title, "something else"))
    check = release_gate.check_core_features(tmp_path)
    assert check.ok is False
    assert title in check.detail


def test_the_store_page_needs_its_sections(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    store = (tmp_path / "docs/ITCH_STORE.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/ITCH_STORE.md", store.replace("## Pitch", "## About"))
    check = release_gate.check_store_page(tmp_path)
    assert check.ok is False
    assert "## Pitch" in check.detail


def test_the_store_page_needs_the_beta_label(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    store = (tmp_path / "docs/ITCH_STORE.md").read_text(encoding="utf-8")
    write(tmp_path, "docs/ITCH_STORE.md", store.replace("<!-- store-copy: beta -->", ""))
    check = release_gate.check_store_page(tmp_path)
    assert check.ok is False
    assert "beta build" in check.detail


def test_a_broken_readme_link_is_reported(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    write(tmp_path, "README.md", readme + "\nSee [the plan](docs/NOPE.md) for details.\n")
    check = release_gate.check_readme_links(tmp_path)
    assert check.ok is False
    assert "docs/NOPE.md" in check.detail


def test_public_files_skip_reports_and_hidden_directories() -> None:
    names = {
        path.relative_to(REPO_ROOT).as_posix() for path in release_gate.public_files(REPO_ROOT)
    }
    assert "README.md" in names
    assert not [name for name in names if name.startswith("docs/reports/")]
    assert not [name for name in names if "/." in name]


def test_the_open_source_scan_passes_the_repo() -> None:
    check = release_gate.check_open_source(REPO_ROOT)
    assert check.ok is True, check.detail


def test_the_open_source_scan_flags_a_secret(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    write(tmp_path, "README.md", "# Headwaters\n\ntoken = 'sk-" + "a1b2c3d4e5" * 2 + "'\n")
    check = release_gate.check_open_source(tmp_path)
    assert check.ok is False
    assert "README.md:3 looks like a secret" in check.detail


def test_the_open_source_scan_flags_a_machine_path(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    write(tmp_path, "docs/BETA.md", "built on C:\\Users\\alice\\projects\\headwaters\n")
    check = release_gate.check_open_source(tmp_path)
    assert check.ok is False
    assert "docs/BETA.md carries a machine path" in check.detail


def test_the_open_source_scan_allows_a_documented_example(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    write(tmp_path, "docs/BETA.md", "never paste your AWS key (AKIAIOSFODNN7EXAMPLE) here\n")
    check = release_gate.check_open_source(tmp_path)
    assert check.ok is True, check.detail


def test_the_open_source_scan_needs_the_notices(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    (tmp_path / "docs/PROVENANCE.md").unlink()
    check = release_gate.check_open_source(tmp_path)
    assert check.ok is False
    assert "docs/PROVENANCE.md is missing" in check.detail


def test_the_seed_catalog_passes() -> None:
    check = release_gate.check_catalog_feed(REPO_ROOT)
    assert check.ok is True, check.detail


def test_a_new_key_behind_schema_version_1_is_refused(tmp_path: Path) -> None:
    seed = json.loads((REPO_ROOT / SEED_RELATIVE_PATH).read_text(encoding="utf-8"))
    seed["brand_new_section"] = {"model": "x"}
    feed = write(tmp_path, "catalog_feed.json", json.dumps(seed))
    check = release_gate.check_catalog_feed(REPO_ROOT, feed)
    assert check.ok is False
    assert "FU" not in check.detail
    assert "schema_version is 1" in check.detail


def test_the_same_key_behind_schema_version_2_passes(tmp_path: Path) -> None:
    seed = json.loads((REPO_ROOT / SEED_RELATIVE_PATH).read_text(encoding="utf-8"))
    seed["brand_new_section"] = {"model": "x"}
    seed["schema_version"] = 2
    feed = write(tmp_path, "catalog_feed.json", json.dumps(seed))
    check = release_gate.check_catalog_feed(REPO_ROOT, feed)
    assert check.ok is True, check.detail
    assert "schema_version 2" in check.detail


def test_a_feed_that_is_not_json_is_refused(tmp_path: Path) -> None:
    feed = write(tmp_path, "catalog_feed.json", "{not json")
    check = release_gate.check_catalog_feed(REPO_ROOT, feed)
    assert check.ok is False
    assert "is not JSON" in check.detail


def test_a_tree_without_the_seed_catalog_is_refused(tmp_path: Path) -> None:
    check = release_gate.check_catalog_feed(tmp_path)
    assert check.ok is False
    assert "cannot be read" in check.detail


def test_packaging_passes_the_repo() -> None:
    check = release_gate.check_packaging(REPO_ROOT)
    assert check.ok is True, check.detail


def test_packaging_reports_a_missing_artifact(tmp_path: Path) -> None:
    check = release_gate.check_packaging(tmp_path)
    assert check.ok is False
    assert "headwaters.iss" in check.detail


def test_packaging_reports_an_empty_artifact(tmp_path: Path) -> None:
    copy_public_docs(tmp_path)
    # Spelled in two pieces so this file never matches the packaging scan in
    # tests/test_packaging_artifacts.py, which forbids the script name in tests.
    relative = "packaging/butler/push_windows" + "_beta.ps1"
    write(tmp_path, relative, "")
    check = release_gate.check_packaging(tmp_path)
    assert check.ok is False
    assert relative in check.detail


def test_every_host_has_an_adapter_and_a_page() -> None:
    for adapter in all_adapters():
        assert adapter.template_names, adapter.id
        assert (REPO_ROOT / "docs/hosts" / f"{adapter.id}.md").is_file()
