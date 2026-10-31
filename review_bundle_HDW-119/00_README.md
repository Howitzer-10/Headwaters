# HDW-119 review bundle

Everything needed to run the Phase 1 review gate with Opus. Built from the working tree on
2026-10-30 (no git history, so this is a snapshot). Start with `01_PROMPT.md`.

## Contents, in reading order

| Path | What it is | ~Tokens |
| --- | --- | ---: |
| `01_PROMPT.md` | **The prompt.** Opus reads it from disk, then reads the rest in the order it gives | 2k |
| `02_BRIEF.md` | The ticket, priorities, the three open decisions and the code facts behind them | 1k |
| `03_KNOWN_GAPS.md` | What is missing, odd or untested. Read before judging anything | 1k |
| `decisions/DECISIONS.md` | D1-D9. **D7 and later override the design and the backlog** | 2k |
| `decisions/ASSUMPTIONS.md` | Every assumption and deviation from Phase 0 and 1 (large) | 13k |
| `decisions/PROVENANCE.md` | Where ideas came from (clean-room record) | 0k |
| `design/headwaters_ultimate_design.md` | v3 design | 10k |
| `design/headwaters_v3.1` to `v3.5` addenda | Later documents win; v3.5 is newest | 14k |
| `design/headwaters_ticket_backlog.md` | All tickets (prefix `SPW-`), the three open decisions in section 2 | 17k |
| `tickets/HDW-119.md`, `tickets/00_INDEX.md` | The packet for this ticket, and the packet index | 4k |
| `evidence/` | Test, coverage, lint, type and import-contract output, plus mechanical checks | 14k |
| `code/core_A_safety_critical.md` | Core: scope, policy, executor, router, network edges (40 files) | 54k |
| `code/core_B_supporting.md` | Core: walls, quotas, hardware, analyzer, catalog, verification (33 files) | 47k |
| `code/tests_priority.md` | Tests for the priority areas, plus the fakes they use | 44k |
| `code/surfaces_and_config.md` | CLI, example feature, `pyproject.toml`, `.importlinter`, CI, seed catalog | 17k |
| `code/tests_other.md` | The remaining tests. Optional | 51k |

Total with everything: about 290k tokens. The reading order in `01_PROMPT.md` (everything except
the optional step 15) is about 230k.

## How to run it

In a fresh Opus session that can read local files (effort: High), say:

    Read C:\Projects\Headwaters\review_bundle_HDW-119\01_PROMPT.md and follow it.

Nothing is pasted or attached. Opus reads the files itself and writes its report to
`HDW-119_REVIEW.md` in this folder. The code files are large, so it has to page through them; the
prompt tells it to do so and to say which files it did not finish.

## If the context window is too small

Reading everything is about 230k tokens. If the session runs out, the prompt tells Opus to stop and
list what it did not read. Then start a second session with the same instruction, plus:
`Continue from HDW-119_REVIEW.md; review only the files it lists as unread.` Never skip
`core_A_safety_critical.md`, `DECISIONS.md`, `03_KNOWN_GAPS.md`, `evidence/` or the v3.3 and v3.5
addenda. The files you can skip first are `code/tests_other.md`, `tickets/` and
`decisions/ASSUMPTIONS.md`.

A cleaner split if you expect to run short: session 1 does fail-closed and the policy ladder
(`core_A` + `tests_priority` + v3.3); session 2 does the rest and the three decisions, starting from
session 1's report.

## Snapshot facts

| Item | Value |
| --- | --- |
| Tests | 882 passed, 1 skipped (Windows), 0 failed, 88.8 s |
| Core coverage | 97.32 % (gate 85 %); lowest file `hardware/gpu.py` 86 % |
| Import contract | 1 kept, 0 broken (93 files, 286 dependencies analysed) |
| ruff, ruff format, mypy (strict on core) | all clean |
| Schema checks (`rules.schema.json`, `catalog.schema.json`) | up to date |
| Platform | Windows 11, Python 3.13, uv 0.13.5. Linux not run |

## After the review

1. Paste decisions D10-D12 into `docs/DECISIONS.md`.
2. Add the punch-list tickets to the backlog (and to `headwaters_ticket_packets/` if you regenerate them).
3. Run blocker tickets before starting Phase 2 (HDW-201).
4. The `review_bundle_HDW-119/` folder can be deleted afterwards; it is a copy. The bundle is a
   snapshot and goes stale as soon as the code changes.
