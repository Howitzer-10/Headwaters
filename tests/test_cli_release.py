"""`headwaters release-gate` and `headwaters release-docs` (HDW-407).

The exit code is the contract: 0 ready, 1 not ready, 2 unusable evidence or repo root.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.cli_support import invoke, invoke_json
from tests.test_release_gate import GOOD_STEPS

REPO_ROOT = Path(__file__).resolve().parents[1]


def full_evidence(tmp_path: Path) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"version": 1, "steps": GOOD_STEPS}), encoding="utf-8", newline="\n")
    return path


def checkout(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (tmp_path / "headwaters").mkdir()
    return tmp_path


def test_the_gate_refuses_to_call_this_checkout_ready() -> None:
    result = invoke("release-gate", "--repo-root", str(REPO_ROOT))
    assert result.exit_code == 1, result.output
    assert "machine checks: 13/13 passed" in result.output
    assert "owner steps: 0/11 recorded" in result.output
    assert "NOT READY" in result.output
    assert "BLOCKED  install-12gb" in result.output


def test_the_gate_exits_zero_once_every_owner_step_is_recorded(tmp_path: Path) -> None:
    result = invoke(
        "release-gate",
        "--repo-root",
        str(REPO_ROOT),
        "--evidence",
        str(full_evidence(tmp_path)),
    )
    assert result.exit_code == 0, result.output
    assert "READY" in result.output
    assert "owner steps: 11/11 recorded" in result.output


def test_the_gate_reports_itself_as_json(tmp_path: Path) -> None:
    result, data = invoke_json(
        "release-gate",
        "--repo-root",
        str(REPO_ROOT),
        "--evidence",
        str(full_evidence(tmp_path)),
    )
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["summary"] == {
        "checks": 13,
        "checks_failed": 0,
        "steps": 11,
        "steps_blocked": 0,
    }


def test_the_json_report_is_not_ready_without_evidence() -> None:
    result, data = invoke_json("release-gate", "--repo-root", str(REPO_ROOT))
    assert result.exit_code == 1
    assert data["ok"] is False
    assert data["summary"]["steps_blocked"] == 11


def test_a_directory_that_is_not_a_checkout_exits_two(tmp_path: Path) -> None:
    result = invoke("release-gate", "--repo-root", str(tmp_path))
    assert result.exit_code == 2
    outcome, data = invoke_json("release-gate", "--repo-root", str(tmp_path))
    assert outcome.exit_code == 2
    assert data["error"]["code"] == "not_a_checkout"


def test_broken_evidence_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text('{"version": 9, "steps": {}}', encoding="utf-8", newline="\n")
    result = invoke("release-gate", "--repo-root", str(REPO_ROOT), "--evidence", str(path))
    assert result.exit_code == 2
    assert "version" in result.output
    outcome, data = invoke_json(
        "release-gate", "--repo-root", str(REPO_ROOT), "--evidence", str(path)
    )
    assert outcome.exit_code == 2
    assert data["error"]["code"] == "bad_evidence"


def test_the_committed_pages_need_no_write() -> None:
    result = invoke("release-docs", "--root", str(REPO_ROOT))
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "up to date"


def test_docs_need_a_checkout(tmp_path: Path) -> None:
    result = invoke("release-docs", "--root", str(tmp_path))
    assert result.exit_code == 2
    assert "not a Headwaters source checkout" in result.output


def test_a_missing_page_fails_then_write_fixes_it(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    first = invoke("release-docs", "--root", str(root))
    assert first.exit_code == 1
    assert "stale: docs/hosts/vscode.md" in first.output
    assert "run with --write" in first.output

    fixed = invoke("release-docs", "--root", str(root), "--write")
    assert fixed.exit_code == 0, fixed.output
    assert "wrote docs/hosts/vscode.md" in fixed.output

    again, data = invoke_json("release-docs", "--root", str(root))
    assert again.exit_code == 0
    assert data["ok"] is True
    assert data["stale"] == []


def test_the_json_write_lists_what_it_wrote(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    result, data = invoke_json("release-docs", "--root", str(root), "--write")
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["stale"]
    assert data["written"] == data["stale"]
    assert "docs/hosts/index.md" in data["written"]
    assert (root / "docs/hosts/index.md").is_file()
