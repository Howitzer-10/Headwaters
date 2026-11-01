"""HDW-204: verdict merging, failure mapping and error rendering."""

from __future__ import annotations

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.router_types import RouteFailed, VerifyOutcome
from headwaters.core.scope import ScopeError
from headwaters.mcp.tools.context import ToolFailure
from headwaters.mcp.tools.failures import failure_info
from headwaters.mcp.tools.render import render_error
from headwaters.mcp.tools.schemas import CheckItem, ErrorInfo
from headwaters.mcp.tools.verify import Verification, combine


def test_combine_lets_a_failure_anywhere_win_and_never_invents_a_pass() -> None:
    failed = VerifyOutcome(True, False, ("placeholder: x",), 1, 1)
    clean = Verification("pass", checked=1)
    assert combine(failed, clean) == ("fail", ("placeholder: x",))
    assert combine(None, Verification("fail", ("a",), checked=1)) == ("fail", ("a",))
    assert combine(VerifyOutcome(False, None), Verification("unchecked")) == ("unchecked", ())
    assert combine(None, None) == ("unchecked", ())
    assert combine(VerifyOutcome(True, True, (), 1, 1), None) == ("pass", ())


def test_a_mandatory_gate_that_checked_nothing_is_not_a_pass_by_itself() -> None:
    unchecked = VerifyOutcome(True, None, (), 1, 0, unchecked=True)
    assert combine(unchecked, None) == ("unchecked", ())
    assert combine(unchecked, Verification("pass", checked=1))[0] == "pass"  # this tool did check


def test_diff_applied_needs_the_git_check_to_have_passed() -> None:
    ran = Verification("pass", checks=(CheckItem(name="diff", status="pass"),), checked=1)
    skipped = Verification("unchecked", checks=(CheckItem(name="diff", status="skipped"),))
    assert ran.diff_applied is True
    assert skipped.diff_applied is False
    assert Verification("pass", checked=1).diff_applied is False


def test_known_failures_become_structured_errors() -> None:
    scope = failure_info(ScopeError("private_lock_conflict", "locked", "turn it off"))
    assert scope is not None
    assert (scope.error.code, scope.error.fix) == ("private_lock_conflict", "turn it off")
    tips = (Suggestion("pull_model", "Pull a model."),)
    none = failure_info(NoRouteError("no_route", "nothing", suggestions=tips))
    assert none is not None
    assert none.suggestions[0].code == "pull_model"
    assert none.error.fix == "Pull a model."
    failed = failure_info(RouteFailed("all_failed", "every try failed"))
    assert failed is not None
    assert failed.error.code == "all_failed"
    assert failed.error.fix
    ex = failure_info(ExecutorError("local_scope_violation", "refused"))
    assert ex is not None
    assert ex.error.code == "local_scope_violation"
    tool = failure_info(ToolFailure("no_workspace", "m", "f", suggestions=[("split_task", "t")]))
    assert tool is not None
    assert tool.suggestions[0].text == "t"


def test_an_unexpected_exception_is_not_classified() -> None:
    assert failure_info(RuntimeError("secret detail")) is None


def test_render_error_has_the_code_the_fix_and_each_tip() -> None:
    text = render_error(ErrorInfo(code="c", message="m", fix="f"), ["t1"])
    assert text.splitlines() == ["Headwaters could not do this (c): m", "Fix: f", "Try: t1"]
