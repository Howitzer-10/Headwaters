# Orchestration: how work is split, handed over and accepted

This page is for the session that directs the project. It does not repeat the product plan or the
report contract; it says who does what, what a handover contains, and what must be true before work
is accepted. Written after HDW-146, from failures that run actually hit.

## Roles

| Role | Does | Does not |
| --- | --- | --- |
| **Owner** | Sets direction, answers `decision` follow-ups, runs the release gate and the owner evidence steps | Work tickets |
| **Director** (one long-running session) | Picks the next ticket, writes the brief, spawns the ticket session, verifies the result, keeps the queue, triages follow-ups | Write product code, edit a ticket's report |
| **Ticket session** (fresh session per ticket) | One ticket, start to finish, including the whole finish sequence and its report | Touch another ticket's report, `docs/FOLLOWUPS.md`, the backlog or a packet |
| **Triage session** | Reads reports, turns follow-ups into backlog tickets, marks reports triaged | Implement tickets |

A ticket session's context is separate from the director's. That is the point: it starts clean and
only sees what the brief and the packet give it. It also means the director must never trust a
session's summary in place of the repository state.

## Sources of truth, in order

1. `docs/DECISIONS.md`: D7 and later are binding and override any packet text.
2. `docs/PLAN.md`: which stage is active and which phase comes next. It is the sequencing authority,
   not raw backlog order.
3. `headwaters_spec/headwaters_ticket_backlog.md`: the ticket rows, their Effort and Depends.
4. `docs/SDK_FACTS.md`: verified SDK and tooling facts. Read before probing anything.
5. `docs/ASSUMPTIONS.md`: what each ticket assumed, per row.
6. `docs/reports/HDW-nnn.md`: what each finished ticket actually did.

## The cycle

1. **Pick.** Recompute the queue from the repository every cycle; never quote a remembered one (see
   Failure modes). A ticket is ready when its packet exists, it has no report, and every id in its
   `Depends` is finished. Finished means a report exists, or the id is from phases 1-3, which
   finished before the report convention began at HDW-120.
2. **Brief.** One short message, below.
3. **Delegate.** One ticket, one fresh session. Do not split a ticket into several sessions: each
   one would rerun the full suite (~5 minutes) and its own report.
4. **Verify.** Check evidence, not prose. The checklist below.
5. **Accept or correct.** Accept silently, or send one focused correction. Do not re-run the ticket
   yourself in the director session.
6. **Record.** Add any new fact to `SDK_FACTS.md`. Leave the report to triage.

## The brief

Keep it to these lines. Everything else belongs in the packet.

```
Ticket: HDW-nnn (<title>)            Effort: low|medium|high
Packet: C:\Projects\Headwaters\headwaters_ticket_packets\HDW-nnn.md
Read first: docs/DECISIONS.md (D7+), docs/SDK_FACTS.md, docs/PLAN.md
Then: the prompt's BEFORE CODING line, implementation, the full finish sequence.
Do not: edit docs/FOLLOWUPS.md, the backlog, a packet, or another ticket's report.
Report: docs/reports/HDW-nnn.md, status set from the real run.
Return: the step-10 block only (Summary, Report, Commit, Checks, Follow-ups, NEXT TICKET, Effort).
Note: <anything the director knows that the packet does not, or "nothing">
```

## Verification before accepting

Read the repository, not the session's message.

- `git log --oneline -1` says `HDW-nnn: ...` and `git status --short` is empty.
- `docs/reports/HDW-nnn.md` exists, has the six headings from `docs/reports/README.md`, and `Status`
  is `done` rather than `partial` unless the packet allows it.
- The report's `Checks` line states counts, not "passed". A report claiming 0 failures while the
  session also claims a rerun needs its log reasoning read.
- `docs/ASSUMPTIONS.md` gained exactly one row for the ticket, and it names what was verified how.
- No file outside the ticket's scope changed: `git show --stat HEAD` should not touch
  `docs/FOLLOWUPS.md`, the backlog or another report.
- Follow-ups are `FU-nnn-n` lines in the report only, each with a kind.
- If the ticket changed a count that existing tests assert (tools listed, settings, rows), those
  tests were updated and the report says so under Deviations.

## Parallelism

- Two sessions must never work in the same working tree: they race on `git add -A`, on
  `pytest.log`/`mypy.log`, and on the report files.
- Safe parallelism is one `git worktree` per session, merged back in sequence, with the full suite
  run again on the merged tree before the branch is accepted.
- Otherwise, tickets are serial. Say which mode you are in before delegating more than one ticket.
- Only batch tickets with different files and no dependency between them. Two `features/*` tickets
  are a bad batch; a `features/*` ticket and a `vscode-extension/` ticket are a good one.

## Known limits

- The director cannot set a sub-session's reasoning level and cannot see its token use. Effort in
  the brief is advice, not enforcement.
- A gate ("switch reasoning, then reply continue") costs a human round-trip. It is worth it only
  when a person is watching a High-effort ticket.
- Sub-session quality depends on the packet. A thin packet produces a thin report; fix the packet
  rather than the session.
- The director's own memory of the repository goes stale as soon as another session commits. Re-read
  before every pick.

## Failure modes seen so far

Each of these cost time or nearly shipped a wrong claim. They are the reason for the rules above.

| What happened | Why | Rule that prevents it |
| --- | --- | --- |
| The HDW-146 prompt named `docs/reports/HDW-137.md` while the ticket was HDW-146, and `HDW-137.md` already belonged to another ticket | A hard-coded id in a template | Use the ticket's own id everywhere; state it once at the top of the prompt |
| The director told the owner "NEXT TICKET: HDW-147", then found `HDW-147.md` on disk three turns later | The queue was computed once and then quoted from memory, while other sessions committed | Recompute the queue from the repository at every pick |
| A queue script reported `remaining: 0` because it parsed headings that read `SPW-501`, while the packets and reports say `HDW-501` | Trusting one parse without a sanity check against file counts | Cross-check a computed answer against a simple count before reporting it |
| Adding one advanced tool broke two tests that assert the tool count (16, now 17) | Those tests encode the count as a literal | Grep for counts when adding or removing an exposed item, and note the change under Deviations |
| `Set-Content -Encoding utf8` rewrote a test file with a BOM | PowerShell's default | Use the editor tool or `[System.IO.File]`; never `Set-Content` for code |
| The editor refused to create a file it was told to overwrite, and an 8 KB edit was rejected for size | Tool limits, not a bug | Create in chunks, then `insert_line`; keep edits under ~6000 characters |
| `uv run pytest` on 16 files timed out at 30 s in the foreground | The 30 s shell limit | Named files only when quick; otherwise `Start-Process` and poll the log |
| A crash's traceback was swallowed by PowerShell's error wrapping | stderr handling | `cmd /c "uv run python x.py > out.txt 2>&1"`, then read `out.txt` |

## Keeping this page honest

- Add a row to Failure modes only for a failure that actually happened, with the ticket id.
- If a rule here contradicts `docs/DECISIONS.md`, the decision wins and this page is corrected.
- Keep it short. Anything about the product goes in `PLAN.md`, `BETA.md` or the checklists.

