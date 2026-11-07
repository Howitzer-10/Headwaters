"""HDW-508: the opt-in usage report, the one-shot send, and the product-feedback prompt.

The two accept-when conditions are tests rather than prose: the payload has no free-text field, and
the feature opens no socket while it is off. Both are written to fail if a later change erodes
them — the allow-list test checks every leaf's *kind*, not just the field names, and the silence
test forbids the socket itself rather than trusting a call site to remember the gate.
"""

from __future__ import annotations

import re
import socket
from pathlib import Path
from typing import Any, ClassVar

import httpx
import pytest

from headwaters.control import doctor as doctor_mod
from headwaters.control.status_page import _stars
from headwaters.core import features
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.rules import ReportRules, Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_decisions import DecisionRow, OutcomeRow, insert_decision, save_outcome
from headwaters.core.usage_report import (
    REPORT_FLAG,
    preview_report,
    report_status,
    send_due_report,
    send_report_now,
)
from headwaters.features.usage_report import payload as payload_mod
from headwaters.features.usage_report import send as sender
from headwaters.features.usage_report import state as report_state
from headwaters.features.usage_report import survey
from tests.cli_support import home, invoke, invoke_json

REPO_ROOT = Path(__file__).resolve().parent.parent
DAY = 86_400.0
NOW = 1_760_000_000.0


def catalog() -> Any:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


def registry_for(db: Database, rules: Rules | None = None) -> FeatureRegistry:
    """The production discovery path, with the services ``headwaters report`` gives it."""
    used = rules or Rules()
    registry = FeatureRegistry(
        services={"db": db, "rules": used, "catalog": catalog(), "hardware": lambda: None}
    )
    discover_features(registry)
    registry.set_flags(used.features)
    return registry


def with_endpoint(**report: Any) -> Rules:
    return Rules(report=ReportRules(endpoint="https://collector.example/report", **report))


