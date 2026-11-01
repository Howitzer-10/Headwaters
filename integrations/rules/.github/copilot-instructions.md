# Headwaters

This project uses Headwaters, a local router that sends coding work to cheap, free or local
models, so you spend fewer paid tokens and keep working when a limit runs out.

## Rules

- For implementation work (writing, fixing, refactoring, reviewing code), call `headwaters_do`.
  Do not do that work yourself.
- Name files in `paths`. Never paste file contents into a Headwaters call.
- Ask `headwaters_do` for `want: "diff"` when you want a change you can apply.
- Honor `relay` in every result:
  - `apply_diff`: apply the returned diffs exactly as given.
  - `verbatim`: show the answer unchanged.
  - `summarize_ok`: you may summarize it.
- Apply diffs exactly. Do not reword, reorder or "improve" them. If one fails to apply, run
  `headwaters_check` on it or call `headwaters_do` again with the error.
- Prefer delegating cheap subtasks (boilerplate, tests, renames, small fixes, explanations) to
  `headwaters_do` or `headwaters_ask`. Keep planning and the final answer to the user yourself.
- For a hard design question or a tricky bug, call `headwaters_think`.
- If a call is refused, call `headwaters_status` and tell the user why.
- Only the user loosens a scope or the Private lock. If a result gives a command for the user to
  run, show it to them and do not run it yourself.
