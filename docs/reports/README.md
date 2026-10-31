# Ticket session reports

Every ticket session writes `docs/reports/HDW-nnn.md` before it finishes and commits it with the
code. Chats are not kept; this folder is the only record a later session can read.

The triage session (prompt in `docs/FOLLOWUPS.md`) reads every report here, turns follow-ups into
backlog tickets or ledger entries, and marks each report as triaged. Ticket sessions never edit
another ticket's report and never edit `docs/FOLLOWUPS.md`.

## Format (copy exactly; keep the headings so the triage session can parse them)

```markdown
# HDW-nnn report

- Date: YYYY-MM-DD
- Model: Sonnet | Opus
- Status: done | partial | blocked
- Commit: <short hash, or "uncommitted">
- Triaged: no

## Summary
Two or three sentences: what changed and why.

## Files changed
- path: one line on what changed

## Tests
- Added: test names
- Suite: N passed, N failed, N skipped; lint-imports, mypy, ruff results

## Accept-when check
- [x] each "Accept when" item from the packet, with the test or file that proves it
- [ ] any item not met, with the reason

## Assumptions
- A-xxx added to docs/ASSUMPTIONS.md: one line each (or "none")

## Deviations
- Where the work differs from the packet, and why (or "none")

## Follow-ups
One line each, numbered FU-nnn-1, FU-nnn-2 ... Kind is one of:
bug | gap | risk | test | doc | decision (needs the owner) | cleanup

- FU-nnn-1 | kind | where (file or ticket) | what and why | suggested owner ticket, or "new"
```

If there are no follow-ups, write `- none`. Do not leave the section out.