def add(db: Database, *, ts: float = NOW, surface: str = "cli", scope: str = "auto") -> int:
    return insert_decision(
        db, DecisionRow(surface=surface, scope=scope, strategy="balanced", chosen="qwen:14b", ts=ts)
    )


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HEADATERS_HOME so consent stamps and kv rows never touch the real install."""
    return home(tmp_path, monkeypatch)


# --------------------------------------------------------------------- the shape of the payload


def _rule_for(path: str) -> Any:
    for pattern, rule in payload_mod.FIELD_RULES.items():
        if payload_mod._matches(path, pattern):
            return rule
    raise AssertionError(f"{path} is not in the allow-list at all")


def test_payload_is_exactly_the_allow_list_and_nothing_else(isolated: Path) -> None:
    """The closed key set: literals, three grouped count maps, and one entry per ★ feature."""
    db = open_database()
    registry = registry_for(db)
    built = payload_mod.build({"db": db, "registry": registry, "hardware": lambda: None}, now=NOW)
    flat = payload_mod._flatten(built)

    stars = sorted(spec.id for spec in registry.all() if spec.star and not spec.id.startswith("_"))
    assert len(stars) == 4  # ledger, paid_scopes, tray and this ticket's own package
    expected = {
        "payload_version",
        "kind",
        "generated_on",
        "window_days",
        "app_version",
        "os",
        "gpu_class",
        "requests",
        "active_days",
        "verify_catches",
        "escalations",
        "failover_requests_approx",
        "savings_bucket",
        *(f"engine_counts.{name}" for name in payload_mod.ENGINES),
        *(f"scope_counts.{name}" for name in payload_mod.SCOPES),
        *(f"tier_counts.{name}" for name in payload_mod.TIERS),
        *(f"features_enabled.{name}" for name in stars),
        *(f"features_retained_14d.{name}" for name in stars),
    }
    assert set(flat) == expected


def test_every_leaf_is_a_count_a_bool_a_closed_set_name_or_an_explicit_null(
    isolated: Path,
) -> None:
    """Stronger than the key set: a future ``notes: str`` field fails here, not at the endpoint.

    A string leaf is allowed only where the allow-list names a closed set, a strict pattern or the
    ``gpu`` class — so "some string" cannot slide in under the enum rule.
    """
    db = open_database()
    registry = registry_for(db)
    built = payload_mod.build({"db": db, "registry": registry, "hardware": lambda: None}, now=NOW)
    for path, value in payload_mod._flatten(built).items():
        rule = _rule_for(path)
        if isinstance(value, bool):
            assert rule is bool or rule == "bool-or-null", path
        elif isinstance(value, int):
            assert rule is int, path
            assert value >= 0, path
        elif value is None:
            assert rule in ("bool-or-null", "gpu"), path
        elif isinstance(value, str):
            assert isinstance(rule, frozenset | re.Pattern) or rule == "gpu", (
                f"{path} is a free-text string ({value!r}); the payload carries counts only"
            )
            assert len(value) <= payload_mod.MAX_STRING, path
        else:
            raise AssertionError(f"{path} carries {type(value).__name__}, which no rule allows")


def test_a_new_string_field_is_refused(isolated: Path) -> None:
    """The negative half: adding prose, or a long string in an allowed slot, fails the check."""
    db = open_database()
    registry = registry_for(db)
    built = payload_mod.build({"db": db, "registry": registry, "hardware": lambda: None}, now=NOW)
    payload_mod.check_payload(built)  # the shipped shape passes

    with_notes = dict(built, notes="the user pasted their source code here")
    with pytest.raises(payload_mod.PayloadShapeError, match="outside the allow-list"):
        payload_mod.check_payload(with_notes)

    prose = dict(built, os="windows " + "x" * 60)
    with pytest.raises(payload_mod.PayloadShapeError):
        payload_mod.check_payload(prose)

    unversioned = {key: value for key, value in built.items() if key != "payload_version"}
    with pytest.raises(payload_mod.PayloadShapeError, match=r"missing|payload_version"):
        payload_mod.check_payload(unversioned)


def test_the_payload_carries_one_version_integer(isolated: Path) -> None:
    """An endpoint can reject a shape it does not know: the version is an int, and required."""
    db = open_database()
    registry = registry_for(db)
    built = payload_mod.build({"db": db, "registry": registry, "hardware": lambda: None}, now=NOW)
    assert built["payload_version"] == payload_mod.PAYLOAD_VERSION
    assert isinstance(built["payload_version"], int)
    assert not isinstance(built["payload_version"], bool)
    with pytest.raises(payload_mod.PayloadShapeError, match="payload_version"):
        payload_mod.check_payload(dict(built, payload_version=payload_mod.PAYLOAD_VERSION + 1))


# ------------------------------------------------------------------------ silent while off


def _forbid_socket(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("a socket was opened while the usage report was off")


def test_nothing_opens_a_socket_while_the_report_is_off(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off is provably silent: the socket itself refuses, so no call site can forget a gate."""
    db = open_database()
    add(db)
    registry = registry_for(db, with_endpoint())
    assert registry.enabled(REPORT_FLAG) is False  # default_enabled=False, and no flag is set

    monkeypatch.setattr(socket, "socket", _forbid_socket)
    monkeypatch.setattr(socket, "create_connection", _forbid_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _forbid_socket)
    monkeypatch.setattr(httpx, "Client", _forbid_socket)

    described = report_status(registry)
    assert described["enabled"] is False
    assert described["endpoint_set"] is True
    shown = preview_report(registry)
    assert shown is not None
    assert shown["gate"] == sender.NOT_CONSENTED
    assert send_due_report(registry)["reason"] == sender.OFF
    # The build itself reads the database and the rules only; it never reaches for httpx.
    payload_mod.build({"db": db, "registry": registry, "hardware": lambda: None}, now=NOW)


def test_send_now_refuses_without_an_endpoint_even_when_consented(isolated: Path) -> None:
    db = open_database()
    registry = registry_for(db)
    report_state.record_consent(db, payload_version=payload_mod.PAYLOAD_VERSION)
    outcome = send_report_now(registry)
    assert outcome["sent"] is False
    assert outcome["reason"] == sender.NOT_CONFIGURED


def test_a_changed_payload_version_is_not_consent(isolated: Path) -> None:
    """Consent is for a shape, not for a promise: previewing version 1 does not consent to 2."""
    db = open_database()
    registry = registry_for(db, with_endpoint())
    report_state.record_consent(db, payload_version=0)
    assert send_report_now(registry)["reason"] == sender.NOT_CONSENTED


