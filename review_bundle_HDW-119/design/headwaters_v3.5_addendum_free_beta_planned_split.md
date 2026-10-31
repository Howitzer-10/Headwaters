# Headwaters v3.5 Addendum: Free Beta with a Planned Split

This addendum applies on top of v3–v3.4. Where they conflict, this file wins.

## A. Decision
**D6: Launch as a free beta with every feature included (Option 3). Decide at 1.0 whether to split into Lite and Pro (Option 4), using the evidence in §E.**

What this replaces:
| Earlier text | Now |
| --- | --- |
| v3 §10.1 and v3.1 §B: two editions, separate private `headwaters-pro` repo, edition packaging | **One repo, one build, no edition packaging.** Keep internal module boundaries so a split stays cheap (§F). |
| v3.1 §D: pricing and release plan (Lite 0.1, Pro Early Access, Pro 1.0) | Free beta 0.x → decision → 1.0 (§G) |
| v3.3 §B1 "Edition" column; v3.4 §A "Edition" column and §K roadmap labels | Ignore edition labels. Use the ★ markers in §B instead. |
| Gateway listed as Pro | **The gateway is core and free.** The headline use case ("Copilot credits ran out, keep coding") needs it. |

Unchanged: the design itself, scopes and locks, the clean-room policy, ToS and privacy rules, and Windows first.

## B. Feature classification
**Core** = free forever. This is a public promise to testers: core features never move behind a paywall.
**★ Pro candidate** = free during beta. May become paid at 1.0.

