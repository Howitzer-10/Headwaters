# Follow-up ledger

Maintained **only** by the triage session. Ticket sessions write follow-ups into their own report
(`docs/reports/HDW-nnn.md`) and never edit this file.

Every follow-up from every report ends up in exactly one row here, with one of these outcomes:

- **ticket**: became a new backlog ticket (HDW-142 upward) with a packet
- **merged**: folded into an existing ticket's Build or Accept-when list (named in the row)
- **owner**: needs a decision only the owner can make (listed under "Waiting on the owner")
- **dropped**: not worth doing, with the reason
- **done**: already fixed by a later ticket (named in the row)

## Waiting on the owner

| FU id | Question | Options | Raised |
| --- | --- | --- | --- |

## Ledger

| FU id | From | Kind | Summary | Outcome | Where it went | Triaged |
| --- | --- | --- | --- | --- | --- | --- |
| FU-120-1 | HDW-120 | gap | Read the decision and outcome rows back from SQLite and assert no secret text; closes the open fourth accept-when item. | merged | HDW-306 Build and Accept when (backlog and packet); the privacy egress suite is the right home. | 2026-10-31 |
| FU-120-2 | HDW-120 | gap | A secret split across two JSON fields is not joined, and base64 or percent-encoded secrets are not detected. Real: `SecretDetector.scan_all` scans each text alone and never decodes. | ticket | HDW-142 (Before HDW-306), with packet. | 2026-10-31 |
| FU-120-3 | HDW-120 | risk | The socket spy patches Python-level calls only; a C extension opening its own sockets is not caught. | merged | HDW-306: add a `sys.addaudithook` check and record the remaining limit in ASSUMPTIONS. | 2026-10-31 |
| FU-120-4 | HDW-120 | cleanup | `router_session.text_of` reads only `content` and can be mistaken for the scan path. Real: still used by `router_prep` and session keys. | merged | HDW-134 (it edits `router_session`): rename or document. | 2026-10-31 |
| FU-120-5 | HDW-120 | cleanup | Untracked `.tmp*`, `model_provisioner_previous.py`, `setup_models_previous.py` in the repo root. | dropped | None of these files exist at triage (root listing and `git ls-files`), so there is nothing to delete. | 2026-10-31 |
| FU-120-6 | HDW-120 | cleanup | Line-ending rule: Git warns LF to CRLF on every touch. Real: no `.gitattributes`, `core.autocrlf=true`, files mix LF and CRLF. | merged | HDW-141: add `.gitattributes` (`* text=auto eol=lf`) and renormalize in its own commit. | 2026-10-31 |
| FU-120-7 | HDW-120 | doc | Full coverage run took about 100 s, not the ~90 s the packets say. | dropped | No ~90 s estimate exists in the packets, backlog, README or CI (searched). Measured on 2026-10-31: 955 passed in 116 s without coverage. | 2026-10-31 |
| FU-122-1 | HDW-122 | gap | No production code supplies `key_alias_for`, so key-scoped walls still use the provider subject. Real: `cli/runtime.py` builds `Executor` with neither callback. | merged | HDW-401 (key management) Build and Accept when. | 2026-10-31 |
| FU-122-2 | HDW-122 | gap | `poll_key` has no caller. Real: only its definition in `quotas.py`. | merged | HDW-131 (already named in the report): daemon timer, with a test that nothing is polled under airplane or a Private lock. | 2026-10-31 |
| FU-122-3 | HDW-122 | risk | `openrouter_auto` is treated as paid and local as free; revisit if the auto router can pick free models. | dropped | OpenRouter's Auto Router page (read 2026-10-31) says you pay the standard rate of the model it picks and does not say it picks `:free` ones, so the cautious reading holds. Reopen if a free pick through auto is ever seen. | 2026-10-31 |
| FU-122-4 | HDW-122 | cleanup | `headwaters status` shows raw `...:paid`/`...:free` subjects; its "after a balance check" text is now rarely reached. | merged | HDW-404 (status page) Build, which also covers `headwaters status`. | 2026-10-31 |
| FU-122-5 | HDW-122 | risk | Seed wall patterns are a first draft, unchecked against live provider responses, and the class split depends on them. | merged | HDW-139 (seed catalog data): verify every `wall_patterns` row against current docs; effort raised from Low to Medium. | 2026-10-31 |
| FU-122-6 | HDW-122 | test | Tests ran on Windows only; the ticket needs ubuntu-latest and nothing was pushed. Real: `ci.yml` triggers on `main`, the repo branch is `master`. | ticket | HDW-143 (Before HDW-201), with packet. | 2026-10-31 |
| FU-123-1 | HDW-123 | gap | Verified retry onto a stronger local model after a failed mandatory check is not built; the HDW-129/130 packets might not cover this case. Checked: HDW-129 covers the retry generally but did not name the below-floor local case. | merged | HDW-129 Build and Accept when: the below-floor local case is tested both ways. | 2026-10-31 |
| FU-123-2 | HDW-123 | gap | Only the CLI `pick` shows the below-floor warning; gateway and MCP do not. Real: neither package has code yet and no ticket mentioned it. | merged | HDW-204 (MCP result carries `suggestions` and a warning line) and HDW-302 (response header). | 2026-10-31 |
| FU-123-3 | HDW-123 | cleanup | Text in `tests/policy_golden_support.py` and `docs/HDW-119_REVIEW.md` may still describe the old `local_below_floor` error. | dropped | Searched the code and tests: `local_below_floor` appears only in two test names, and `policy_golden_support.py` has no such text. The review doc is a historical record. | 2026-10-31 |
| FU-124-1 | HDW-124 | gap | The `mcp`, `fastapi` and `starlette` contracts pass vacuously until Phase 2 code lands. Real: none of the three is in `pyproject.toml`. | merged | HDW-201 (first web code) and HDW-202 (adds `mcp`): show each contract bites. | 2026-10-31 |
| FU-124-2 | HDW-124 | test | The litellm telemetry check covers import time only, not call time. | merged | HDW-127 (litellm stdout and logging): mocked-HTTP check that a call contacts only the provider host. | 2026-10-31 |
| FU-124-3 | HDW-124 | cleanup | Same untracked `.tmp*` and `*_previous.py` files as FU-120-5. | dropped | Same reason as FU-120-5: the files are gone. | 2026-10-31 |
| FU-TRI-1 | triage | bug | CI never runs: `ci.yml` triggers on pushes to `main`, but the only branch is `master`; nothing has run on ubuntu. | ticket | HDW-143 (shared with FU-122-6). | 2026-10-31 |
| FU-TRI-2 | HDW-123 report | bug | HDW-123's report said `Commit: uncommitted`; its changes were still modified in the working tree. | done | Owner answered (a) on 2026-10-31: committed as 8d0728e (code and tests only) and pushed with the other 9 commits (`573b612..8d0728e`). Report Commit field updated. | 2026-10-31 |
| FU-TRI-3 | HDW-124 | gap | The contract lets only `headwaters.gateway` import fastapi/starlette, but the repo layout puts the daemon's internal HTTP API in `headwaters/mcp/`. A FastAPI daemon there would fail `lint-imports`. | merged | HDW-201: settle which package owns the HTTP server before coding, and change the contract in that ticket if needed. | 2026-10-31 |
| FU-TRI-4 | HDW-125, HDW-126 | gap | Both tickets were committed without a report, so their follow-ups and accept-when checks were never read. | done | Owner answered (a) on 2026-10-31: `docs/reports/HDW-125.md` and `HDW-126.md` written after the fact (both `Triaged: no`); the next triage session reads them. | 2026-10-31 |

