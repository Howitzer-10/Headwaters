# Free-only triage: the remaining backlog through D25

The owner changed the product's goal (D25, 2026-11-07). This report is the record of what that does
to the work that is still unbuilt, and what it leaves missing. It is a recommendation: the backlog
spec, the ticket packets, `docs/FOLLOWUPS.md` and every code file are untouched by this run, and are
edited only after the owner approves the edits.

## The direction

This product is now only two things: the smartest router and discoverer of free cloud provider
models, and the best use of local Ollama models that can work on the person's own graphics card.
Paid-model routing is out of scope for new work. Existing paid code — budgets, paid scopes, the
ledger's savings counterfactual — stays in place and de-prioritised, not removed; the counterfactual
changes job from a cost centre to a pitch ("what this would have cost you"). The strongest machinery
already on master is already on this path: the provider registry and its access vocabulary
(HDW-514, HDW-519 — 11 provider rows and 6 `free_limits` clause rows in
`catalog/seed_catalog.json`), the downward cascade that only steps to a strictly lower tier
(HDW-516, HDW-517), and per-provider free-limit parsing consumed as advisory ranking
(`headwaters/core/quotas.py`). What is *not* on master, and is the actual moat, is any living
mechanism that finds new free providers and keeps their limits true. That gap is called out in
"The missing piece" below as a proposal, not assumed to exist.

## Unbuilt tickets

Every ticket in the backlog spec (Phases 6–9) that has no report in `docs/reports/` and no commit on
master. Ticket names are the working `HDW-` names; the spec numbers them `SPW-`.

| id | one-line purpose | verdict | reason through the free-only lens |
| --- | --- | --- | --- |
| HDW-601 | Incremental tree-sitter symbol index, git-aware invalidation, file watcher in SQLite | Keep | Local context is what makes a 14B model usable on a real repo; it is the "best use of local" half of the niche and needs no cloud call. |
| HDW-602 | CPU embeddings via Ollama plus token-aware context packing to the model's `num_ctx` | Keep | Runs on the person's own machine by construction; packing to a computed budget is exactly how a free or local model stops overrunning its window. |
| HDW-603 | Analyzer layer 2: embedding kNN classifier for task class | Keep | A better task class is what makes a free-tier or local pick accurate; the classifier is local and free. |
| HDW-604 | Verification v2: answer grounding check plus project checks | Keep | A free or local answer is only worth having if it is checked; verification is the trust half of the niche. |
| HDW-605 | Worktree execution of a proposed diff with limits and a deny-list | Keep | Makes an unvetted free or local diff safe to accept; on-niche twice (quality and safety) with no paid dependency. |
| HDW-606 | Verify-then-escalate loop: retry a rung higher with a budget and failure reasons | Re-scope | Re-scope to free and local rungs only: escalate within free tiers, then to local, never to a paid tier; drop the paid `max_escalations` budget from the default path (keep the machinery for the de-prioritised paid scope). |
| HDW-607 | Handoff packets and sticky sessions (D11) for long-running work | Keep | Keeps a long session on the best free or local model and moves it cleanly when that stops working. |
| HDW-608 | Beta 0.3 review gate: local quality with and without the context engine and verify v2 | Keep | Its own measure is the niche's evidence; add a free-tier leg and use it as the gate for 601–605. |
| HDW-701 | Hooks engine framework plus the first Claude Code adapter | Drop or Defer | Defer. Host breadth, not the moat; the reference adapter is a paid host subscription, and nothing here improves discovery or routing. |
| HDW-702 | Hooks for the remaining hosts, one adapter per host | Drop or Defer | Defer. Depends on HDW-701 and shares its reason; revisit only after the catalog is living. |
| HDW-703 | Installers and setup docs for the remaining hosts, one per host | Drop or Defer | Defer. Distribution breadth; it makes the product wider, not the niche deeper. |
| HDW-704 | HeadwatersBench: routing accuracy, quality vs paid baseline, savings at equal quality, tool-call reliability | Re-scope | Re-scope so free-tier quality, local quality and discovery coverage are the primary suites; the paid baseline becomes one optional ceiling line, not the yardstick. |
| HDW-705 | Host tool-call eval suite, 100+ real tasks including weak free and local dispatchers | Keep | Already aimed at weak free models and local dispatchers — the exact population the niche serves; it is the only suite that measures the product's own customers. |
| HDW-706 | Beta 0.4 review gate | Keep | The gate stays; it also carries the no-unverified-claims rule when results are published. |
| HDW-801 | RC hardening: security review, dependency licence audit, secrets scan, open-source readiness files | Keep | Nothing paid-specific to unwind; the hardening work is unchanged by the direction. |
| HDW-802 | Decision evidence report: five-question table, evidence, `DECISION_1_0.md` draft | Re-scope | Re-scope the question it answers: is the free-only router plus a living catalog worth shipping, rather than Lite/Pro. The evidence assembly is unchanged. |
| HDW-803 | Apply the 1.0 decision, including the extract-★-into-a-paid-package branch | Re-scope | Re-scope the branch set: the paid extraction is no longer the default outcome; the live question is which free/local mix ships and how discovery stays fed. |
| HDW-901 | Fusion "deliberate": consult a paid fusion model via OpenRouter, budget-capped | Drop or Defer | Drop from new work. Depends on HDW-502 paid scopes and routes paid models by design; it is the opposite of the niche. It can return if the owner reverses D25. |
| HDW-902 | Rubber duck overlay: a separate reviewer model that discusses the current answer | Drop or Defer | Defer. It works in local and free scope, so it is not contrary — but it makes no pick better and finds no provider. |
| HDW-903 | Image generation tool via a paid image model | Drop or Defer | Defer. A paid modality; the niche is text routing over free and local models. |
| HDW-904 | Learning: learned per-model pass rates, local prompt-bank growth, opt-in anonymised export | Keep | Turns verification outcomes and thumbs into better free and local picks; the learning loop is the product's own data, which is the one advantage a competitor cannot copy. |
| HDW-905 | MCP sampling tier | Drop or Defer | Defer. Already deferred by D9; this report does not lift it. |

