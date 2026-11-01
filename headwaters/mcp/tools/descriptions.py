"""Tool descriptions (HDW-204, v3.4 B2).

Short, imperative and example-led, so a weak or free dispatcher model picks the right tool and
fills the arguments correctly. Each says when to use it, when not to, and shows one call. They are
the only text a host model reads before calling, so keep them under ~120 words.
"""

from __future__ import annotations

from typing import Final

__all__ = ["ADVANCED", "ASK", "CHECK", "DO", "HANDOFF", "MORE", "SCOPE", "STATUS", "THINK"]

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

THINK: Final = (
    "Ask a hard question that needs careful reasoning: a design choice, a tricky bug, a proof. "
    "Read-only. Runs on the best reasoning model Headwaters may use here (local or free when "
    "scope says so) and returns the final answer, never the reasoning. Name files in `paths`.\n"
    'Example: {"question": "why can this lock deadlock?", "paths": ["src/pool.py"]}\n'
    "Not for plain edits: use headwaters_do. Follow `relay`."
)

STATUS: Final = (
    "Show where Headwaters stands: the scope and locks in force, walls with their reset times, "
    "the GPU, the warm local model, the pinned model and savings. Read-only; no model call.\n"
    "Example: {}\n"
    "Use it when a call was refused, to see why."
)

SCOPE: Final = (
    "Set which models may serve requests: `scope` (auto, free, local, cloud), `private_lock` "
    "(true = local models only) and `strategy` (cost_saver, quality_first, primary_first). "
    "Tightening applies at once. Loosening needs the USER: the host asks them, or the result "
    "gives a command for them to run. Never run that command yourself.\n"
    'Example: {"scope": "local"}'
)

HANDOFF: Final = (
    "Save a short packet so another model or host can carry on: goal, files (path and hash, no "
    "contents), the last error and recent turns. Use it before switching models.\n"
    'Example: {"goal": "fix the flaky test", "paths": ["tests/test_io.py"], '
    '"last_error": "AssertionError ..."}\n'
    "Secret-shaped text is removed. Returns a packet_id and the packet."
)

MORE: Final = (
    "List Headwaters' advanced tools and switch them on for this session. Call it when you "
    "need to pick a model, check quotas or local models, pin a model, give feedback or poll a "
    "job. The tool list then grows (list_changed).\n"
    "Example: {}"
)

ADVANCED: Final = {
    "headwaters_recommend_model": (
        "Which model would Headwaters use for this task, and the exact command that switches the "
        "host to it (OpenCode free models included). Dry run.\n"
        'Example: {"task": "refactor the parser"}'
    ),
    "headwaters_should_switch_host": (
        "Say whether the host's current model should change, and why. Dry run.\n"
        'Example: {"current_model": "opencode/some-free-model"}'
    ),
    "headwaters_list_local_models": (
        "Local models with fit, context and speed on this GPU.\nExample: {}"
    ),
    "headwaters_quota_status": (
        "Walls with reset times and each cloud model's request counters.\nExample: {}"
    ),
    "headwaters_job_status": ('Progress of a background job.\nExample: {"job_id": "abc"}'),
    "headwaters_job_result": (
        'The result of a finished background job.\nExample: {"job_id": "abc"}'
    ),
    "headwaters_pin_model": (
        "Pin one model for later calls, or clear the pin (omit `model`). The scope and locks "
        'still apply.\nExample: {"model": "qwen3:14b"}'
    ),
    "headwaters_feedback": (
        "Thumbs up or down on an earlier answer, using its decision_id.\n"
        'Example: {"decision_id": 12, "rating": "down"}'
    ),
}
