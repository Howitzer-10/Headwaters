# Reference: VRAMWeave-MCP prior art

## 1. What this is

VRAMWeave-MCP is a private sibling project: an MCP-only helper that runs alongside an IDE on one
machine. It is **not code this repo owns, not a dependency, and not a specification**. This note
records it as prior art for cost-, quality- and reachability-aware routing, so a future ticket does
not re-derive work that already exists elsewhere.

Source: the sibling project owner's description dated 2026-11-03. Nothing in it was verified by us
today. Prices, model ids, thresholds and timings below are reproduced as **claims from that
description**, not as facts checked against the provider or the project. D19 already records the
project by name as provenance (`docs/DECISIONS.md:31`).

What the description covers: a model guide generated for OpenRouter and Ollama (per-model context,
image/tool support, paid prices, a coding index, an expiry date, a live "status now", measured speed
and gate-pass count); a local Ollama table (GPU/RAM, installs, size/quant/params/max context, the
reduced context actually fitted to VRAM, a measured-vs-estimated fit judgement, vision flag); a
v1.1.0 control panel with Dashboard, API Key & Rate Limits and IDE Config Injector tabs
(local/cloud/free-cloud requests and tokens, estimated API cost saved, latency, time saved by local
drafting, gate pass rate, VRAM state); four global routing modes; adaptive complexity routing; a
paid-model safety rule; and a keep-warm heartbeat. It advertises zero telemetry, cloud only when
routed, and DPAPI-encrypted keys.

## 2. Capability comparison

| Capability | How VRAMWeave-MCP does it (as described) | What Headwaters has today | Gap |
| --- | --- | --- | --- |
| Per-model guide facts | One row per model with context, image/tool support, paid price per 1M in/out, coding index, optional expiry, live "status now", "speed here" and "your results" | Catalog rows carry `ctx`, modalities, `tools_ok`/`json_ok`, `price_in`/`price_out` and `price_date` (`catalog/seed_catalog.json:13-27`; schema `catalog/catalog.schema.json:387-425`) | No coding index, live status, measured speed or pass count; prices are flat |
| Tiered pricing | Paid rows note tiers, for example "higher above 272,000 prompt tokens" | One `price_in` / `price_out` pair per model (`catalog/seed_catalog.json:24-25`) | No threshold or tier structure, so a long prompt is priced at the flat rate |
| Published quality index | A "coding index" is an Artificial Analysis score republished by OpenRouter, with "(N general)" when only a general score exists and `n/a` when none is published | Hand-maintained task `scores` (`catalog/seed_catalog.json:27`) and scoring terms (`headwaters/core/policy/scoring.py`) | No external published index and no provenance line for a score |
| Reachability and expiry | `status now` is `ok`, or a failure such as "rate-limited upstream, HTTP 429/5xx, not found 404 - tried last until <time>"; expiry is a date; restrictions are flagged, e.g. `thinkingmachines/inkling:free` restricted to agentic-harness apps (OpenRouter 403, verified 2026-11-02) | `deprecated_by` on a model, persisted walls (`headwaters/core/quotas.py:38`) and a per-request exclusion set (`headwaters/core/router_run.py:29`) | No per-model "verified on" reachability state or expiry; a listed but unusable model can still be picked |
| Local model table | Knows the machine's GPU/RAM, installs, per-model size/quant/params/max context, the reduced context used to fit VRAM, and a measured-vs-estimated "fits your GPU" judgement | Hardware fit computes `fits` / `offload` / `no_fit` (`headwaters/core/hardware/fit.py:24,110`) and filters on it (`headwaters/core/policy/filters.py:102-105`); `models.size_vram_gb` is persisted (`headwaters/core/state_schema.py:82`) | No persisted local inventory or fit provenance per model; fit is recomputed, not recorded |
| Cross-session metrics and cost/latency accounting | Dashboard shows local/cloud/free-cloud requests, token share, estimated API cost saved, average latency (sample 10.3 s local / 1.3 s cloud), time saved, gate pass rate (sample 86%, 6 of 7) and VRAM state ("warm") | Decisions and outcomes persist status, latency, tokens, cost and counterfactual cost (`headwaters/core/state_schema.py:55-60`; `headwaters/core/router_log.py`) | No aggregated per-model view and no pass-rate, "time saved" or "cost saved" rollup |
| Routing modes and complexity pre-router | Four global modes: Auto Cascade, Force 100% Local, Force 100% Cloud, Cloud Draft First. Auto Cascade chooses complex vs simple with a keyword + size scan (thresholds: complex 2, multi-file 3) | Scope/tier selection (`headwaters/core/scope.py:35,112-114,270-272`) and the analyzer (`headwaters/core/analyzer/requirements.py:198-251`) | No equivalent four-way mode switch and no cheap pre-router; the analyzer is the only path |
| Paid-tier vocabulary and safety | Paid models run only when named (`force_model`, or `paid` for the saved preferred model); with "Free models only" it shows the price and free alternatives and waits; every paid answer ends with its price | D19 separates paid **tier** from paid **product** (`docs/DECISIONS.md:21`) and records that a credits wall stops rather than downgrades (`docs/DECISIONS.md:29`); `paid_scopes` gates paid tiers (`headwaters/core/scope.py:35,112-114,270-272`) | No pre-call price prompt and no price-on-every-answer in code at this base; D19 is policy, not implementation |
| Per-request failure memory | No re-pick is described per request; health is a per-model status kept by the tool | Base has only `RouteRun.excluded` (`headwaters/core/router_run.py:29`); HDW-513 adds `FailureMemory`, failure classes and an honest terminal message (`docs/reports/HDW-513.md`, `headwaters/core/router_failures.py`, on the HDW-513 branch and unmerged here) | At this base a failed model can be re-picked on the next attempt, and the terminal error hides the cause |
| Walls and quotas | No `ok` / `cooldown` / `exhausted` vocabulary is described; health is per model | Persisted per scope/key/provider (`headwaters/core/quotas.py:38,254,367`; HDW-122, `docs/reports/HDW-122.md:10-13`) | No cross-provider live health, and balance polling is OpenRouter-only |
| Tier ladder and round-robin | Routing is per call; no tier ladder is described | The ladder orders cheap, paid, below-floor and free-router entries (`headwaters/core/policy/ladder.py:100-140`); round-robin is HDW-513's D17 claim (unmerged; numbering note `docs/DECISIONS.md:23`) | No round-robin at this base, so one tier can starve another until HDW-513 lands |
| Paid-tier gating | A personal allowlist; a paid model is used only when the person names it | `features.paid_scopes` excludes paid tiers (`headwaters/core/scope.py:35,112-114,270-272`) | A local boolean, not an entitlement or licence check (D19) |
| Balance polling and per-call cost | Not described for balances; cost shown is an estimate from the guide | Only OpenRouter's `/key` balance is polled; other providers are skipped (`headwaters/core/key_poll.py:33,86,99`) | No balance endpoint for other paid providers; OpenCode Go's `usage.opencode` block (`billing_source`, `list_cost`, `allowance`, live probe 2026-11-03 on the HDW-515 branch, not at this base) is the first real non-OpenRouter cost signal |
| Provider catalog | The guide is generated for OpenRouter and Ollama, not a shared catalog | `catalog/seed_catalog.json` is the provider/model catalog; discovery fills it | No ingestion of per-call usage or provider-published cost metadata |

