"""HDW-204: pulling diffs and commands out of an answer, and the relay and compaction rules."""

from __future__ import annotations

import pytest

from headwaters.mcp.tools.extract import DIFF_OMITTED, extract, split_segments
from headwaters.mcp.tools.relay import choose_relay, compact, has_code

DIFF = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n"


def test_a_fenced_diff_is_pulled_out_and_marked_in_the_prose() -> None:
    got = extract(f"Changed it.\n```diff\n{DIFF}```\nDone.")
    assert got.diffs == (DIFF,)
    assert DIFF_OMITTED in got.prose
    assert "Changed it." in got.prose
    assert "@@" not in got.prose


def test_a_patch_fence_and_an_aliased_fence_count_as_diffs() -> None:
    assert extract(f"```patch\n{DIFF}```").diffs == (DIFF,)
    assert extract(f"```d\n{DIFF}```", {"d": "diff"}).diffs == (DIFF,)


def test_an_unclosed_diff_fence_is_not_offered_as_something_to_apply() -> None:
    got = extract(f"```diff\n{DIFF}")
    assert got.diffs == ()
    assert "@@" in got.prose  # left in the prose; the verifier reports the truncated fence


def test_a_bare_diff_at_the_end_is_found() -> None:
    got = extract(f"Here you go:\n{DIFF}")
    assert got.diffs == (DIFF,)
    assert got.prose.startswith("Here you go:")


def test_shell_blocks_become_commands_and_other_code_stays_in_the_prose() -> None:
    got = extract("```sh\nls\n```\n```python\nx = 1\n```\n```powershell\ndir\n```")
    assert got.commands == ("ls", "dir")
    assert "x = 1" in got.prose
    assert got.diffs == ()


def test_crlf_answers_are_normalized() -> None:
    got = extract("```diff\r\n--- a/x\r\n+++ b/x\r\n@@ -1 +1 @@\r\n-a\r\n+b\r\n```")
    assert "\r" not in got.diffs[0]


def test_inline_triple_backticks_do_not_open_a_fence() -> None:
    assert [s.is_code for s in split_segments("use ```x``` here")] == [False]


def test_segments_round_trip_their_text() -> None:
    text = "intro\n```python\nx = 1\n```\noutro"
    assert "\n".join(s.render() for s in split_segments(text)) == text


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"status": "pass", "diffs": True, "diff_applied": True}, "apply_diff"),
        ({"status": "pass", "diffs": True, "diff_applied": False}, "verbatim"),
        ({"status": "unchecked", "diffs": True, "diff_applied": True}, "verbatim"),
        ({"status": "fail", "diffs": True, "diff_applied": True}, "verbatim"),
        ({"status": "pass", "diffs": False, "code": True}, "verbatim"),
        ({"status": "pass", "diffs": False}, "summarize_ok"),
        ({"status": "unchecked", "diffs": False}, "summarize_ok"),
        ({"status": "fail", "diffs": False}, "verbatim"),
        ({"status": "pass", "diffs": False, "below_floor": True}, "verbatim"),
        ({"status": "pass", "diffs": True, "diff_applied": True, "below_floor": True}, "verbatim"),
    ],
)
def test_the_relay_rule(kw: dict[str, object], expected: str) -> None:
    base: dict[str, object] = {"code": False, "below_floor": False, "diff_applied": False}
    assert choose_relay(**{**base, **kw}) == expected  # type: ignore[arg-type]


def test_has_code_needs_a_closed_fence() -> None:
    assert has_code("```py\nx\n```")
    assert not has_code("```py\nx")
    assert not has_code("plain")


def test_compact_cuts_at_a_line_and_points_to_the_full_text() -> None:
    text = "\n".join(f"line {n}" for n in range(100))
    short, cut = compact(text, 60, "headwaters://result/abc")
    assert cut is True
    assert short.endswith("headwaters://result/abc]")
    assert "line 99" not in short
    assert compact("tiny", 60, "u") == ("tiny", False)
