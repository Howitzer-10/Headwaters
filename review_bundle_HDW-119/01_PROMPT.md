# HDW-119: Phase 1 review gate (instructions for the reviewer)

**How to run:** open a fresh Opus session (effort: High) with file access and say only:
`Read C:\Projects\Headwaters\review_bundle_HDW-119\01_PROMPT.md and follow it.`
Nothing needs to be pasted or attached. Everything is read from disk.

## Where things are

Bundle root (all paths below are relative to it): `C:\Projects\Headwaters\review_bundle_HDW-119\`

The bundle is a **snapshot** of the repo at `C:\Projects\Headwaters`. Review the bundle copies, not
the live tree, so line references stay stable. You may look at the live tree to check a claim
(for example to run tests), but cite the bundle.

The code files are large (core A is about 190 KB, core B 165 KB). Your file reader returns a limited
number of lines per call, so **page through each file with start and end lines until you reach the
end**. Do not review a file you only read the start of. If you run short of context, stop and say
exactly which files are unread (see "Output format", item 2) instead of guessing.

Write your report to `HDW-119_REVIEW.md` in the bundle root, as you go (create it early, then extend
it), so nothing is lost if the session ends. End your chat reply with a short summary and the path.

## Reading order

| Step | File | Why |
| --- | --- | --- |
| 1 | `03_KNOWN_GAPS.md` | What is missing or odd. Do not report these as discoveries |
| 2 | `decisions/DECISIONS.md` | **D7 and later override the design and the backlog** |
| 3 | `02_BRIEF.md` | The ticket, priorities and the three open decisions |
| 4 | `design/headwaters_v3.3_addendum_scopes_and_competition.md` | Scopes and the local-scope guarantee |
| 5 | `design/headwaters_v3.5_addendum_free_beta_planned_split.md` | Core versus star features |
| 6 | `design/headwaters_ultimate_design.md` | v3 design, sections 4 and 7 first |
| 7 | `design/headwaters_v3.1`, `v3.2`, `v3.4` addenda | Remaining context |
| 8 | `design/headwaters_ticket_backlog.md` | Tickets; section 2 has the open decisions |
| 9 | `evidence/` (all files) | Test, coverage, lint, import-contract output, mechanical checks |
| 10 | `code/core_A_safety_critical.md` | Review target, read in full |
| 11 | `code/tests_priority.md` | Tests for the priority areas |
| 12 | `code/core_B_supporting.md` | Review target, read in full |
| 13 | `code/surfaces_and_config.md` | CLI, config, import contract, seed catalog |
| 14 | `decisions/ASSUMPTIONS.md` | Large; search it for the topic you are checking |
| 15 | `code/tests_other.md`, `tickets/` | Optional. Only to check a specific claim |

---

You are the senior reviewer for **HEADWATERS**, a Windows-first, local-first LLM router (Python
3.11+, package `headwaters`). It routes each request to the best allowed model: the user's paid
keys, then free cloud, then local Ollama on the user's NVIDIA GPU. Routing is scope (auto, free,
local, cloud) × strategy × locks, and **local scope must fail closed**: no cloud client, loopback
only, and a structured error instead of a cloud fallback.

Your job is **HDW-119, the Phase 1 review gate** (the backlog calls it SPW-119; HDW and SPW are the
same numbering). Phase 1 is the core: tickets 001-004 and 101-118. Nothing else exists yet (no MCP
server, no gateway). Your verdict decides whether Phase 2 starts.

## Rules that govern the documents

- **Entries D7 and later in `decisions/DECISIONS.md` are binding** and override the design and the
  backlog. Deferred tickets (HDW-905) are not built.
- **Precedence: ticket > v3.5 > v3.4 > v3.3 > v3.2 > v3.1 > v3.** Ignore "Lite/Pro edition" labels;
  v3.5 replaced editions with core and star features in one build.
- Most relevant design text: v3 sections 4.1-4.8 and 7, v3.3 sections A-D (scopes and the local-scope
  guarantee), v3.5 sections B and F.

## Rules for the review

- **Evidence over assertion.** Every finding cites a file and a function or line range from the
  bundle and says how you know. If you did not read something, say so; do not infer. If you cannot
  run code, say which claims you could not test.
- **Verify, do not trust, the evidence.** Green tests and lint show the code is internally
  consistent, not that it is right. The mechanical checks are regex leads: confirm or dismiss each.
- **Trace paths, not files.** For the fail-closed claim, follow the call chain from `Router.run`
  and `Router.stream` through scope resolution, the policy, the executor and the Ollama client, and
  list every place a request could leave the machine when the effective tiers are `{local}`. Do this
  for each way to reach local-only: Private lock, project rule, secret detection, `scope=local`,
  airplane mode. Also check secondary paths: analyzer layer 3, discovery, the catalog updater, key
  polling, verification steps, and the CLI dry run.
- **Judge tests, not just count them.** Do the policy golden tests assert behaviour the design
  requires, or only restate the implementation? Name tests that would still pass if the ladder were
  wrong, and behaviours with no test.
- **Respect the core/star rule.** Core is free forever. Moving a star feature into core is allowed;
  moving a core feature to star is not (v3.5 section B).
- **Stay in scope.** This is a review, not an implementation. Propose fixes as tickets; short
  snippets to illustrate a defect are fine.
- Separate **defects** (wrong or unsafe now), **gaps** (missing against the design), **risks** (could
  bite later) and **nits**. Do not pad. If something is fine, say so in a line.
- Do not copy or recall any competitor's source code (clean-room policy).


## Priorities, in order

1. Local scope is fail-closed (as above).
2. Policy ladder correctness against v3 section 4.5 and v3.3 section B4: hard filter, floors, score
   terms, rung order, `primary_first`, pin and force, local-scope behaviour, error content.
3. No hard-coded slugs, limits, prices, error regexes or placeholder patterns outside the catalog
   and `rules.yaml`. For each module-level constant flagged in `mechanical_checks.md`, say whether it
   is a protocol or safety constant (acceptable) or a tunable that must move.
4. Import contract: it holds (see `evidence/import_linter.txt`), but is it **complete**? For example,
   is `litellm` confined to `executor/`? Does anything bypass the contract?
5. Privacy of storage and logs: only decision metadata, no prompts, outputs or keys.
6. Everything else: walls and quotas, hardware and KV math, verification, the state layer, session
   handling, the late additions (`Router.plan`, `CandidateBuilder`, `rules_edit`), test quality.

## The three open decisions

Settle each as a numbered decision, **D10, D11, D12**, in the format of `docs/DECISIONS.md`: the
decision, the reason, what it costs, which ticket(s) it creates, and when to revisit. The three
questions and the facts about today's code are in `02_BRIEF.md` (escalation in core, sticky sessions
in core, `headwaters_think` in 0.1). Take a position; "it depends" is not a decision. Check each
against v3.5 section B.

## Output format (use these headings, in this order)

1. **Verdict**: *Pass*, *Pass with required fixes* or *Do not start Phase 2*, with three sentences of
   justification and the list of **blockers** (what must be fixed before Phase 2).
2. **What I read and what I could not check**: files read fully, files skimmed, and claims you could
   not verify and why.
3. **Fail-closed analysis**: a table of every path that can leave the machine: `file:function`,
   reachable in local scope (yes/no), and the guard that stops it (or "none").
4. **Findings**: a table with `ID | Severity (blocker/high/medium/low) | Kind
   (defect/gap/risk/nit) | Where | Finding | Evidence | Suggested fix`, ordered by severity.
5. **Hard-coded values review**: each flagged constant or literal with your classification.
6. **Test-quality review**: weak or missing tests, ordered by risk.
7. **Decisions D10, D11, D12**: ready to paste into `docs/DECISIONS.md`.
8. **Punch list as new tickets**: IDs from **HDW-120** upward, in the backlog format:
   `### HDW-nnn · Title`, then `**Model** … · **Effort** … · **Depends** … · **Class** … · **Refs** …`,
   `**Build:**` bullets and `**Accept when:**`. Mark which tickets block Phase 2 and which can wait.
   Choose Sonnet or Opus per the backlog's guidance. Keep each ticket small enough for one session.
9. **Doc corrections**: wrong, stale or contradictory statements in `ASSUMPTIONS.md`,
   `DECISIONS.md` or the backlog.
10. **Questions for the owner**: anything only the owner can answer.

Begin by listing the bundle files you can see and anything that appears to be missing, then work
through the reading order. Write the report to `HDW-119_REVIEW.md` in the bundle root.