Counts: 11 Keep, 4 Re-scope, 7 Drop or Defer (1 Drop, 6 Defer).

## Elevated follow-ups

Each row was re-verified against master at `4aa66e3` before being cited. Where the code the follow-up
named has changed, that is said plainly and the follow-up is not claimed to still hold.

| FU | Code site verified | Still true? | Why the free-only direction raises it |
| --- | --- | --- | --- |
| FU-201-3 | `headwaters/core/engine.py:107-132` — `entry.refuses(name)`, then the `provider == OPENCODE` branch: "OpenCode free models can only be used from inside OpenCode." `TargetBuilder` still raises `provider_unsupported` | No. Closed by HDW-150 (commit `16b264e`) as a *decision*, not a fix: discovery no longer offers the ids and the refusal stays as defence in depth | Not raised as an open gap. What the direction takes from it is sharper: a free tier that exists but is not callable by a third-party client is not a free provider, and today that judgement is curated by hand. Deciding reachability per provider — and re-deciding it when a provider changes its terms — is exactly the probe the discovery engine has to own. |
| FU-201-2 | `headwaters/core/hardware/state.py` (`_is_ollama`, the hidden-memory fallback that subtracts `/api/ps` `size_vram`); `headwaters/core/keepwarm.py:137-142` still returns `"gpu_busy"` from `_blocked()` | No, as filed: fixed by HDW-144 (commit `5f6f21e`), and fixed in `state.py`, not in `hardware/gpu.py` which the FU named | Raised, through the residue HDW-144 left: **FU-144-1** (with memory hidden for every process, a game plus Ollama on Windows reads not-busy), **FU-144-2** (`decode_smi` on a non-ASCII process name is assumed, not proven), **FU-144-3** (`/api/ps` `size_vram` under-reports badly — 1.2–1.7 GiB against 10.9 GiB measured) and **FU-144-4** (no live test for the repro). GPU residency is now the product's second pillar, so a wrong busy reading either holds VRAM nobody asked for (D21) or refuses to warm when it should. |
| FU-139-1 | `catalog/seed_catalog.json`: no placeholder model ids; the only `example-` string is the `example-placeholder` *secret pattern* name at line 203, which is a placeholder, not a model. `test_seed_has_no_placeholder_ids` | No. Closed by HDW-149 (commit `235bb52`) | Not raised as filed. What it leaves behind is raised: `expected_tps` are estimates rather than measurements, the live 429/402 bodies were not captured, and the catalog is **unsigned** — the trust anchor is still empty (`EMBEDDED_PUBLIC_KEY_B64 = ""` in `headwaters/core/catalog/trust.py`), so no downloaded catalog can be accepted. That is FU-149-1 and FU-149-2, both still open and neither yet in the follow-up ledger. |
| FU-139-2 | `catalog/seed_catalog.json` `wall_patterns`: 17 rows, every one with `source`, `checked: 2026-11-01` and `verify: true` (0 rows left `verify: false`) | Partly. Sourced and dated, but the exact bodies it named are doc-derived or community-reported — Anthropic's credit-balance text is "from community reports, not in the docs" and not live-captured; OpenRouter's `free-models-per-day` token and Gemini's per-day quota id are documentation readings | Yes, and more than before: the wall class is what decides whether the router steps down to a lower free tier or to local. A missed exhaustion wall keeps retrying a dead free tier (the generic 429 row classifies `rate_limit`, which is retryable); a false wall steps down too early. |
| FU-140-1 | `headwaters/core/catalog/schema.py:58` — `reasoning_ok` is an optional `bool` defaulting to `None`; `headwaters/core/catalog/merge.py:105-106, 131, 157` — live observation wins, catalog fills `None` | No. The field exists and is merged; closed by HDW-149. The FU's premise ("neither `schema.py` nor `merge.py` has `reasoning_ok`") is no longer true | Not raised as an open gap, but it is now load-bearing: a catalog-only free row can serve a reasoning request only because of this field. A regression here silently removes free models from `think`, and a free-only product has fewer rows to lose. |
| FU-122-5 | `catalog/seed_catalog.json` `wall_patterns` (17 rows, all sourced) and `free_limits`: only 2 of 6 rows are `verify: true` (openrouter, nvidia); mistral, groq, cohere and cloudflare are unverified | Partly. Merged into HDW-139 and delivered by HDW-149 for the sourcing half; the live-capture half is still open | Yes, and it is the sharpest of the six. Provider-specific wall rows exist for only `gemini` and `opencode` of the 11 catalog provider rows (the rest of the specific rows cover built-ins: openai, anthropic, openrouter, ollama). A free tier that answers "daily quota exhausted" with a 429 therefore falls through to the generic `*` 429 row and is classified `rate_limit` — retryable — instead of `exhausted`. For a product whose ordinary failure *is* the free limit, that is the wrong class in the common case. |
| FU-503-3 | `docs/FOLLOWUPS.md` "Waiting on the owner" (lines 14-20): a stored `KILO_API_KEY` answers 401 `INVALID_TOKEN` while the keyless path answers 200 | Still open, still the owner's to answer | Raised in weight only, and **not decided here**. The keyless free path is the Kilo value, and a stored bad key breaks both discovery and every keyed call; the `kilo` catalog row also carries `verify: false`. This report records that it is now on the niche's critical path, and leaves the question with the owner. |