## 3. Ranked ideas worth borrowing

1. **A price table with tiered pricing.** Why: cost-aware routing is only as good as the price it
   uses, and a flat price understates a long-context call. Sketch: add
   `price_tiers: [{above_tokens, input, output}]` to the model schema and resolve the applicable
   tier from `est_tokens` at decision time. Where: `catalog/catalog.schema.json` and
   `catalog/seed_catalog.json` for the data; a resolver beside `headwaters/core/catalog/` and a
   cost term in `headwaters/core/policy/scoring.py`.

2. **A published quality index as the quality signal.** Why: we may not need to benchmark models
   ourselves for a first ranking; a republished index gives a comparable score, provided the
   "general only" caveat travels with it. Sketch: add `quality_index` with `value`, `source`,
   `general_only` and `retrieved_on`, then use it as a prior or tie-breaker rather than replacing
   the task scores. Where: catalog schema and rows; `headwaters/core/policy/scoring.py`. Check the
   index's licence and attribution before republishing it.

3. **Per-model reachability and expiry with a `verified_on` date.** Why: a model can be listed and
   still be unusable from an API client; the `thinkingmachines/inkling:free` restriction and the
   OpenCode reachability problem reported today are the same shape of problem. Sketch: add
   `reachability: {state, reason, verified_on, retry_after}` and a hard filter on `unusable`, while
   letting `unknown` through. Where: catalog schema and rows;
   `headwaters/core/policy/filters.py`; wall recording stays in `headwaters/core/quotas.py`.

4. **Persist per-model health, speed and pass rate across sessions.** Why: the outcome table
   already records latency and verification per decision, but nothing feeds those facts back into
   ranking; D10 leaves learned pass rates to HDW-606 (`docs/DECISIONS.md:14`). Sketch: aggregate
   `outcomes` by model into a `model_stats` table (successes, verify passes, median latency,
   tokens/s) and expose one scoring term. Where: `headwaters/core/state_schema.py` for the table,
   `headwaters/core/router_log.py` for the write path, `headwaters/core/policy/scoring.py` for the
   read path.

