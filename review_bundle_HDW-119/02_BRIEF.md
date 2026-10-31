# HDW-119: Phase 1 review gate, brief

Ticket IDs: the backlog calls this **SPW-119**, the ticket packets call it **HDW-119**. `SPW-nnn` and
`HDW-nnn` are the same ticket (the product was renamed; the backlog kept the old prefix).

## The ticket (verbatim from the backlog)

> **Model** Opus, **Effort** High, **Depends** 101 to 118
> - Review core against the design. Policy ladder correctness, local-scope fail-closed behaviour,
>   and the absence of hard-coded slugs or limits are the priorities.
> - Run coverage. Check that the import-linter contract holds.
> - Settle the 3 open decisions in section 2.
> - Produce a punch list of fixes as new tickets.

## What is under review

Phase 0 and Phase 1: tickets 001 to 004 and 101 to 118 (18 of 22 are in `headwaters/core`). There is
no git history, so the "diff" is a **snapshot**: all of Phase 1 is new code. The code is in
`code/core_A_safety_critical.md` and `code/core_B_supporting.md` (the review target; read A first)
and `code/surfaces_and_config.md`.

## Priorities, in order

1. **Local scope is fail-closed.** In local scope no cloud client is constructed, every network
   sub-step is loopback-only, and a failure is a structured error, never a cloud fallback. Look at
   `scope.py`, `executor/guard.py`, `executor/executor.py`, `executor/ollama.py`, `router.py`,
   `analyzer/ollama_client.py`, `catalog/http.py`, `catalog/discovery.py`, `candidates.py`. Test the
   claim from both sides: can any code path reach a non-loopback host or build a cloud client when
   the effective tiers are `{local}`? (Private lock, project rule, secret detection and airplane mode
   all produce that state.)
2. **Policy ladder correctness.** `policy/*.py` against v3 section 4.5 and v3.3 section B4: hard
   filter, floors, scoring terms, rung order, `primary_first`, pin and force, local-scope behaviour,
   `NoRouteError` content. Check the golden tests (`tests/test_policy_golden_*.py`) really pin the
   behaviour rather than restate the implementation.
3. **No hard-coded slugs, limits, prices, error regexes or placeholder patterns** (Context Pack
   non-negotiable). `evidence/mechanical_checks.md` lists leads; confirm or dismiss each one and
   say which constants should move to `catalog/seed_catalog.json` or `rules.yaml`.
4. **Layering.** `headwaters.core` must not import features, gateway, mcp, control or installer.
   `evidence/import_linter.txt` has the result; check the contract in `.importlinter` is complete
   (for example `litellm` is isolated to `executor/cloud.py`).
5. **Privacy of stored and logged data.** Only decision metadata is stored; no prompts, outputs or
   keys. Look at `state.py` (`scrub`), `router_log.py`, `logging.py`, `router_session.py`.
6. Everything else: correctness of walls and quotas, hardware fit math, verification, state layer,
   test quality, the three open decisions below.

## The three open decisions (backlog section 2)

Settle each with a clear decision, a reason, and the follow-up work it causes. Number them D10, D11
and D12 (D9 is the last entry in `docs/DECISIONS.md`). Facts about what the code does today:

1. **Escalation in core?** v3.5 makes verify-then-escalate a star feature. Today core verifies and
   reports; the optimistic rung only exists when an `EscalationPlugin` is registered; without one a
   failed check is returned with `verify_failed` set. The backlog suggests considering one retry on
   the next candidate in core. Relevant: `router.py` (`_verify`, `_escalate`, `_finish`),
   `policy/ladder.py`, `plugins.py`, v3.5 section B.
2. **Sticky sessions in core?** v3.5 makes them a star feature. Today core derives a session key
   (`router_session.py`), pins the model that last answered (`sessions.pinned_model`), and gives it
   the warm bonus on the next turn. Full stickiness would be ticket 607 (star). The concern is
   gateway agent loops switching models between turns.
3. **`headwaters_think` in 0.1.** Planned as single-model reasoning in core, with Fusion and duck
   modes as 1.x star overlays. The MCP tool does not exist yet (Phase 2); decide whether that scope
   is right and what core must expose for it.

The decision must respect v3.5 section B: **moving a star feature into core is always allowed;
moving a core feature to star is not.**

## What to produce

Follow the format in `01_PROMPT.md`. In short: a verdict, a findings table with file and line
references, decisions D10 to D12 ready to paste into `docs/DECISIONS.md`, and a punch list written as
new tickets in the backlog format (use IDs from HDW-120 upward, with Model, Effort, Depends, Class,
Refs, Build, Accept when).