Two ledger gaps worth naming, because the triage lens depends on them: **FU-144-1/2/3/4** and
**FU-149-1/FU-149-2** exist only in their ticket reports and in `docs/ASSUMPTIONS.md`, not in
`docs/FOLLOWUPS.md`, so nothing currently tracks them.

## The missing piece

A proposed ticket — the free-provider discovery engine. Outline only; no packet is written, and none
of this runs before the owner decides.

**What it must do to be "like nothing does today".**

- **Find new free providers, not just new models.** Today a new provider is added by hand following
  "Add a provider as data" in `docs/PROVIDERS.md`: a person notices it, fills in a `ProviderEntry`,
  finds the models endpoint, and runs a refresh. That is the whole discovery mechanism, and it is a
  checklist. The engine has to surface candidates — provider directories, model-listing aggregators,
  provider changelogs, and the free-tier terms a provider publishes — into a reviewable queue with
  the evidence for each.
- **Probe rate limits live, and record them as measurements.** `free_limits` is curated and
  **advisory by decision**: `docs/PROVIDERS.md` says a counted request ranks a model down, it never
  refuses a call (D5). Two of six rows are `verify: true`. The engine has to be able to test a
  claimed limit, stamp what it actually observed, and let the rank be driven by the measurement
  rather than by the note.
- **Probe capability truths, not capability claims.** D20 already settles the principle: capability
  comes from an observed tool call, not from an advertisement. `capability_probes` (HDW-512) stores
  exactly those observations. The engine has to run those probes across free and local models as a
  first-class job, so "this free model can actually call tools" is a stored fact with a date.