## Triage log

| Date | Reports read | New tickets | Merged | Owner questions | Dropped |
| --- | --- | --- | --- | --- | --- |
| 2026-10-31 | HDW-120, HDW-122, HDW-123, HDW-124 | 2 (HDW-142, HDW-143) | 13 | 2 (FU-TRI-2, FU-TRI-4) | 5 |

---

## Triage prompt (Opus, effort High; run after every 3 to 5 ticket sessions and at every phase gate)

```
You are the triage session for HEADWATERS. Read docs/FOLLOWUPS.md (this file),
docs/reports/README.md, docs/DECISIONS.md, and every docs/reports/HDW-*.md whose header says
"Triaged: no". Also read headwaters_spec/headwaters_ticket_backlog.md to know which tickets exist.

For each follow-up (FU-...) in those reports:
1. Check the live code before trusting it: is it real, already fixed by a later commit, or a
   duplicate of an existing ticket or ledger row?
2. Give it exactly one outcome: ticket | merged | owner | dropped | done (definitions at the top
   of docs/FOLLOWUPS.md). Prefer merged over new tickets when an unstarted ticket fits.
3. Record it as a ledger row. Owner questions also go under "Waiting on the owner", with options
   and your recommendation.

Also check each report's "Accept-when check" and "Deviations". An unmet accept-when item or a
deviation that conflicts with docs/DECISIONS.md becomes a follow-up of kind bug, even if the
session did not list it.

Then:
- New tickets: add them to headwaters_spec/headwaters_ticket_backlog.md in the backlog format
  (### HDW-nnn · Title, Model/Effort/Depends/Class/Refs, Build, Accept when, plus Blocks/Before/
  Can wait), numbered from the next free HDW number. Create headwaters_ticket_packets/HDW-nnn.md
  in the same format as the existing packets and add it to headwaters_ticket_packets/00_INDEX.md.
- Merged items: append them to that ticket's Build/Accept-when in the backlog AND its packet.
- Set "Triaged: yes (YYYY-MM-DD)" in every report you processed. Change nothing else in reports.
- Add one row to the Triage log.

Make no code changes. Finish with a short summary: what is new, what now blocks the next ticket
in order, and the owner questions with your recommended answer.
```