5. **Stop with the price before a paid call, and put the price on every paid answer.** Why: this is
   the owner's stated preference already recorded in D19 (`docs/DECISIONS.md:29`), so the prior art
   is **corroboration, not a new idea**. Sketch: when a paid-tier candidate is chosen, show the
   price and the free alternatives and wait for an explicit choice; append the resolved cost to
   every paid answer. Where: the decision/execute boundary in `headwaters/core/router.py` and
   `headwaters/core/router_run.py`; cost from `headwaters/core/router_log.py`; output text in the
   CLI and host adapters.

6. **Local cost/latency accounting.** Why: "cost saved" and "time saved" are what make a local tier
   a real choice rather than a fallback; the fields already exist. Sketch: roll up `cost_usd` versus
   `counterfactual_usd` and local-versus-cloud latency per session, and surface the totals before
   building a dashboard. Where: `headwaters/core/state_schema.py:55-60` (fields),
   `headwaters/core/router_log.py` (writes), `headwaters/cli/` (status output).

7. **Cloud draft first, with local fallback.** Why: a cloud draft gives speed and quality, and a
   local fallback survives a quota wall or outage; it fits the existing ladder and verification
   shape. Sketch: add a strategy that places one cloud candidate first and one local candidate
   second, with the existing verification gate before accepting the draft. Where:
   `headwaters/core/policy/ladder.py` (ordering) and `headwaters/core/policy/scoring.py`
   (strategy weights).

8. **A cheap request-complexity pre-router.** Why: the analyzer costs work on every call; a keyword
   plus size scan can route the obvious cases and reserve the full analyzer for ambiguity. Sketch:
   a pure `preclassify(messages) -> complexity band` with tunable thresholds, used only to choose
   analyzer depth, never to bypass hard filters, scope or walls. Where: a new
   `headwaters/core/analyzer/preclassify.py` called from `headwaters/core/router_prep.py`.

9. **The keep-warm heartbeat as a latency lever for a local tier.** Why: a cold local model loses
   the latency comparison and can make the local tier look worse than it is. This is already built:
   `KeepWarm.tick` pings loaded Ollama models (`headwaters/core/keepwarm.py:109`), `keep_alive`
   defaults to `30m` (`headwaters/core/rules.py:102`) and the daemon schedules the heartbeat
   (`headwaters/daemon/service.py:406`). The source sample uses `15m`; our longer default is a
   tuning choice. Sketch: keep it, and tune the interval and target model from measured cold-load
   data. Where: `headwaters/core/keepwarm.py` and `headwaters/core/rules.py`; no new subsystem.

## 4. What NOT to copy

- **It is a per-user MCP helper inside one IDE with a personal allowlist, not a shared daemon with
  an authority and multiple hosts.** Its state, key handling and "preferred model" assume one
  person on one machine. Headwaters has a daemon, a key store, a shared catalog, multiple host
  surfaces and provider identity as a first-class concept (D18). A personal allowlist cannot express
  our scopes, pins, locks, provider registry or shared walls.

- **Its "gate" is a self-check it owns; our hosts are third-party clients.** VRAMWeave can require
  its own draft to pass before it shows the result. Headwaters cannot assume a host will run a
  verifier, and must not treat a host's acceptance as verification; our gate is a plugin the router
  owns (`headwaters/core/router_verify.py`), and some hosts will never expose it.

- **Its routing is per call; ours is policy-driven with pins, tiers and walls.** A per-call pick is
  free to re-evaluate everything each time. Headwaters has stickiness (D11), hard filters, effective
  scope, per-request exclusions and persisted walls (`headwaters/core/quotas.py`). Copying a
  per-call router would either bypass those or duplicate them in a second decision path.

- **Its assumptions do not survive our topology.** One local GPU, one user, no concurrent hosts, no
  entitlement system, no network block or private lock, no per-request budget, no shared session
  state, and no third-party host identity. Its paid rule is an allowlist, not a tier gated by
  `paid_scopes`; its pass rate is self-reported, not a host-verifiable signal; its sample latency
  and pass-rate numbers come from one machine and must not become our defaults without measurement.

## 5. Open questions for the owner

1. Which published index may we republish, under what licence and attribution, and is a coding index
   enough for paid-tier ranking or do we need our own calibration for our task mix?
2. Should reachability be a catalog fact with a `verified_on` date, a live probe, or both, and who is
   allowed to mark a model unusable?
3. Do we want cloud-draft-first as a scope/strategy, and how should it interact with pins, walls and
   mandatory verification?
4. May a persisted pass rate influence ranking (D10 leaves learned pass rates to HDW-606), and should
   that signal be local-only or opt-in?
5. What is the price-display contract: a pre-call prompt only in hosts that can ask, a price line on
   every paid answer, or both, and how does a third-party host that renders no footer receive it?