- **Decide reachability, per provider, as a fact with a date.** HDW-150 is the template: a free tier
  can exist and still not be callable by a third-party client. That judgement is currently a person's
  paragraph in a decision; it needs to be a probe-able property that decays.
- **Sign what it publishes, and verify what it accepts.** `catalog/trust.py` verifies an ed25519
  signature and `scripts/sign_catalog.py` signs, but `EMBEDDED_PUBLIC_KEY_B64` is empty, so a fresh
  catalog cannot be accepted at all. Freshness without a trust anchor is a supply-chain hole, not a
  feature.
- **Keep the catalog fresh.** `checked` dates exist on provider rows, `free_limits` rows and
  `wall_patterns`, and nothing re-checks them. A stale catalog is a router that confidently picks a
  dead free tier — the failure mode the niche cannot afford.
- **Answer the `free_access: plan` residual risk.** `docs/PROVIDERS.md` already states it: "`plan`
  says a free path exists, not that *this* key is on it". Nothing in the product can currently tell
  which plan the stored key is on, so "free" is a claim about the provider's menu, not about the
  person's account.

**What exists to build on.** The registry is data, not code (`headwaters/core/providers.py`,
`ProviderEntry` with `charges_money` and the four-valued `free_access` in
`headwaters/core/catalog/schema.py`, and `format_access` rendering it on every surface); the 11
provider rows and 6 `free_limits` clause rows are in `catalog/seed_catalog.json`; `free_limits` is
already consumed as advisory ranking by `QuotaTracker` in `headwaters/core/quotas.py`; discovery
(`headwaters/core/catalog/discovery.py`) already does per-source listing for *known* providers with
one overall wall-clock budget, per-source graceful failure, an airplane/local-only lock, ETag and
offline handling, and removes models a refresh no longer lists; `headwaters/core/catalog/sources.py`
holds the source table; `headwaters/core/catalog/updater.py` and `trust.py` are the signed-download
path; `capability_probes` is the observation store; and `docs/PROVIDERS.md` is the procedure the new
work replaces. `scripts/host_probe.py` is the existing precedent for a probe that reports what a host
actually does.

**What is genuinely new.** Automated candidate discovery for providers (nothing in the tree looks for
a provider it does not already know); live measurement of limits and capabilities stamped into the
catalog rather than curated notes; a reachability verdict per provider that expires; the trust anchor
in use, so a signed fresh catalog is actually accepted; and any account-plan truth at all. The rest
is extension of machinery that already exists.

**Open design questions — owner only, recorded as questions and left unanswered.**

- How proactive may discovery be? Everything on master reaches the network only on an explicit
  command, and the airplane lock (`--local-only`) must keep working. May the engine reach providers
  that are not yet in the catalog, or only ones a person has nominated?
- Whose network egress is acceptable, and is consent per-run, per-provider or standing?
- Does probing a limit spend the account's quota? A probe that burns the free tier it is measuring is
  a cost the owner has to accept or bound.
- Where does the signing key live, and who runs the sign step — the owner by hand (as FU-149-2 says
  today), or a release process?
- What refresh cadence is right, and what happens to a verdict that has not been re-probed?
- Which do we trust more when they disagree: a broad catalog of shaky free providers, or a narrow one
  we can prove? This is the ranking question, and it decides what "smartest router" means.
- Does a provider that has gone paid get dropped from discovery, kept but marked, or kept until the
  person notices?

## Recommended next-ticket order

The owner chooses before any of this runs. Recommended, in order:

1. **Ingest the two orphaned follow-up sets into the ledger** — FU-144-1/2/3/4 and FU-149-1/2 are
   real, still open, and tracked nowhere (`docs/FOLLOWUPS.md` is the edit; not made here).
2. **FU-503-3** — the owner answers the Kilo key question, because a broken stored key sits directly
   on the keyless free path the niche depends on.
3. **HDW-601 + HDW-602** — the local-quality half of the niche, which needs no new direction work and
   makes every free pick cheaper to run.
4. **Approve (or reject) the discovery-engine ticket** from "The missing piece" and write its packet;
   this is the moat, and everything else in the catalog is data that slowly rots without it.
5. **HDW-604 + the re-scoped HDW-606**, so the free and local answers those two rungs produce are
   checked before escalation is designed around them.

The re-triage table above is a recommendation, not an edit: the backlog spec, the packets and the
follow-up ledger still describe the old order until the owner approves a separate change.
