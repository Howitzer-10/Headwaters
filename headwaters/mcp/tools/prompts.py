"""System prompts of the front-door tools (HDW-204).

Short on purpose: the analyzer counts system-prompt tokens toward its "agentic" test, and a small
local model follows a short instruction better. Wording only; nothing here is a pattern or a limit.
"""

from __future__ import annotations

from typing import Final

__all__ = ["ASK", "DO_ANSWER", "DO_DIFF", "DO_PLAN", "FILES_HEADER", "system_for"]

_COMMON = (
    "Write complete code with no placeholders and no 'rest of the file' comments. "
    "Put code in fenced blocks with a language tag."
)

DO_ANSWER: Final = f"You are a careful senior engineer. Do the task and answer concisely. {_COMMON}"

DO_DIFF: Final = (
    "You are a careful senior engineer. Reply with the change as ONE unified diff in a ```diff "
    "fenced block, then at most two sentences of explanation. Name files exactly as shown after "
    "'###' below, with a/ and b/ prefixes, and give enough context lines for `git apply`. Change "
    f"only what the task needs; never rewrite a whole file. {_COMMON}"
)

DO_PLAN: Final = (
    "You are a careful senior engineer. Reply with a short numbered plan. Each step names the file "
    "and what changes in it. Do not write a diff or full code; short snippets only."
)

ASK: Final = (
    "You answer questions about the files below. You are read-only: explain, summarize or review, "
    "and do not propose edits unless asked. Be concise and cite file names and line numbers."
)

FILES_HEADER: Final = "Files (each starts with ### and its path):"


def system_for(tool: str, want: str) -> str:
    if tool == "ask":
        return ASK
    return {"diff": DO_DIFF, "plan": DO_PLAN}.get(want, DO_ANSWER)