# ------------------------------------------------------------------------- the one-shot send


class _Response:
    status_code = 204
    text = ""


class _Client:
    """A stand-in for ``httpx.Client``: records the request and answers 204."""

    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_exc: Any) -> bool:
        return False

    def post(self, url: str, *, json: Any = None, headers: Any = None) -> _Response:
        _Client.calls.append({"url": url, "json": json, "headers": dict(headers or {})})
        return _Response()


def test_send_now_delivers_once_without_switching_the_report_on(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = open_database()
    add(db)
    registry = registry_for(db, with_endpoint())
    report_state.record_consent(db, payload_version=payload_mod.PAYLOAD_VERSION)
    _Client.calls = []
    monkeypatch.setattr(httpx, "Client", _Client)

    outcome = send_report_now(registry)
    assert outcome["sent"] is True, outcome
    assert outcome["status_code"] == 204
    assert len(_Client.calls) == 1
    sent = _Client.calls[0]
    assert sent["url"] == "https://collector.example/report"
    assert sent["headers"]["content-type"] == "application/json"
    assert sent["json"]["kind"] == payload_mod.KIND
    payload_mod.check_payload(sent["json"])  # what left is still the allow-listed shape
    assert registry.enabled(REPORT_FLAG) is False  # one-shot, not a switch


def test_send_due_respects_the_interval_and_the_switch(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = open_database()
    registry = registry_for(db, with_endpoint())
    report_state.record_consent(db, payload_version=payload_mod.PAYLOAD_VERSION)
    _Client.calls = []
    monkeypatch.setattr(httpx, "Client", _Client)

    assert send_due_report(registry, now=NOW)["reason"] == sender.OFF

    registry.set_flags({REPORT_FLAG: True})
    assert send_due_report(registry, now=NOW)["sent"] is True
    assert send_due_report(registry, now=NOW + DAY)["reason"] == sender.NOT_DUE
    assert send_due_report(registry, now=NOW + 8 * DAY)["sent"] is True
    assert len(_Client.calls) == 2


def test_a_refused_endpoint_is_reported_not_raised(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = open_database()
    registry = registry_for(db, with_endpoint())
    report_state.record_consent(db, payload_version=payload_mod.PAYLOAD_VERSION)

    def _boom(**_kwargs: Any) -> Any:
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "Client", _boom)
    outcome = send_report_now(registry)
    assert outcome["sent"] is False
    assert outcome["reason"] == sender.UNAVAILABLE
    assert report_state.last_result(db)["reason"] == sender.UNAVAILABLE


def test_the_endpoint_must_be_https() -> None:
    with pytest.raises(ValueError, match="https"):
        ReportRules(endpoint="http://collector.example/report")
    with pytest.raises(ValueError, match="https"):
        ReportRules(endpoint="collector.example/report")


# ------------------------------------------------------------------------ the preview and switch


def test_preview_and_enable_through_the_cli(isolated: Path) -> None:
    """The order the ticket requires: the payload is shown, then the switch can be set."""
    refused = invoke("report", "enable", "--i-have-seen-the-payload")
    assert refused.exit_code == 2
    assert "preview" in refused.stderr

    shown = invoke("report", "preview")
    assert shown.exit_code == 0
    assert '"payload_version": 1' in shown.stdout
    assert '"notes"' not in shown.stdout

    enabled = invoke("report", "enable", "--i-have-seen-the-payload")
    assert enabled.exit_code == 0, enabled.stdout
    _, status = invoke_json("report", "status")
    assert status["enabled"] is True
    assert status["consent_current"] is True

    off = invoke("report", "disable")
    assert off.exit_code == 0
    _, after = invoke_json("report", "status")
    assert after["enabled"] is False
    assert after["consent_current"] is True  # disabling does not revoke the preview


def test_the_switch_cannot_be_turned_on_by_flag_alone(isolated: Path) -> None:
    """``enable`` without ``--i-have-seen-the-payload`` is refused even after a preview."""
    assert invoke("report", "preview").exit_code == 0
    result = invoke("report", "enable")
    assert result.exit_code == 2
    _, status = invoke_json("report", "status")
    assert status["enabled"] is False


def test_report_send_works_with_the_report_off(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Client.calls = []
    monkeypatch.setattr(httpx, "Client", _Client)
    assert invoke("report", "preview").exit_code == 0
    assert invoke("report", "endpoint", "https://collector.example/report").exit_code == 0
    sent = invoke("report", "send")
    assert sent.exit_code == 0, sent.stdout
    assert "sent" in sent.stdout
    assert len(_Client.calls) == 1
    _, status = invoke_json("report", "status")
    assert status["enabled"] is False


def test_status_and_doctor_render_the_row_while_the_report_is_off(isolated: Path) -> None:
    """PRIVACY.md's promise: the toggle is visible before anyone is asked to consent to it."""
    _, status = invoke_json("report", "status")
    assert status["discovered"] is True
    assert status["enabled"] is False
    assert status["endpoint_set"] is False
    assert status["payload_version"] == payload_mod.PAYLOAD_VERSION

    db = open_database()
    row = doctor_mod.Doctor(Rules(), registry=registry_for(db))._usage_report()
    assert row.status == doctor_mod.OK
    assert "off" in row.detail
    assert "no endpoint" in row.detail
    assert "payload version 1" in row.detail


def test_doctor_and_the_status_page_still_name_the_report_when_the_package_is_gone(
    isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removed from discovery: the row stays and says so, rather than vanishing."""
    real = features.pkgutil.iter_modules

    def without_report(path: Any = None, prefix: str = "") -> Any:
        return (module for module in real(path, prefix) if module[1] != "usage_report")

    monkeypatch.setattr(features.pkgutil, "iter_modules", without_report)
    db = open_database()
    registry = FeatureRegistry(services={"db": db, "rules": Rules()})
    assert "headwaters.features.usage_report" not in discover_features(registry)

    described = report_status(registry)
    assert described["discovered"] is False
    assert "not installed" in str(described["note"])
    assert preview_report(registry) is None
    assert send_report_now(registry)["reason"] == "not_installed"

    row = doctor_mod.Doctor(Rules(), registry=registry)._usage_report()
    assert row.status == doctor_mod.OK
    assert "not installed" in row.detail

    stars = {feature_id: label for feature_id, label, _on in _stars(registry)}
    assert REPORT_FLAG in stars
    assert "not installed" in stars[REPORT_FLAG]


def test_the_package_is_found_by_the_same_discovery_as_the_ledger(isolated: Path) -> None:
    db = open_database()
    registry = FeatureRegistry(services={"db": db, "rules": Rules()})
    assert "headwaters.features.usage_report" in discover_features(registry)
    spec = registry.get(REPORT_FLAG)
    assert spec is not None
    assert spec.star
    assert spec.default_enabled is False


# ---------------------------------------------------------------------------- the survey prompt


def test_the_prompt_is_owed_at_seven_then_thirty_active_days(isolated: Path) -> None:
    db = open_database()
    for day in range(6):
        add(db, ts=NOW + day * DAY)
    assert survey.prompt(db) is None  # 6 active days: not yet

    add(db, ts=NOW + 6 * DAY)
    owed = survey.prompt(db)
    assert owed is not None
    assert owed["stage"] == 7
    assert owed["remaining"] == 2
    survey.record_shown(db, 7)
    assert survey.prompt(db) is None  # asked at this stage; the next one is 30 days

    for day in range(7, 30):
        add(db, ts=NOW + day * DAY)
    later = survey.prompt(db)
    assert later is not None
    assert later["stage"] == 30
    assert later["remaining"] == 1
    survey.record_shown(db, 30)
    assert survey.prompt(db) is None
    assert survey.MAX_PROMPTS == 2


def test_a_dismissal_is_permanent(isolated: Path) -> None:
    db = open_database()
    for day in range(40):
        add(db, ts=NOW + day * DAY)
    survey.dismiss(db)
    assert survey.prompt(db) is None
    survey.reset(db)
    assert survey.prompt(db) is not None  # reset is explicit, and only the CLI offers it


def test_days_without_a_request_do_not_count_as_active(isolated: Path) -> None:
    """The daemon runs every day; only a day that served a request is an active day."""
    db = open_database()
    add(db, ts=NOW)
    add(db, ts=NOW + 60)  # same UTC day
    assert survey.active_days(db) == 1


# ------------------------------------------------------------------------------ product feedback


def test_feedback_writes_a_note_and_never_sends(isolated: Path) -> None:
    _, written = invoke_json("feedback", "the router picked a slower model than I expected")
    assert written["ok"] is True
    assert written["sent"] is False
    assert written["product_feedback"] is True
    assert written["rates_an_answer"] is False
    note = Path(written["note"])
    assert note.is_file()
    assert "slower model" in note.read_text(encoding="utf-8")


def test_feedback_asks_for_a_bundle_only_when_asked(isolated: Path) -> None:
    _, plain = invoke_json("feedback", "no bundle please")
    assert plain["bundle"] is None
    _, bundled = invoke_json("feedback", "with diagnostics", "--bundle")
    assert bundled["bundle"] is not None
    assert Path(bundled["bundle"]).is_file()


def test_feedback_refuses_to_post_floating_free_text(isolated: Path) -> None:
    """``--send`` fails: a feedback body is free text and must never ride the report payload."""
    result = invoke("feedback", "please send this", "--send")
    assert result.exit_code == 2
    assert "no feedback collector" in result.stderr


# ------------------------------------------------------------------------ the ★ retention record


def test_retention_is_null_until_a_feature_has_been_on_for_two_weeks(isolated: Path) -> None:
    """Nothing recorded ★ history before this ticket, so a fresh stamp answers null, not false."""
    db = open_database()
    registry = registry_for(db)
    build = {"db": db, "registry": registry, "hardware": lambda: None}

    first = payload_mod.build(build, now=NOW)
    assert first["features_retained_14d"]["ledger"] is None  # stamped today, too young to know
    assert first["features_retained_14d"][REPORT_FLAG] is False  # off is a real answer

    later = payload_mod.build(build, now=NOW + 15 * DAY)
    assert later["features_retained_14d"]["ledger"] is True
    assert later["features_retained_14d"][REPORT_FLAG] is False


def test_the_counts_come_from_the_decisions_and_outcomes_tables(isolated: Path) -> None:
    """Every number is a row that exists; nothing is estimated except the named failover proxy."""
    db = open_database()
    first = add(db, ts=NOW, surface="gateway", scope="local")
    add(db, ts=NOW + 60, surface="cli", scope="auto")
    save_outcome(db, OutcomeRow(decision_id=first, status="verify_failed", escalated_to="qwen:32b"))
    registry = registry_for(db)
    built = payload_mod.build({"db": db, "registry": registry, "hardware": lambda: None}, now=NOW)

    assert built["requests"] == 2
    assert built["active_days"] == 1
    assert built["engine_counts"]["gateway"] == 1
    assert built["engine_counts"]["cli"] == 1
    assert built["scope_counts"]["local"] == 1
    assert built["verify_catches"] == 1
    assert built["escalations"] == 1
    assert built["failover_requests_approx"] == 0  # no ``alternatives`` were recorded
    assert built["savings_bucket"] in payload_mod.SAVINGS_BUCKETS
    payload_mod.check_payload(built)


def test_the_savings_bucket_never_turns_unknown_into_no_savings() -> None:
    """The ledger's own rule (``_rateable``): an unpriced request is not a free one."""
    assert payload_mod.savings_bucket(None) == "unknown"
    assert payload_mod.savings_bucket({"available": False}) == "unknown"
    assert payload_mod.savings_bucket({"available": True, "week": {}}) == "unknown"
    zero = {"available": True, "week": {"estimated_savings_usd": 0}}
    assert payload_mod.savings_bucket(zero) == "no_savings"
    small = {"available": True, "week": {"estimated_savings_usd": 0.5}}
    assert payload_mod.savings_bucket(small) == "0-1"


def test_the_gpu_class_is_a_size_and_a_vendor_never_a_device_name() -> None:
    class _Gpu:
        total_mb = 12288

    class _State:
        gpu = _Gpu()

    _Gpu.name = "NVIDIA GeForce RTX 3060 Ti LHR"  # type: ignore[attr-defined]
    assert payload_mod.gpu_class(_State()) == "12 GB NVIDIA"  # type: ignore[arg-type]
    assert payload_mod.gpu_class(None) is None
