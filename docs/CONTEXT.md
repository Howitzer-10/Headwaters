# CONTEXT

Every future ticket session used to spend its first 5-8 turns reading
`docs/DECISIONS.md` (394 lines), `docs/ASSUMPTIONS.md` (294 lines) and a long
packet before it could start. This page replaces that reading: two pages read
instead of those documents, pointing at them for depth. It is a pointer, never a
second `DECISIONS.md` and never a restatement of authority.

## 1. What this is and what it is not

Headwaters is one self-contained product that routes a coding agent's request to
the best free cloud model or local Ollama model it can reach.

D25 (owner, 2026-11-07) sets the niche: **the best router and discoverer of FREE
cloud provider models, plus the best use of LOCAL Ollama models on the person's
own GPU.**

- Paid-model routing is out of scope for new work. Existing paid code (budgets,
  `paid_scopes`, the ledger's savings counterfactual) stays in place and is
  de-prioritised; the counterfactual is now a pitch, not a cost centre.
- Every future ticket is judged through this lens.

It is **not** a router for paid models, and **not** a chat app.

## 2. Where we are, today

`master` is at `21cd924` ("HDW-601: session report") and the tree is clean.
This is Stage 1 under D16: the owner runs the build from source, dogfoods it and
functionally tests it. No packaging, no signing, no beta, no publication.

The release gate is **expected to be red** during Stage 1 and must not be
"fixed"; its 11 owner evidence steps are Stage 2 work.

Phase map (`docs/PLAN.md`):

| Phase | Tickets | State |
| --- | --- | --- |
| 5 | 501-509 | landed |
| 6 | 601-608 | HDW-601 landed at `21cd924`; 602-608 open |
| 7 | 701-706 | not started |
| 8 | 801-803 | Stage 2 only, deferred |

Stage 2 starts only when Phase 7 is complete.

## 3. The shape of the code

| Package | What it holds |
| --- | --- |
| `headwaters/cli` | the `headwaters` command: `start/stop`, `catalog`, `keys`, `doctor`, `tray`, `wizard` |
| `headwaters/control` | doctor, diagnostic bundle, key management, release gate docs, status page |
| `headwaters/core` | routing and execution: `analyzer/` (request analysis), `catalog/` (loader, discovery, trust, updater), `executor/` (cloud + Ollama backends), `hardware/` (GPU, fit, residency), `policy/` (filters, scoring, ladder, decide) |
| `headwaters/daemon` | the composition root that wires core, gateway and mcp into one process |
| `headwaters/features` | star features: `context_engine`, `ledger`, `paid_scopes`, `tray`, `usage_report`, `wizard` |
| `headwaters/gateway` | the OpenAI-compatible and Anthropic-compatible HTTP faces, footer, aliases |
| `headwaters/installer` | writing Headwaters into a host's own config files |
| `headwaters/mcp` | the MCP server and `tools/` (frontdoor, scope, verify, workspace, handoff) |
| `headwaters/packaging` | install layouts, runtime, autostart, payloads (Stage 2 machinery) |

Load-bearing boundaries, enforced by `.importlinter`:

- D14: `gateway` owns `fastapi`/`starlette`/`uvicorn`; `mcp` owns the MCP SDK;
  `daemon` is the composition root and nothing imports it.
- `headwaters.core` is a leaf: it imports none of features, gateway, mcp, daemon, control, installer, packaging or cli.

litellm use is isolated behind `core/executor`; only `core/executor/cloud.py` may import it.

`catalog/seed_catalog.json` holds 11 provider rows and 6 `free_limits` rows.
`tests/` holds 209 test files.

## 4. The decisions a session must not get wrong

| # | Rule | Damage if unknown |
| --- | --- | --- |
| D5 | One self-contained product: gateway, MCP and hooks on one core, no dependency on another router. | You add a second product by accident. |
| D9 | The configured workspace path is the boundary; host roots may only narrow it. Sampling is not built (HDW-905 deferred). | You widen file access or build a deferred tier. |
| D13 | Never route Ollama `:cloud` / `-cloud` models, in any tier. Discovery drops them; the executor refuses them. | Local-scope prompts silently leave the machine. |
| D16 | Two stages. Stage 1 is dogfooding from source; Stage 2 is distribution and sale. | You package, sign or publish early. |
| D18 | One provider-name registry (`KeyStore.providers()`); never show an environment-variable name. | Surfaces drift and keys go to the wrong host. |
| D19 | Never write "paid" bare: say **paid tier** or **paid product**. | An entitlement bug spends real money. |
| D20 | Capability comes from an observed tool call, not advertisement; `models.overrides` is applied last. | You trust a claimed `tools_ok` that does not exist. |
| D21 | `keep_warm_model` defaults to `off`, not `auto`. | The person's GPU is held indefinitely. |
| D23 | A zero price is not free access: an allowance-consuming $0 model is `free_access: "plan"`, never `"all"`. | You claim a subscription model is unrestricted. |
| D24 | `licence` is documentation only; no runtime path reads it and no gate will be built. | You build theatre gates. |
| D25 | Free cloud models plus local Ollama is the whole niche. | New work targets paid routing. |

Two standing rules: **decisions override packets** (D7 and later win over
conflicting packet text), and tickets under "Deferred tickets" are **not built**
(HDW-905).

## 5. Rules of the road for any ticket session

- **Worktrees.** Work only in your own worktree with worktree-relative paths.
  Never read or write the primary clone.
- **Verification.** The OWNER is the tester. No full `pytest`, no full-suite
  `mypy`, no re-runs "to be sure". Run a command once only if needed, and say why.
- **Done and report.** A ticket is done when the diff reads correctly and the
  report says what to try by hand, not a wall of test output.
- **Size.** Target a diff under ~300 lines, one subsystem.
- **Bitdefender.** Batch commands. Never `Start-Process -WindowStyle Hidden`;
  never `ProcessStartInfo` with a timeout and `Kill`.

## 6. Open owner questions

A child must never decide these. The owner answers, or nothing moves.

- **FU-503-3** - the stored Kilo key returns 401 `INVALID_TOKEN` while the
  keyless path works. The owner must clear or replace it.
- **HDW-601 question 1** - no documented 10k-file time budget exists anywhere in
  the repo, so 10 s was measured and recorded in `ASSUMPTIONS.md` and FU-601-2.
  Confirm or replace the number.
- **HDW-601 question 2** - `FEATURE_SPEC.interfaces` is deliberately empty
  because the plugin, MCP-tool and daemon wiring is HDW-602.
- **The 11 owner release-evidence steps** - all Stage 2, all owner work.
- **Whether to write a discovery-engine ticket at all** - the recommended moat,
  not yet approved.

## 7. Next ticket order

From `docs/reports/FREE-ONLY-TRIAGE.md` ("Recommended next-ticket order"),
corrected to today: step 1 (ingest FU-144-1/2/3/4 and FU-149-1/2) is done at
`1e7f3aa`, and HDW-601 is done at `21cd924`. What remains:

1. **FU-503-3** - owner action on the Kilo key.
2. **HDW-602** - wire the context engine into a plugin, MCP tools and a daemon drain.
3. **The discovery-engine ticket decision** - approve or reject, then write the packet.
4. **HDW-604 + re-scoped HDW-606** - verification, escalating within free and local rungs only.

The owner chooses, in this order. Nothing starts without a yes.

## 8. Where to look things up

| File | Question it answers |
| --- | --- |
| `docs/DECISIONS.md` | every decision D1-D25, plus detail sections |
| `docs/ASSUMPTIONS.md` | verified external facts and the risk register |
| `docs/PLAN.md` | Stage 1 vs Stage 2, and the phase map |
| `docs/FOLLOWUPS.md` | the FU ledger: what is open, done or decided |
| `docs/PROVIDERS.md` | the "add a provider as data" checklist the discovery engine is meant to replace |
| `docs/DOGFOOD.md` | how to run the current build from source |
| `docs/FUNCTIONAL_TESTS.md` | the hand-run Stage 1 checks |
| `docs/reports/HDW-601.md` | the context engine's symbol index |
| `docs/reports/FREE-ONLY-TRIAGE.md` | what D25 does to the remaining backlog |
| `headwaters_ticket_packets/` | per-ticket scope, context and workflow |

## 9. Contract

Read this page first, grep the decision number you need out of `DECISIONS.md`,
and do not read `DECISIONS.md` end to end. If you find yourself needing most of
it, the ticket is too big.
