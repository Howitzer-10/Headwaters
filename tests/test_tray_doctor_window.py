"""HDW-504 follow-up: what the Doctor window is handed, checked with no display.

The window itself needs a screen; its payload does not. These tests build real
:class:`headwaters.control.doctor.Report` objects, summarise them exactly as the tray does, and
read the lines the window would insert. The owner's complaint - the window showing raw strings
such as "no answer from http://127.0.0.1:11434: [WinError 10061] connection refused" and
"core.autocrlf=true" - is what these assertions keep out of the window from now on.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from headwaters.control.doctor import HOST_CONFIG_FIX, Check, Report
from headwaters.features.tray import doctor_window
from headwaters.features.tray.doctor_window import (
    ALL_CLEAR,
    DEVELOPER,
    DEVELOPER_IDS,
    DEVELOPER_LABEL,
    GROUP_LABELS,
    NEEDS_DOING,
    OPTIONAL,
    OPTIONAL_IDS,
    PLAIN_CHECKS,
    DoctorEntry,
    DoctorView,
    _content,
    summarise,
)

# The ids that can actually stop a request working, from every Check(...) Doctor.run builds
# plus the three _host_checks adds. Spelled out here so a new check id has to be classified on
# purpose rather than defaulting into this list by accident.
REAL_IDS = [
    "environment",
    "install",
    "ollama",
    "private_mode",
    "gpu",
    "daemon",
    "daemon_token",
    "keys",
    "mcp_launch",
    "host_round_trip",
    "host_tool_call",
    "host_scope",
]

OLLAMA_DETAIL = "no answer from http://127.0.0.1:11434: [WinError 10061] connection refused"
OLLAMA_FIX = (
    "Install Ollama and start it, or set tiers.local.host in rules.yaml to the port it listens on."
)
GIT_FIX = (
    "core.autocrlf=true rewrites every text file to CRLF on checkout; a patch or hash made here "
    "then differs from a Linux CI run. Set core.autocrlf=input."
)
HOSTS_DETAIL = "vscode: the MCP entry is missing"
# What the window shows for the same Ollama finding, and the one quiet line the developer-only
# checks collapse into: neither is ever the raw string above.
OLLAMA_MEANING = "Headwaters uses Ollama to run models on this computer, and it isn't answering."
OLLAMA_PLAIN_FIX = "Install and start Ollama, then reopen Doctor."
DEVELOPER_LINE = DEVELOPER_LABEL.format(count=2)


def check(id: str, status: str, **kwargs: Any) -> Check:
    """A check with a title derived from its id and a detail, unless the test says otherwise."""
    kwargs.setdefault("title", id.replace("_", " ").title())
    kwargs.setdefault("detail", f"{id} detail")
    return Check(id=id, status=status, **kwargs)


def users_report() -> Report:
    """The five-check report the tray showed the owner, with the text ``doctor.py`` holds."""
    return Report(
        checks=(
            Check("ollama", "Ollama", "warn", OLLAMA_DETAIL, fix=OLLAMA_FIX),
            Check("private_mode", "Private Mode", "skip", "off (scope cloud)"),
            Check("gpu", "GPU", "skip", "the GPU probe is unavailable on this machine"),
            Check("git", "Git line endings", "warn", "core.autocrlf=true", fix=GIT_FIX),
            Check("host_configs", "Host config files", "warn", HOSTS_DETAIL, fix=HOST_CONFIG_FIX),
        )
    )


# --------------------------------------------------------------------------- the owner's report


def test_his_five_checks_are_one_problem_and_a_developer_note() -> None:
    view = summarise(users_report())
    # Ollama is the only line that can stop a request working; git and the host configs are
    # developer housekeeping, and both skips were never problems at all.
    assert view.headline == "One thing to check"
    assert [entry.id for entry in view.entries] == ["ollama", "git", "host_configs"]
    assert [entry.title for entry in view.needs_doing] == ["Ollama isn't running"]
    assert view.optional == ()
    assert [entry.id for entry in view.developer] == ["git", "host_configs"]
    assert [entry.id for entry in view.entries if entry.id in {"private_mode", "gpu"}] == []


def test_the_window_shows_plain_words_for_a_mapped_check() -> None:
    ollama = summarise(users_report()).needs_doing[0]
    assert ollama.detail == OLLAMA_MEANING
    assert ollama.fix == OLLAMA_PLAIN_FIX
    assert OLLAMA_DETAIL not in ollama.detail
    assert ollama.fix != OLLAMA_FIX


def test_a_developer_check_keeps_its_raw_detail_for_the_log() -> None:
    """The footer hides these lines; the entry still carries what ``headwaters doctor`` reported."""
    git, hosts = summarise(users_report()).developer
    assert git.detail == "core.autocrlf=true"
    assert git.fix == GIT_FIX
    assert hosts.detail == HOSTS_DETAIL
    assert hosts.fix == HOST_CONFIG_FIX


def test_the_headline_counts_only_what_needs_doing() -> None:
    two = summarise(Report(checks=(users_report().checks[0], check("daemon", "fail"))))
    one = summarise(Report(checks=(users_report().checks[0],)))
    three = summarise(
        Report(
            checks=(
                users_report().checks[0],
                check("daemon", "fail"),
                check("keys", "warn"),
            )
        )
    )
    notes_only = summarise(
        Report(
            checks=(Check("git", "Git line endings", "warn", "core.autocrlf=true", fix=GIT_FIX),)
        )
    )
    assert one.headline == "One thing to check"
    assert two.headline == "Two things to check"
    assert three.headline == "3 things to check"
    assert notes_only.headline == ALL_CLEAR
    assert notes_only.all_clear  # a developer note alone still means nothing needs doing


def test_developer_notes_are_still_described_even_when_nothing_needs_doing() -> None:
    """An all-clear report never opens the window; the payload still describes the notes."""
    view = summarise(Report(checks=(check("git", "warn"),)))
    assert view.all_clear
    assert [entry.group for entry in view.entries] == [DEVELOPER]


# --------------------------------------------------------------------------- the classification


@pytest.mark.parametrize("status", ["warn", "fail"])
@pytest.mark.parametrize("check_id", sorted(DEVELOPER_IDS))
def test_a_developer_only_check_is_a_footer_note_whatever_its_status(
    check_id: str, status: str
) -> None:
    view = summarise(Report(checks=(check(check_id, status),)))
    assert [entry.group for entry in view.entries] == [DEVELOPER]
    assert view.all_clear
    assert view.headline == ALL_CLEAR


@pytest.mark.parametrize("status", ["warn", "fail"])
@pytest.mark.parametrize("check_id", sorted(OPTIONAL_IDS - DEVELOPER_IDS))
def test_an_optional_check_that_cannot_stop_a_request_is_a_note(check_id: str, status: str) -> None:
    view = summarise(Report(checks=(check(check_id, status),)))
    assert [entry.group for entry in view.entries] == [OPTIONAL]
    assert view.all_clear


@pytest.mark.parametrize("check_id", REAL_IDS)
def test_a_check_that_can_stop_a_request_is_needs_doing(check_id: str) -> None:
    view = summarise(Report(checks=(check(check_id, "fail"),)))
    assert [entry.group for entry in view.entries] == [NEEDS_DOING]
    assert not view.all_clear
    assert view.headline == "One thing to check"


def test_a_private_mode_warning_is_a_problem_in_plain_words() -> None:
    """Private Mode is a ``skip`` when it is off, so a warn means the promise is actually false."""
    view = summarise(
        Report(
            checks=(
                Check(
                    "private_mode",
                    "Private Mode",
                    "warn",
                    "Ollama can still run a cloud model, which would send your prompt off this PC.",
                    fix="Set OLLAMA_NO_CLOUD=1 and restart Ollama.",
                ),
            )
        )
    )
    assert view.headline == "One thing to check"
    entry = view.needs_doing[0]
    assert entry.title == "Private mode isn't fully private"
    assert entry.detail == PLAIN_CHECKS["private_mode"].meaning
    assert entry.fix == PLAIN_CHECKS["private_mode"].fix


def test_an_id_nobody_classified_is_a_problem_not_a_silent_note() -> None:
    """A check added later must not be demoted, or hidden by a missing plain-English entry."""
    view = summarise(Report(checks=(check("some_future_check", "warn"),)))
    assert [entry.group for entry in view.entries] == [NEEDS_DOING]
    assert view.entries[0].title == "Some Future Check"
    assert view.entries[0].detail == "some_future_check detail"


@pytest.mark.parametrize("check_id", sorted(PLAIN_CHECKS))
def test_every_mapped_check_shows_its_plain_three_sentences(check_id: str) -> None:
    """No mapped check may leak its raw detail or fix into the window."""
    plain = PLAIN_CHECKS[check_id]
    view = summarise(
        Report(checks=(Check(check_id, plain.title, "fail", "raw detail", fix="raw fix"),))
    )
    rows = _content(view)
    assert rows[0] == (view.headline, "headline")
    assert (plain.title, "title") in rows
    assert (plain.meaning, "detail") in rows
    assert (f"Fix: {plain.fix}", "fix") in rows
    assert not any("raw detail" in line or "raw fix" in line for line, _tag in rows)


@pytest.mark.parametrize("check_id", sorted(OPTIONAL_IDS | set(REAL_IDS)))
def test_skip_is_never_a_problem_whatever_the_id(check_id: str) -> None:
    view = summarise(Report(checks=(check(check_id, "skip"),)))
    assert view.entries == ()
    assert view.all_clear
    assert view.headline == ALL_CLEAR


def test_ok_is_never_listed() -> None:
    view = summarise(Report(checks=(check("ollama", "ok"), check("daemon", "ok"))))
    assert view.entries == ()
    assert view.all_clear


# --------------------------------------------------------------------------- the payload details


def test_an_unmapped_entry_with_no_detail_falls_back_to_its_title() -> None:
    view = summarise(Report(checks=(Check("mcp_launch", "MCP launcher", "fail"),)))
    assert view.entries[0].detail == "MCP launcher"


def test_an_entry_with_no_fix_borrows_the_reports_fix_for_the_same_title() -> None:
    """``Report.fixes`` de-duplicates by fix text and keys it by title: that lookup still works."""
    report = Report(
        checks=(
            Check("catalog", "Catalog", "warn", "stale", fix="headwaters catalog update"),
            Check("catalog", "Catalog", "warn", "stale"),
        )
    )
    assert [entry.fix for entry in summarise(report).entries] == [
        "headwaters catalog update",
        "headwaters catalog update",
    ]


def test_two_checks_sharing_one_fix_each_show_it() -> None:
    view = summarise(
        Report(
            checks=(
                Check(
                    "host_tool_call", "Host tool call", "fail", "the call timed out", fix="Retry."
                ),
                Check("host_round_trip", "Host round trip", "fail", "no answer", fix="Retry."),
            )
        )
    )
    assert [entry.fix for entry in view.entries] == ["Retry.", "Retry."]


def test_the_report_order_survives_inside_each_group() -> None:
    view = summarise(
        Report(
            checks=(
                check("git", "warn"),
                check("ollama", "fail"),
                check("host_configs", "warn"),
                check("daemon", "fail"),
            )
        )
    )
    assert [entry.id for entry in view.needs_doing] == ["ollama", "daemon"]
    assert [entry.id for entry in view.developer] == ["git", "host_configs"]
    assert [entry.id for entry in view.entries] == ["ollama", "daemon", "git", "host_configs"]


# --------------------------------------------------------------------------- the window's lines


def test_the_content_badges_the_two_groups_differently() -> None:
    rows = _content(summarise(Report(checks=(users_report().checks[0], check("catalog", "warn")))))
    assert rows[0] == ("One thing to check", "headline")
    assert (GROUP_LABELS[NEEDS_DOING], "group_needs") in rows
    assert (GROUP_LABELS[OPTIONAL], "group_optional") in rows
    assert GROUP_LABELS[NEEDS_DOING] != GROUP_LABELS[OPTIONAL]


def test_the_content_lists_each_problem_then_what_to_do_about_it() -> None:
    rows = _content(summarise(users_report()))
    lines = [line for line, _tag in rows]
    assert lines.index("Needs doing") < lines.index("Ollama isn't running")
    assert lines.index("Ollama isn't running") < lines.index(OLLAMA_MEANING)
    assert lines.index(OLLAMA_MEANING) < lines.index(f"Fix: {OLLAMA_PLAIN_FIX}")
    # The raw check text belongs to the log and the CLI, never to this window.
    assert not any(
        "WinError" in line or "http://" in line or "core.autocrlf" in line for line in lines
    )
    assert not any(line.startswith("What to do:") for line in lines)
    # A model name or a private-mode detail would be a lie here: neither is a problem.
    assert not any(line in lines for line in ("off (scope cloud)", "the GPU probe is unavailable"))


def test_developer_findings_collapse_into_one_quiet_line_at_the_bottom() -> None:
    rows = _content(summarise(users_report()))
    lines = [line for line, _tag in rows]
    assert rows[-1] == (DEVELOPER_LINE, "developer")
    assert lines.count(DEVELOPER_LINE) == 1
    assert "Git line endings" not in lines
    assert "Host config files" not in lines
    assert HOSTS_DETAIL not in lines
    assert GIT_FIX not in lines


def test_every_line_the_window_inserts_has_a_known_style() -> None:
    tags = {
        "headline",
        "blank",
        "group_needs",
        "group_optional",
        "title",
        "detail",
        "fix",
        "developer",
    }
    assert {tag for _line, tag in _content(summarise(users_report()))} <= tags


def test_the_all_clear_content_is_one_sentence() -> None:
    view = summarise(Report(checks=(check("ollama", "ok"),)))
    assert _content(view) == ((ALL_CLEAR, "headline"),)
    assert view.text() == ALL_CLEAR


def test_the_plain_text_fallback_says_the_same_thing_as_the_window() -> None:
    view = summarise(users_report())
    text = view.text()
    assert text.splitlines()[0] == "One thing to check"
    assert OLLAMA_MEANING in text
    assert f"Fix: {OLLAMA_PLAIN_FIX}" in text
    assert "core.autocrlf" not in text
    assert DEVELOPER_LINE in text
    assert "Private Mode" not in text


def test_the_developer_footer_is_not_a_full_entry_in_the_text_fallback() -> None:
    view = summarise(users_report())
    assert "Git line endings" not in view.text()
    assert "Host config files" not in view.text()


def test_a_view_named_in_the_log_is_one_short_line() -> None:
    """``_run_action`` logs ``doctor finished: {view}``, so ``str`` must stay the headline."""
    view = summarise(users_report())
    assert str(view) == "One thing to check"
    assert "\n" not in str(view)


def test_the_payload_is_frozen_so_nothing_downstream_can_rewrite_it() -> None:
    view = summarise(users_report())
    with pytest.raises(FrozenInstanceError):
        view.headline = "something else"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        view.entries[0].group = OPTIONAL  # type: ignore[misc]


def test_a_hand_built_view_is_all_clear_until_something_needs_doing() -> None:
    assert DoctorView(headline=ALL_CLEAR).all_clear
    entry = DoctorEntry("ollama", "Ollama isn't running", NEEDS_DOING, "not running", "start it")
    assert not DoctorView(headline="One thing to check", entries=(entry,)).all_clear


# --------------------------------------------------------------------------- not a dependency


def test_tkinter_is_imported_inside_the_window_function_never_at_module_level() -> None:
    """Importing the tray on a headless machine, or in a test, must not load a GUI toolkit."""
    source = Path(doctor_window.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_level = [node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)]
    assert all("tkinter" not in ast.unparse(node) for node in module_level)
    assert "tkinter" in source  # and the window does use it, inside ``show``