| Feature | Class | Why |
| --- | --- | --- |
| Gateway (OpenAI + Anthropic endpoints, BYOK in VS Code Copilot) | Core | Without it, the main use case doesn't work |
| MCP server with the 8 front-door tools | Core | Works in every MCP host |
| Automatic failover across walls (rate limit, out of credit, overloaded) | Core | Main promise |
| Scopes `headwaters/free` and `headwaters/local`, Private Mode, project privacy rule, secret detection | Core | Trust; competes with free tools |
| Hardware guard (VRAM/KV fit, computed `num_ctx`, warm model, sysmem-fallback detection, battery) | Core | Differentiator that makes free/local usable |
| Basic verification (parse + placeholder scan + `git apply --check`) | Core | Safety baseline |
| Installer for VS Code/Copilot, Cline, OpenCode, Claude Desktop; `doctor`; CLI; status page | Core | Low support load |
| Catalog updates | Core | Keeps everyone working as models change |
| Scopes `headwaters/auto` and `headwaters/cloud` with your own paid keys, budgets, premium tier | ★ | For people who already pay for APIs |
| Repo context engine (symbol index + embeddings, context packing) | ★ | Main reason to pay, if local models get measurably better |
| Verification v2: grounding, project lint/tests, worktree execution, verify-then-escalate | ★ | Main reason to pay, if it means fewer broken edits |
| VS Code extension (Headwaters models in Copilot's picker, status bar) | ★ | Convenience for Copilot users; could also be sold alone |
| Hooks engine (zero-host-token answers) | ★ | Saves more credits |
| Savings dashboard and ledger, tray app, setup wizard | ★ | Polish, plus the "saved $X" proof |
| Handoff packets, sticky sessions | ★ | Smoother switching between models |
| Learning (EMA, kNN bank growth), Fusion `think`, rubber duck, image tool | ★ | Nice extras, not purchase drivers |
| Hosts beyond the first four (installer and hook support) | ★ | Breadth |

Notes:
- Recheck the classification at 1.0. Moving a ★ feature into core is always allowed; moving a core feature to ★ is not.
- Show ★ in the app (`headwaters features`, dashboard, tool descriptions) and on the store page, with the legend: "★ = free during beta; may become part of Headwaters Pro at 1.0".

## C. Promises to beta testers (store page, README, first-run screen)
1. Core features stay free forever.
2. Beta testers who register (§D3) get Pro free if a split happens. Alternative if that's not workable: at least 50% off for life of 1.x. Pick one before launching the beta, and don't change it.
3. No usage tracking unless you opt in. Prompts, code, file paths and outputs are never collected.
4. Breaking changes are announced in release notes before they ship.

## D. Beta program
### D1. Stages
| Stage | Who | Goal | Exit criteria |
| --- | --- | --- | --- |
| Closed alpha | You + 5–15 invited users (Discord) | Install works, no data loss, routing sane | 10-minute install on a 12 GB laptop and a 16 GB desktop; no P1 bugs for 1 week |
| Open beta 0.x | Anyone on itch.io ($0 / pay what you want) | Feature feedback, compatibility across setups | Milestones in §G |
| Release candidate | Same | Freeze features, collect decision evidence | §E data collected for at least 4 weeks |
| 1.0 | – | Apply the §E decision | – |

### D2. Feedback channels
- Discord server (support, feature requests, ★ polls)
- itch.io community board (bugs)
- `headwaters feedback` CLI command, plus a feedback button in the dashboard (opens a prefilled form; attaches the redacted `doctor` bundle only if the user chooses)
- In-app survey prompt after 7 and 30 days of active use. Ask at most twice and never again after a dismissal.

### D3. Tester registration (for the §C promise)
- Opt-in: email or Discord handle, stored outside the app.
- Verify itch.io's current mechanism for granting later paid access to earlier $0 claimants (e.g. download keys). Plan to generate keys for registered testers at 1.0.

### D4. Opt-in feature-usage report
- Off by default (`telemetry: false` stays). Turned on by the user in the wizard or settings, with a preview of exactly what is sent.
- **Contents (aggregate counts only, weekly):**
  - app version, OS, GPU class (e.g. "12 GB NVIDIA")
  - which scopes and engines were used (counts)
  - which ★ features are enabled, and whether they are still enabled after 14 days
  - verification catches, escalations, failovers (counts)
  - savings bucket (e.g. "$1–5 this week")
- **Never included:** prompts, outputs, code, file names, paths, model API keys, IP-derived location beyond country.
- A "Send report now" button lets users who leave it off still share a one-time report.
- Before turning the report on, check privacy-law requirements where you sell (e.g. GDPR consent for EU users), and publish a short privacy notice.

## E. Decision at 1.0 (Option 3 → Option 4 or not)
### E1. Evidence to collect
| # | Question | Measurement | Threshold for "yes" |
| --- | --- | --- | --- |
| Q1 | Do people keep using it with only core features? | Opt-in reports + survey: share of active users whose usage is mostly core | Core-only users are a meaningful share (≥ 30%) and say core alone solves their problem |
| Q2 | Do ★ features get used and kept? | Share of reporting users with each ★ feature still enabled at 14 days | ≥ 40% for at least two of: context engine, verification v2, VS Code extension, dashboard |
| Q3 | Would people pay for them? | Survey: "Would you pay $X for ★ features?" plus price sensitivity question | ≥ 20–30% yes among active users |
| Q4 | Is Pro's value provable? | SpillBench: local-model task pass rate **with vs. without** context engine + verification v2, on the reference GPU profiles | Clear, repeatable improvement (e.g. ≥ 15 percentage points) |
| Q5 | Does a free competitor already give this away? | Re-check competitors (v3.3 §F) | ★ features still not matched for free |

Note: opt-in data is biased toward engaged users. Weigh it together with survey answers and Discord feedback, and record the sample size next to every number.

### E2. Outcomes
| Result | Do this at 1.0 |
| --- | --- |
| Q1–Q5 mostly yes | **Split.** Core = Lite (free). ★ = Pro (paid, e.g. $19–29 one-time). Honor the §C promise. |
| Q2–Q4 yes, Q1 no (almost everyone uses ★) | **One paid product** with a demo or trial; keep core free only if the promise requires it (it does: keep core free) and price ★ as Pro anyway. |
| Only the VS Code extension shows willingness to pay | **Free app + paid VS Code extension add-on.** |
| Weak willingness to pay overall | **Stay free / pay what you want**, add donations or sponsorship, and revisit at 2.0. |

Write the decision and the evidence into `docs/DECISION_1_0.md`.

## F. Architecture for a cheap split later
- One repo, one package `headwaters`. ★ features live under `headwaters/features/<name>/` and register through an internal feature registry (`FeatureSpec{id, star: bool, interfaces, default_enabled}`).
- **Core must never import ★ modules.** Enforce with an import-linter contract in CI. ★ modules may import core.
- Interfaces stay versioned: `SurfacePlugin`, `VerifierPlugin`, `ContextPlugin`, `BridgePlugin`, `DashboardPlugin`, `LedgerPlugin`. Keep the contract tests.
- Runtime feature flags in `rules.yaml` (`features: {context_engine: true, ...}`). All ★ features are on during the beta. Flags are for user choice, not licensing.
- No license checks, no edition flags, no DRM.
- If the decision is to split, extract `headwaters/features/*` into a private package registered via entry points (`headwaters.plugins`). The boundaries above should make this mostly a move, not a rewrite.
- If you later open-source the core, the same boundary keeps ★ code out of the public repo.

## G. Revised roadmap
| Release | Scope | ★ included |
| --- | --- | --- |
| **Beta 0.1** | Core: catalog, analyzer (rules + tiny CPU model), hardware guard, walls, scopes/locks, executor, SQLite, MCP front-door tools, **gateway** (OpenAI + Anthropic endpoints, failover before first token), basic verification, installer for 4 hosts, `doctor`, CLI, status page | – |
| Beta 0.2 | VS Code extension, savings ledger + dashboard, tray, setup wizard, `auto`/`cloud` with own keys, opt-in usage report, survey prompts | ★ extension, dashboard, wizard, paid scopes |
| Beta 0.3 | Repo context engine, analyzer kNN layer, verification v2 + worktree execution, handoff packets, sticky sessions | ★ context engine, verification v2, handoff |
| Beta 0.4 | Hooks engine, installers for remaining hosts, SpillBench public (incl. with/without ★ comparison) | ★ hooks, extra hosts |
| RC | Feature freeze; collect §E evidence ≥ 4 weeks | – |
| **1.0** | Apply the §E decision (split or not), packaging, pricing, tester keys | – |
| 1.x | Fusion, rubber duck, image tool, learning export, sampling tier | ★ |

Scope risk: putting the gateway into 0.1 makes the first release bigger. If 0.1 slips, ship the MCP server first as 0.1-alpha to the closed group, and add the gateway before the open beta.

## H. Store page copy (beta)
**Title:** Headwaters (Beta): Never run out of model
**Pitch:** When Copilot or your API credits hit the wall, Headwaters keeps you coding: best free cloud model, then your own GPU, picked per prompt and checked before you see it. Free during beta.
**Legend:** ★ = free during beta; may become part of Headwaters Pro at 1.0.
**FAQ: Will it stay free?**
"The core (automatic failover, free and local modes, Private Mode, GPU-aware routing, basic checks, the gateway and MCP server) stays free forever. Features marked ★ are free during the beta. If they become a paid Pro edition at 1.0, registered beta testers get [Pro free / 50% off] as thanks."
**FAQ: Do you track me?**
"No. Usage reports are off unless you turn them on, and they never include prompts, code or file names."

## I. Agent prompt patch (append; replaces the v3.1 §E "EDITIONS" block)
```
EDITIONS (replaced): Single repo, single package `headwaters`, single build. No edition
flags, no license checks, no private Pro package for now. The gateway is CORE.
FEATURE REGISTRY: ★ features live in headwaters/features/<name>/ and register via an
internal registry (FeatureSpec{id, star, interfaces, default_enabled}); runtime flags in
rules.yaml `features:` (all on during beta). Enforce "core never imports
headwaters.features.*" with an import-linter contract in CI. Keep versioned plugin
interfaces + contract tests so ★ modules can later move to a private entry-point
package with minimal changes.
★ MARKERS: `headwaters features` CLI and dashboard list each feature with core/★ status
and the legend "★ = free during beta; may become part of Headwaters Pro at 1.0".
OPT-IN USAGE REPORT: off by default; preview payload before enabling; weekly aggregate
counts only (version, OS, GPU class, scope/engine counts, ★ enabled + 14-day retention,
verify catches, escalations, failovers, savings bucket); never prompts, outputs, code,
paths, keys. Include "Send report now" one-shot. Unit test that the payload schema
contains no free-text fields.
FEEDBACK: `headwaters feedback` (prefilled form, optional redacted doctor bundle), survey
prompt at 7 and 30 active days, max twice, respect dismissal.
SPILLBENCH: add a with/without comparison for context engine + verification v2 on
reference GPU profiles (decision evidence for 1.0).
ROADMAP: Beta 0.1 = core + MCP + gateway + basic verify + 4-host installer + doctor;
then 0.2 / 0.3 / 0.4 per Addendum v3.5 §G. Stop after each release for review.
```
