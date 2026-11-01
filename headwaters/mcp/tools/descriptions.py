"""Tool descriptions (HDW-204, v3.4 B2).

Short, imperative and example-led, so a weak or free dispatcher model picks the right tool and
fills the arguments correctly. Each says when to use it, when not to, and shows one call. They are
the only text a host model reads before calling, so keep them under ~120 words.
"""

from __future__ import annotations

from typing import Final

__all__ = ["ASK", "CHECK", "DO"]

DO: Final = (
    "Do a coding task on Headwaters' cheap or local models. Use it for writing, fixing, "
    "refactoring or reviewing code, so you spend fewer paid tokens. "
    "Name files in `paths`; do NOT paste file contents. "
    "`want`: 'diff' returns a checked unified diff, 'plan' a short plan, 'answer' (default) text.\n"
    'Example: {"task": "add a --verbose flag", "paths": ["src/cli.py"], "want": "diff"}\n'
    "Then follow `relay`: apply_diff = apply `diffs` as given; verbatim = show unchanged; "
    "summarize_ok = you may summarize. Not for questions: use headwaters_ask."
)

ASK: Final = (
    "Ask a question about code or files without changing anything (read-only). Use it to "
    "explain, summarize or review. Name files in `paths`; do NOT paste file contents.\n"
    'Example: {"question": "where is the retry logic?", "paths": ["src/http.py"]}\n'
    "Follow `relay`: verbatim = show unchanged; summarize_ok = you may summarize."
)

CHECK: Final = (
    "Check a unified diff or a piece of code with Headwaters' verifiers (read-only, no model "
    "call). Use it before you apply a change. A diff is also test-applied to the project.\n"
    'Example: {"diff": "--- a/x.py\\n+++ b/x.py\\n@@ ..."} or {"code": "def f(): ...", '
    '"language": "python"}\n'
    "`verified` true means every check that ran passed; `unchecked` means nothing could be "
    "checked, which is not a pass."
)
