# Headwaters: Phased Ticket Backlog

> **Rename note:** The product formerly called "Spillway" is now **Headwaters**. Every name has been updated: product name, package (`headwaters`, `headwaters/features/…`), CLI (`headwaters`, `headwaters-mcp`), picker models (`headwaters/auto|free|local|cloud`), MCP tools (`headwaters_do`, …) and prompts (`/headwaters-local`, …), config files (`.headwaters.yaml`, `.headwatersignore`), headers (`x-headwaters-*`), env vars (`HEADWATERS_HOME`), resource URIs (`headwaters://`), app folders (`%APPDATA%\Headwaters`), and the benchmark (HeadwatersBench). Ticket IDs keep the `SPW-` prefix so existing references and ticket packets still match. Ordinary uses of the word "spill" (e.g. VRAM spilling into system RAM) are unchanged.


This backlog turns the whole design into tickets: v3, plus addenda v3.1–v3.5. Where documents conflict, the later one wins: v3.5 > v3.4 > v3.3 > v3.2 > v3.1 > v3.

**How to use it**
1. Start each Sonnet session by pasting the **Context Pack** (§1), then paste **one ticket**.
2. Work in ticket order within a phase, respecting the `Depends` field. Tickets in the same phase without a dependency between them can run in any order.
3. Run each ticket on its own branch and merge it via PR. At the end of each phase, run the **Opus review-gate ticket** before starting the next phase.
4. Model and effort per ticket follow the earlier advice:
   - Sonnet Medium: routine work
   - Sonnet High: core logic and integrations
   - Opus High: planning, verification spikes, privacy/security reviews, phase gates
5. If a ticket feels too big for one session, ask Sonnet to propose a split before it writes code.

---

## 1. Context Pack (paste at the top of every Sonnet session)

```
You are implementing one ticket of HEADWATERS, a Windows-first, local-first LLM router
sold on itch.io (free beta now; possible Lite/Pro split at 1.0).

PRODUCT: Keeps developers coding when paid credits/rate limits run out. Routes each
request to the best allowed model: user's own paid keys → free cloud (OpenRouter :free,
OpenCode free) → local Ollama on the user's RTX GPU (12 GB laptop / 16 GB desktop).
Three engines over ONE core with shared SQLite state:
  - Gateway: OpenAI /v1/chat/completions + Anthropic /v1/messages on 127.0.0.1; picker
    models headwaters/auto, headwaters/free, headwaters/local, headwaters/cloud.
  - MCP server: 8 front-door tools (headwaters_do, _ask, _check, _think, _status, _scope,
    _handoff, _more) via thin stdio shims → one shared local daemon.
  - Hooks (later): pre-prompt hooks in hosts that support them.

ROUTING MODEL: scope × strategy × locks.
  scope    = auto | free | local | cloud (which tiers are allowed)
  strategy = cost_saver | quality_first | primary_first (ranking inside scope)
  locks    = Private lock > project rule (.headwaters.yaml, tighten-only) > secret
             detection > requested scope > default scope. Tighter always wins.
  LOCAL SCOPE IS FAIL-CLOSED: no cloud client constructed, loopback-only assertion,
  every sub-step local, structured error instead of cloud fallback.

NON-NEGOTIABLES
- Python 3.11+, uv, pinned deps (uv.lock). Package `headwaters`. Windows 10/11 x64 first,
  Linux second, macOS best-effort.
- Core never imports headwaters.features.* (★ features). Enforced by import-linter.
- Never hard-code model slugs, limits, prices, error regexes, placeholder patterns:
  they live in catalog/seed_catalog.json (curated, signed, updatable) or rules.yaml.
- Local LLM calls: Ollama NATIVE API only (/api/chat, /api/tags, /api/show, /api/ps).
  Cloud calls: litellm SDK.
- Bind servers to 127.0.0.1 only. Paths via platformdirs (%APPDATA%\Headwaters,
  %LOCALAPPDATA%\Headwaters). Secrets via keyring (Windows Credential Manager).
- MCP stdio: NOTHING but JSON-RPC on stdout; logs to stderr/file; UTF-8 stdio.
- No telemetry by default. Never log prompts/outputs/keys (decision metadata only).
- Never proxy/reuse Copilot or other subscription credentials; never rotate accounts
  or keys to evade rate limits; bring-your-own-keys only.
- Clean room: do NOT read or copy competitor source code (e.g. llm-router). Use only
  official docs/specs. Record idea sources in docs/PROVENANCE.md.
- Verify any external API against current official docs before coding it; record in
  docs/ASSUMPTIONS.md; isolate behind an interface.
- Tests: pytest; mock all HTTP in unit tests; `-m live` for real Ollama. Core coverage
  ≥ 85%. Tests must pass on windows-latest and ubuntu-latest CI.

REPO LAYOUT (create only what your ticket needs)
headwaters/core/        rules, state, paths, logging, features (registry), plugins
                      (Protocols), catalog/, analyzer/, hardware/, walls, quotas, scope,
                      secrets_detect, policy/, executor/, verify_basic, router, sessions
headwaters/mcp/         daemon, api, shim, server, tools/, prompts, jobs, resources
headwaters/gateway/     app, openai_routes, anthropic_routes, streaming, aliases
headwaters/control/     status_page, doctor, keys
headwaters/installer/   hosts/, templates/
headwaters/cli/         main (Typer)
headwaters/features/    ★ modules only (ledger, dashboard, tray, wizard, paid_scopes,
                      context_engine, knn, verify_v2, worktree, handoff, hooks,
                      usage_report, fusion, duck, image, learning)
catalog/  integrations/  vscode-extension/  packaging/  bench/  tests/  docs/

WORKFLOW FOR THIS TICKET
1. Restate the goal and list files you will create/change. Ask if anything is unclear.
2. Implement with tests. Keep the scope to this ticket; note follow-ups instead.
3. Run ruff, mypy, pytest. Fix failures.
4. Report: files changed, tests added/passing, assumptions (also added to
   docs/ASSUMPTIONS.md), deviations, and follow-up tickets you recommend.
```

---

## 2. Phase map and critical path

| Phase | Release | Tickets | Goal |
| --- | --- | --- | --- |
| 0 Foundation | – | SPW-001 → 004 | Repo, CI, API verification, plumbing |
| 1 Core | Beta 0.1 (part) | SPW-101 → 119 | Routing brain, walls, scopes, executor, basic verify |
| 2 MCP engine | Beta 0.1 (part) | SPW-201 → 208 | Daemon, shims, 8 front-door tools |
| 3 Gateway | Beta 0.1 (part) | SPW-301 → 306 | OpenAI + Anthropic endpoints, failover, egress proof |
| 4 Ship 0.1 | **Beta 0.1** | SPW-401 → 407 | Keys, doctor, installer for 4 hosts, Windows package |
| 5 Polish | **Beta 0.2** | SPW-501 → 509 | Ledger, paid scopes, dashboard, tray, wizard, VS Code extension, opt-in report |
| 6 Quality | **Beta 0.3** | SPW-601 → 608 | Context engine, kNN, verify v2, worktree, escalation, handoff |
| 7 Breadth + proof | **Beta 0.4** | SPW-701 → 706 | Hooks, more hosts, HeadwatersBench, host eval suite |
| 8 RC → 1.0 | **1.0** | SPW-801 → 803 | Hardening, decision evidence, apply decision |
| 9 Extras | 1.x | SPW-901 → 905 | Fusion, duck, image, learning, sampling (SPW-905 DEFERRED, D9) |

**Critical path to Beta 0.1:** 001 → 002 → 003/004 → 101 → 102 → 103 → 105 → 106 → 108 → 109 → 110 → 112 → 113 → 114 → 115 → 117 → 201 → 204 → 301 → 302 → 403 → 405 → 407.

**Scope-risk valve (v3.5 §G):** if 0.1 slips, ship Phases 1, 2 and 4 to closed-alpha testers without the gateway, then finish Phase 3 before the open beta.

### Open decisions flagged while ticketing: settled
All three were settled at the HDW-119 review gate (2026-10-31); see `docs/DECISIONS.md`:
1. Escalation in core: [D10](../docs/DECISIONS.md#d10-detail-escalation-in-core)
2. Sticky sessions in core: [D11](../docs/DECISIONS.md#d11-detail-sticky-sessions-in-core)
3. `headwaters_think` in 0.1: [D12](../docs/DECISIONS.md#d12-detail-headwaters_think-in-01)

---

## Phase 0: Foundation

### SPW-001 · Repo scaffold, tooling, CI
**Model** Sonnet · **Effort** Medium · **Depends** – · **Class** Core · **Refs** v3.5 §F, v3.4 §J
**Goal:** Working empty project that every later ticket builds on.
**Build:**
- `uv` project `headwaters` (Python 3.11+), with `ruff`, `mypy` (strict on `headwaters/core`), `pytest`, `pytest-cov`, `pre-commit`.
- Directory skeleton from the Context Pack, with `__init__.py` files.
- `import-linter` contract: `headwaters.core` must not import `headwaters.features`, `headwaters.gateway`, `headwaters.mcp`, `headwaters.control` or `headwaters.installer`.
- GitHub Actions: lint + type-check + tests on `windows-latest` and `ubuntu-latest`; coverage report.
- Docs stubs: `docs/ASSUMPTIONS.md`, `docs/PROVENANCE.md`, `docs/DECISIONS.md` (seed with D1–D6; `docs/DECISIONS.md` now holds D1–D13), `THIRD_PARTY_NOTICES.md`, `LICENSE` placeholder ("All rights reserved" for now).
- `pytest` markers: `live`, `windows`, `slow`.
**Accept when:** CI is green on both OSes; the import-linter contract fails a deliberate violation test; `uv run headwaters --help` prints a stub.
**Not in this ticket:** any feature code.

### SPW-002 · External API verification spike
**Model** **Opus** · **Effort** High · **Depends** 001 · **Class** – · **Refs** v3 §14, v3.2 §A, v3.4 §B3
**Goal:** Replace assumptions with verified facts before building integrations. Includes the gateway build decision.
**Verify from official docs (record version and date for each in `docs/ASSUMPTIONS.md`):**
- **LiteLLM:**
  - Can a custom hook or router rewrite the model before the call?
  - Can it observe failures?
  - Can it retry on another deployment *before the first streamed token*?
  - Anthropic `/v1/messages` support.
  - Decide: **LiteLLM proxy + hooks** or **thin FastAPI gateway using the litellm SDK**. Write the decision as `docs/DECISIONS.md` D7.
- **MCP:** current spec version and Python SDK version. Check structured output / `outputSchema`, tool annotations, progress, Tasks (async), elicitation, resources (+ subscriptions), prompts, roots, sampling, streamable HTTP, and `tools/list_changed`. Build a per-host support table for VS Code, Cline, OpenCode and Claude Desktop.
- **Ollama:**
  - `/api/show` `model_info` keys for layers, kv_heads, head_dim
  - `/api/ps` fields (`size`, `size_vram`)
  - `keep_alive`, `options.num_gpu`, `format` JSON schema
  - minimum supported version
- **OpenRouter:**
  - `/api/v1/models` fields and `:free` convention
  - `/api/v1/key` fields
  - `openrouter/auto`, `openrouter/free`
  - Fusion slug and presets
  - reasoning parameter
  - `:floor` and quantization filter
  - `/api/v1/images` and image-model discovery
  - attribution headers
- **VS Code:** BYOK custom OpenAI-compatible endpoint config; `chat.agentHost.byokModels.enabled`; `LanguageModelChatProvider` API status (stable or proposed); org policy that blocks BYOK.
- **OpenCode:** custom provider `baseURL` config location on Windows; how free models are listed.
- **Windows:** NVML display-active query; the command for listing excluded port ranges; whether Task Scheduler can be configured without admin rights.
**Accept when:** ASSUMPTIONS.md has a dated entry per item with a source link and a confidence level; D7 is recorded; every item that remains unverifiable is listed as a risk.
**Not in this ticket:** production code (small throwaway probes are fine).

### SPW-003 · Paths, logging, stdio safety
**Model** Sonnet · **Effort** Medium · **Depends** 001 · **Class** Core · **Refs** v3.1 §C4–C5, v3.3 §D4
**Build:**
- `core/paths.py`: `platformdirs` config/state/cache/log dirs. Supports long paths and paths with spaces. Override via the `HEADWATERS_HOME` environment variable (for tests).
- `core/logging.py`: structured logging to stderr and a rotating file. A redaction filter covers API keys and token-like strings, and drops any `prompt`, `content` or `messages` fields. Debug flag.
- `core/stdio.py`: force UTF-8 on stdio. A `guard_stdout()` context manager that raises in tests if anything writes non-protocol bytes to stdout.
**Accept when:** tests prove redaction works and the stdout guard catches stray `print`; the paths resolve correctly on Windows (CI).

### SPW-004 · Feature registry and plugin interfaces
**Model** Sonnet · **Effort** Medium · **Depends** 001 · **Class** Core · **Refs** v3.5 §B, §F
**Build:**
- `core/features.py`:
  - `FeatureSpec{id, star: bool, title, interfaces, default_enabled}`
  - registry with `register()`, `enabled(id)`
  - runtime flags read from rules (`features:` map); during the beta every ★ feature is on by default
- `core/plugins.py`: versioned `Protocol`s (`api_version` attribute) for `SurfacePlugin`, `VerifierPlugin`, `ContextPlugin`, `BridgePlugin`, `DashboardPlugin`, `LedgerPlugin`, `ClassifierLayerPlugin`, `EscalationPlugin`. Discovery runs through an internal registry now; Python entry points (`headwaters.plugins`) are supported but optional.
- Contract-test harness `tests/contracts/` that any plugin implementation can be run against.
- The ★ legend constant: "★ = free during beta; may become part of Headwaters Pro at 1.0".
**Accept when:** a dummy ★ plugin under `headwaters/features/_example` registers and is found without core importing it; contract tests run against it.

---

## Phase 1: Core

### SPW-101 · Rules config (`rules.yaml`) and project config (`.headwaters.yaml`)
**Model** Sonnet · **Effort** Medium · **Depends** 003, 004 · **Class** Core · **Refs** v3 §8, v3.3 §E, v3.1 §C1
**Build:** a `pydantic` model covering these fields:
- `version`
- `default_scope` (auto|free|local|cloud)
- `strategy`
- `private_lock`
- `airplane`
- `secrets.action` (local|refuse|off)
- `verify` (always|agentic|off)
- `picker.show_extras`
- `budget` (`paid_daily_usd`, `premium_max_usd_per_call`, `confirm_above_usd`)
- `tiers` (paid models, `use_floor_variant`, `openrouter_auto`, premium fusion, free_cloud ranking=catalog, `opencode_free`, `openrouter_free_router{enabled,max_complexity}`, local {host, profile, reserve_gb (null=auto), kv_cache_type, keep_alive, keep_warm_model, classifier{model,num_gpu,timeout_s}, on_battery})
- `floors` (per task base/per_complexity, agentic_bonus 15, high_risk_bonus 10, optimistic_margin 8)
- `walls` (`cascade_if_wait_over_s` 8, `soft_exhaust_pct` 90)
- `gateway` (port 4000), `dashboard` (port 4001), `mcp` (async_threshold_s 20, roots auto)
- `attribution`
- `telemetry` false
- `catalog` (auto_update, url)
- `features` map
- `hosts.subscription`
- `advice` (off|inline|header_only)

Also:
- Export JSON Schema to `catalog/rules.schema.json`.
- Hot reload through a file watcher (debounced) with a validation-error fallback to the last good config.
- Write defaults on first run.
- `.headwaters.yaml` loader: `privacy` (local|free|any, tighten-only) and `checks` (list of commands). Discovered by walking up from the workspace root.
**Accept when:** invalid configs give clear errors; hot reload is tested; the schema file is generated in CI and diffed against the committed copy.

### SPW-102 · SQLite state layer
**Model** Sonnet · **Effort** Medium · **Depends** 003 · **Class** Core · **Refs** v3 §3
**Build:**
- `core/state.py`: SQLite with WAL, `busy_timeout`, short transactions, and a migration table.
- Tables: `models`, `scores`, `walls`, `quotas`, `sessions`, `decisions` (add `scope`, `strategy`, `locks_applied`), `outcomes` (tokens, cost_usd, counterfactual_usd nullable, verify_result, escalated_to, user_feedback), `jobs`, `handoffs`, `kv` (misc settings).
- Typed repository functions. No prompt or output text is stored, only metadata and token counts.
**Accept when:** a multi-process test (two processes writing concurrently) passes on Windows; migrations are idempotent.

### SPW-103 · Catalog schema, seed catalog, loader
**Model** Sonnet · **Effort** High · **Depends** 101 · **Class** Core · **Refs** v3 §4.2, §7.4, v3.4 §C
**Build:** `catalog/catalog.schema.json` and a typed loader, with these sections:
- `models[]`:
  - `id`, aliases, tier hints, provider, modalities, tools_ok, json_ok, ctx
  - prices + `price_date`
  - size_gb, `arch{n_layers,n_kv_heads,head_dim}`
  - `scores{code,reasoning,chat,summarize,extract,creative,vision,agentic_tools}`
  - `expected_tps` per GPU class
  - `deprecated_by`
- `free_limits` (per provider/model: rpm, rpd)
- `wall_patterns` (per provider: status codes, body regex → kind, scope)
- `placeholder_patterns`
- `gpu_profiles` (laptop_12gb, desktop_16gb: recommended models by role)
- `secret_patterns` + `secret_placeholders`
- `subscription_rates` (dated, for credits-avoided estimates)
- `prompt_bank_ref` (for kNN, later)

Also:
- A seed catalog containing **placeholder example entries** clearly marked `"verify": true`. Real values are filled in by the owner.
- Defaults for unknown models: conservative scores, `unscored: true`.
**Accept when:** the seed validates against the schema; the loader rejects bad catalogs with a clear error; the unknown-model path is tested.

### SPW-104 · Catalog updater (signed, ETag, offline)
**Model** Sonnet · **Effort** Medium · **Depends** 103 · **Class** Core · **Refs** v3 §4.2, v3.3 §D5
**Build:**
- Fetch from the configured URL with ETag and verify an **ed25519 signature** (the public key is embedded in the app).
- Atomic swap into the cache; keep the last good copy; fall back to the bundled seed.
- No prompt data in requests. Disabled when `airplane: true` or in local-only lock state, if configured.
- `headwaters catalog update|status`.
- `scripts/sign_catalog.py` for the owner.
**Accept when:** tampered catalogs are rejected; a 304 response leaves the cache unchanged; offline startup uses the cache; airplane mode makes zero network calls (tested with a socket mock).

### SPW-105 · Live discovery and catalog merge
**Model** Sonnet · **Effort** High · **Depends** 102, 103 · **Class** Core · **Refs** v3 §4.2
**Build:**
- Ollama: `/api/tags`, `/api/show` (capabilities, `model_info` arch params, context), `/api/ps` (warm, size, size_vram).
- OpenRouter: `/api/v1/models` (modalities, supported_parameters → tools/json, context, pricing, `:free` → tier free_cloud) and `/api/v1/key`.
- OpenCode free models: read from the OpenCode config (location per SPW-002) or a user list in rules.
- User OpenAI-compatible free endpoints from rules.
- Merge order: live → curated catalog (by id/alias) → user overrides. Produces `ModelRecord`s persisted to `models` and `scores`.
- Refresh on startup, on `headwaters models refresh`, and on a TTL.
**Accept when:** mocked-HTTP tests cover each source, alias matching, deprecations and unknown-model defaults; an unreachable source degrades gracefully.

### SPW-106 · Analyzer layer 1 (deterministic requirements)
**Model** Sonnet · **Effort** Medium · **Depends** 101 · **Class** Core · **Refs** v3 §4.1
**Build:** `Requirements{hard:{vision,tools,json,min_ctx}, soft:{task,complexity,risk,agentic,languages,reasoning_hint,duck_hint}, confidence, source}`, computed in under 5 ms. The rules:
- Image parts mean vision.
- A tools array means tools.
- `response_format` / a schema means json.
- Token estimate: `tiktoken`-style if available; otherwise chars/3.5 for code and chars/4 for prose.
- Languages from code fences and file extensions.
- Agentic = tools present, OR system prompt > N tokens, OR a known agent user-agent.
- Keyword hints come from rules/catalog, never code.
- Ambiguous results get `confidence < threshold`.
**Accept when:** at least 30 fixture prompts (code, vision, agentic Cline-style system prompt, JSON extraction, chat, duck) produce the expected requirements.

### SPW-107 · Analyzer layer 3 (tiny CPU classifier) and layer interface
**Model** Sonnet · **Effort** Medium · **Depends** 106, 115 · **Class** Core · **Refs** v3.4 §C
**Build:**
- `ClassifierLayerPlugin` chain: layer 1 → (optional layer 2 slot for kNN) → layer 3.
- Layer 3: Ollama native call to a small model with `options.num_gpu=0` (CPU only), JSON schema output `{task, complexity 1–5, risk}`, 2 s timeout, sha256 cache.
- Falls back to layer 1 on timeout or error, and runs only when confidence is below the threshold.
- In local scope it runs locally; it never sends anything to the cloud.
**Accept when:** tests cover the timeout fallback, cache hits and the no-GPU option.

### SPW-108 · Hardware A: GPU, display, battery, GPU-busy detection
**Model** Sonnet · **Effort** Medium · **Depends** 003 · **Class** Core · **Refs** v3.1 §C1, v3 §7
**Build:**
- GPU detection: NVML (`nvidia-ml-py`) → `nvidia-smi` → manual config. Collects name, total and free VRAM, and display-active flag.
- Reserve: 2.0–2.5 GB if the GPU drives a display, about 1.0 GB if not, the higher value if unknown. A user override wins.
- Profile auto-select (laptop_12gb, desktop_16gb, custom).
- Battery via `psutil.sensors_battery()`.
- GPU-busy: another process holding more than X% of VRAM.
- Exposes a `HardwareState` snapshot, cached for a few seconds.
**Accept when:** mocked NVML and nvidia-smi outputs produce correct states; a machine without an NVIDIA GPU degrades gracefully to "cloud only suggested".

### SPW-109 · Hardware B: KV math, fit, `num_ctx`, sysmem-fallback detection
**Model** Sonnet · **Effort** High · **Depends** 105, 108 · **Class** Core · **Refs** v3 §7.1–7.3, v3.1 §C1
**Build:**
- `budget = VRAM_total − reserve`
- `kv_bytes = 2·n_layers·n_kv_heads·head_dim·ctx·bytes(kv_type)` with bytes per element f16=2, q8_0≈1, q4_0≈0.5
- `fits = weights + kv + overhead(0.5–1 GB) ≤ budget`
- `num_ctx` = largest of {8k,16k,24k,32k,48k,64k} that fits and is ≥ 1.2×est_tokens; otherwise `offload` (latency penalty) or `no_fit` (excluded)
- Calibrate weights against `/api/ps` actual sizes on first load
- Warm bonus and eviction penalty inputs
- **Sysmem fallback:** compare measured tokens/s against the catalog's `expected_tps`, together with `size_vram < size`. If detected, mark the model `offload` and expose a hint for `doctor`.
**Accept when:** the worked example passes — Qwen2.5-Coder-14B-class (48 layers, 8 KV heads, head_dim 128, ~9 GB) fits ~12–14k ctx at q8 on a 10.5 GB budget, and 32k on 14.5 GB. Sysmem detection is unit-tested.

### SPW-110 · Wall classifier
**Model** Sonnet · **Effort** High · **Depends** 103 · **Class** Core · **Refs** v3 §4.4
**Build:** `classify(status, headers, body, provider) → WallEvent{kind: rate_limit|exhausted|overloaded|context|other, retry_after, reset_at, scope: model|provider|key}`.
- Patterns come from the catalog.
- Header parsing: `Retry-After` (seconds or date), `x-ratelimit-reset*` variants.
- Exhausted = 402, `insufficient_quota`, "credit balance is too low", OpenRouter insufficient credits, Gemini `RESOURCE_EXHAUSTED` with a daily quota.
- Overloaded = 503 / 529 / `overloaded_error`.
- Context = context-length errors.
**Accept when:** fixture tests with realistically shaped bodies for OpenAI, Anthropic, Gemini, OpenRouter, Ollama and OpenCode pass, including scope (e.g. OpenRouter credits → provider scope).

### SPW-111 · Quota tracker and cooldowns
**Model** Sonnet · **Effort** Medium · **Depends** 102, 110 · **Class** Core · **Refs** v3 §4.4
**Build:**
- Per-scope state ok|cooldown|exhausted, persisted.
- Exponential backoff with full jitter on repeated 429s; immediate cascade if the wait exceeds `cascade_if_wait_over_s`.
- Exhausted = no retry until a balance check passes or the reset time.
- Overloaded = 1–2 short retries, then cascade.
- rpm/rpd counters per free model from catalog limits; soft-exhausted at `soft_exhaust_pct`.
- OpenRouter `/api/v1/key` polling every N minutes and after paid calls, skipped in airplane mode or local lock.
- `availability(model) → ok|cooldown(eta)|exhausted|soft`.
**Accept when:** time-controlled tests (freezegun) cover each transition.

### SPW-112 · Scope resolver, locks, secret detection
**Model** Sonnet · **Effort** High · **Depends** 101, 103 · **Class** Core · **Refs** v3.3 §A–E
**Build:**
- `effective_scope = intersect(private_lock?local, project_rule, secret?local, requested, default)`
- Scope → allowed tiers:
  - auto = paid + openrouter/auto + free_cloud + local
  - free = free_cloud + local
  - local = Ollama loopback only
  - cloud = paid + openrouter/auto + free_cloud
- Without the ★ paid-scopes feature, `auto` and `cloud` exclude paid tiers and say so in the reasons.
- Secret detection uses catalog patterns, ignores placeholders, and applies `secrets.action`.
- Empty or unsatisfiable scope → `ScopeError{code, message, fix_hint}` (e.g. "pull qwen2.5vl:7b", "disable Private lock in tray").
- Suffix parsing: `headwaters/free+verify`, `+quality`.
**Accept when:** a golden precedence table (≥ 25 rows) passes; the secret fuzz test passes; project rules can only tighten.

### SPW-113 · Policy engine A: filters, floors, scores
**Model** Sonnet · **Effort** High · **Depends** 105, 109, 111, 112 · **Class** Core · **Refs** v3 §4.5
**Build:**
- Hard filter: requirements (vision, tools, json, ctx ≥ 1.2×tokens) ∩ availability ∩ hardware fit ∩ effective scope.
- `floor = base[task] + k[task]·complexity + 10·high_risk + 15·agentic`; agentic also needs `agentic_tools ≥ floor`.
- `score = quality[task] − complexity_penalty + warm_bonus − latency_penalty − evict_penalty − cost_weight·cost`.
- Battery and GPU-busy add a local latency penalty per `local.on_battery`.
- Strategies adjust the weights (cost_saver, quality_first, primary_first).
**Accept when:** unit tests cover every term; all weights come from rules.

### SPW-114 · Policy engine B: decision ladder and golden tests
**Model** Sonnet · **Effort** High (Opus reviews at SPW-119) · **Depends** 113 · **Class** Core · **Refs** v3 §4.5, v3.3 §B4
**Build:** `decide(req, ctx) → RouteDecision{model, tier, score, floor, below_floor, scope, reasons[], fallbacks[], suggestions[]}`. Ladder inside the effective scope:
1. pin / force overrides
2. hard filter
3. cheap rung: best of {local, free_cloud} ≥ floor (local wins ties)
4. optimistic rung (within margin δ AND verifiable) **only if an `EscalationPlugin` is registered** (D10 adds a core verified retry for mandatory checks, HDW-129; the optimistic rung itself stays ★)
5. paid primary → `openrouter/auto` (only when paid tiers are allowed)
6. if paid is walled → best-effort cheap with `below_floor=true` → `openrouter/free` only if enabled and complexity ≤ 2
7. `NoRouteError` with reset ETAs and suggestions

Also:
- In local scope, escalation stays local (larger local model / larger ctx) and then errors.
- On battery or GPU-busy, *suggest* `headwaters/cloud`; never auto-leave local.
- Premium (Fusion) is never chosen automatically.
**Accept when:** a golden table of ≥ 40 scenarios passes, covering vision, agentic, every scope, private lock, each wall combination, both GPU profiles, battery and unscored models.

### SPW-115 · Executor (cloud + local, streaming, reasoning capture)
**Model** Sonnet · **Effort** High · **Depends** 101, 105, 109 · **Class** Core · **Refs** v3 §4.8, v3.3 §D1
**Build:**
- `Executor.run(decision, messages, tools, stream)`.
- Cloud via the litellm SDK with attribution headers. Local via Ollama native `/api/chat` with computed `num_ctx`, `keep_alive`, `format` schema.
- Longer first-token timeout when the model is cold.
- Captures `reasoning` / `thinking` separately from content.
- The streaming interface exposes a `first_token` event, so callers can fail over only before it.
- **Local scope guard:** cloud clients are never constructed; assert the host resolves to loopback (127.0.0.1 / ::1 / localhost), otherwise raise.
- Errors are mapped through the wall classifier.
**Accept when:** mocked-provider tests cover streaming, reasoning capture, cold-timeout behaviour and the loopback assertion (a non-loopback Ollama host raises in local scope).

### SPW-116 · Basic verification (core)
**Model** Sonnet · **Effort** Medium · **Depends** 004, 103 · **Class** Core · **Refs** v3 §4.6, v3.1 §C4
**Build:** a `VerifierPlugin` implementation `BasicVerifier`, returning `VerifyReport{passed, checks[], reasons[] (machine-readable)}`. Checks:
- tree-sitter parse for Python, JS/TS, Go, Rust, Java, C#, C/C++, JSON, YAML (plus `ast.parse` for Python)
- placeholder/laziness scan (catalog patterns)
- truncated-fence detection
- **CRLF/LF normalization**, respecting `.gitattributes` and `core.autocrlf`
- `git apply --check` for diffs
- JSON Schema validation
- tool-call argument validation
**Accept when:** fixtures for each check pass or fail as expected; the CRLF diff case passes on Windows.

### SPW-117 · Router façade (shared core API)
**Model** Sonnet · **Effort** High · **Depends** 107, 114, 115, 116 · **Class** Core · **Refs** v3 §2, v3.4 §A
**Build:** `Router.handle(request: RouteRequest) → RouteResult | AsyncIterator` used by every engine:
1. analyze
2. resolve scope
3. decide
4. execute
5. classify errors → cascade to the next fallback (bounded attempts, before first token for streams)
6. run registered verifiers per the `verify` setting
7. call `EscalationPlugin` if present
8. log decision + outcome (metadata only)

Also:
- Session keying: `x-session-id`, else hash(system prompt + first user msg). The warm-model preference is passed to the policy.
- Exposes a progress callback hook.
**Accept when:** end-to-end tests with mocked providers pass, covering: free 429 → local; paid 402 → free; local scope never touches the cloud; verify failure reported without a plugin; decision rows written.

### SPW-118 · CLI basics
**Model** Sonnet · **Effort** Medium · **Depends** 117 · **Class** Core · **Refs** v3 §5.4, v3.3 §C, v3.5 §B
**Build:** Typer commands:
- `headwaters version|pick "<prompt>" [--image] [--tools] [--tokens] [--scope]`
- `status` (scope, locks, walls with ETAs, warm model)
- `models list|refresh` (fit/ctx per model)
- `scope <auto|free|local|cloud>`
- `private on|off`
- `features` (core/★ list + legend)
- `catalog update|status`

Output uses Rich tables; `--json` for scripts.
**Accept when:** CLI tests pass via `CliRunner`; `pick` shows the reasons and fallbacks.

### SPW-119 · Phase 1 review gate
**Model** **Opus** · **Effort** High · **Depends** 101–118
**Do:**
- Review core against the design. Policy ladder correctness, local-scope fail-closed behaviour, and the absence of hard-coded slugs or limits are the priorities.
- Run coverage. Check that the import-linter contract holds.
- Settle the 3 open decisions in §2.
- Produce a punch list of fixes as new tickets.

---

## Phase 1.5: HDW-119 review fixes

Tickets HDW-120 to HDW-141 come from the HDW-119 review (`review_bundle_HDW-119/HDW-119_REVIEW.md`, section 8). The backlog numbers tickets SPW-xxx and the packets HDW-xxx; **HDW = SPW numbering**, so HDW-120 is SPW-120 and every Depends/Refs below uses the same numbers. Decisions D10 to D13 are in `docs/DECISIONS.md`. **Blocks Phase 2** = merge before SPW-201; "Before HDW-xxx" = merge before that ticket; **Can wait** = any time before the Beta 0.1 gate (SPW-407). Ticket text is verbatim from the review, except HDW-124, which also carries owner answer 6. HDW-142 and later come from the triage session (`docs/FOLLOWUPS.md`) and are new text.

### HDW-120 · Secret scan covers every message part · **Blocks Phase 2**
**Model** Sonnet · **Effort** Medium · **Depends** 112, 117 · **Class** Core · **Refs** v3.3 §D8, F1
**Build:**
- `router_prep.scan_texts` walks every string leaf of every message: `content` parts, OpenAI `tool_calls[].function.arguments`, Anthropic `tool_use.input`, nested `tool_result` content, and system blocks. Reuse `_extract.collect`, extended to keep the texts.
- A socket-spy fixture (shared with the catalog updater tests) asserts no non-loopback connect during `Router.run` in the secret-locked state.
**Accept when:** P1's four shapes are all detected; a secret only in a tool call routes local; the socket spy records no remote connect; no secret text appears in reasons or rows.

### HDW-121 · Ollama cloud-proxied models are never local · **Blocks Phase 2**
**Model** **Opus** · **Effort** High · **Depends** 105, 115 · **Class** Core · **Refs** v3.3 §D1-D2, A-OLL-8, F2
**Build:**
- Verify against current Ollama docs and a live probe how cloud models appear in `/api/tags` and `/api/show` (remote fields, `:cloud`/`-cloud` tags). Record it as A-OLL-9.
- Discovery marks them `retains_data=true`, `tier=free_cloud`/`paid` per catalog, or drops them; never `local`.
- `enforce_scope` and `OllamaBackend` refuse a local call to a model flagged remote (defence in depth); the layer-3 classifier refuses one too.
- Catalog: `ollama_remote_patterns` (data, not code).
- `doctor` hint (HDW-402): recommend `OLLAMA_NO_CLOUD=1` when Private Mode is used.
**Accept when:** a fixture `/api/tags` with a cloud model never yields a local candidate; Private-lock routing to it raises `LocalScopeViolation`; the assumption is recorded with a source and date.

### HDW-122 · Credit walls hit paid models only, and expire · **Blocks Phase 2**
**Model** Sonnet · **Effort** High · **Depends** 110, 111, 117 · **Class** Core · **Refs** v3 §4.4, F3, F22
**Build:**
- Credit exhaustion (`exhausted` from a 402 or credit body on a paid model) records a subject that only paid models of that provider/key check (e.g. `provider:openrouter:paid`). Free-tier exhaustion (`free-models-per-day`) records a free subject.
- An exhausted wall without a reset gets `until = now + walls.exhausted_recheck_s` (rules, default e.g. 900).
- The router passes the key alias when the executor knows it; `count_request` also runs on 429.
**Accept when:** after a paid OpenRouter 402, a `:free` OpenRouter model is still `ok`; the wall lifts after the recheck time; P9 is a regression test; the quota tests still pass.

### HDW-123 · Local scope serves the best local model below the floor · **Blocks Phase 2**
**Model** Sonnet · **Effort** Medium · **Depends** 114 · **Class** Core · **Refs** v3 §4.5 step 4, v3.3 §D3, D10, F4
**Build:**
- In `build_ladder`, local-only and battery `local_only` states append the below-floor *local* entries (`best_effort`, `verify_required`) after the local entries that meet the floor; `local_below_floor` is raised only when no local model passes the hard filter. The error keeps its suggestions, now attached as `RouteDecision.suggestions` (`pull_model`, `split_task`).
- Remove the analyzer's `+int(agentic)` complexity step (the floor already carries `agentic_bonus`); update the HDW-106 fixtures.
- Golden rows: `agentic-local-scope` (served, best_effort, verify), `scope-local-too-hard` (served below floor), no cloud fallback in any row.
**Accept when:** P2 gives a local decision in local scope with `below_floor=True, verify_required=True`; no golden row leaves the local tier in local scope; CLI `pick --scope local` shows the warning.

### HDW-124 · Complete the import contract · **Blocks Phase 2**
**Model** Sonnet · **Effort** Medium · **Depends** 001 · **Class** Core · **Refs** D7, D8, v3.5 §F, F5
**Build:**
- Add `headwaters.cli` to the core contract's forbidden modules.
- New contracts with `include_external_packages = True`: only `headwaters.core.executor.cloud` may import `litellm`; only `headwaters.mcp` may import `mcp`; only `headwaters.gateway` may import `fastapi`/`starlette` (in place before Phase 2 code exists).
- A source test that fails on any `litellm` reference outside `executor/cloud.py` and `cloud_map.py` (it catches `importlib` strings the linter can't see).
- A test that importing `headwaters.core.router` and `headwaters.cli.main` leaves `litellm` out of `sys.modules`.
- A CI check that litellm's telemetry and cost-map fetch stay off after every litellm version bump (owner decision 2026-10-31, HDW-119 question 6).
**Accept when:** `lint-imports` shows every contract kept; deliberate violations for each new contract fail; the litellm source test passes.

### HDW-125 · Make subprocess decoding independent of the code page · **Blocks Phase 2**
**Model** Sonnet · **Effort** Low · **Depends** 001, 108 · **Class** Core · **Refs** gap 11, F6
**Build:** `encoding="utf-8", errors="replace"` (plus `PYTHONIOENCODING=utf-8` in the child env) in `tests/test_import_contract.py`, `hardware/gpu.py:run_nvidia_smi`, and any other `text=True` call (search the repo).
**Accept when:** the full suite passes 3 times in a row in a cp1252 console on Windows; an `nvidia-smi` fixture with a non-ASCII process name parses.

### HDW-126 · Production never registers `features/_example` · **Blocks Phase 2**
**Model** Sonnet · **Effort** Low · **Depends** 004 · **Class** Core · **Refs** v3.5 §F, F7
**Build:** move `_example` to `tests/fixtures/features/_example` (or make `discover_features` skip `_`-prefixed packages unless `include_private=True`); the contract tests discover it explicitly.
**Accept when:** `discover_features(FeatureRegistry())` in production returns no `_example`; the contract harness still runs against the example; P8 is a regression test.

### HDW-127 · litellm never writes to stdout and logs through redaction · **Before HDW-202**
**Model** Sonnet · **Effort** Low · **Depends** 003, 115 · **Class** Core · **Refs** v3.1 §C5, F8
**Build:** in `_load_litellm` set `suppress_debug_info = True` and `set_verbose = False`, and attach `RedactionFilter` (plus a level cap) to the `LiteLLM`, `LiteLLM Router` and `LiteLLM Proxy` loggers; record the attribute names in ASSUMPTIONS (A-LLM-6). (FU-124-2) Also check call time, not only import time: with the HTTP layer mocked, a full streaming cloud call that fails opens connections only to the provider's own API host (no telemetry, cost-map or callback host).
**Accept when:** under `guard_stdout(strict=True)`, a cloud call that raises a mapped litellm exception (fake module) writes nothing to stdout; a key in a litellm log record is redacted; a mocked cloud call contacts no host other than the provider's.

### HDW-128 · Hints honour every active lock · **Before HDW-205**
**Model** Sonnet · **Effort** Low · **Depends** 112, 114 · **Class** Core · **Refs** v3.3 §B4, F9
**Build:** `ScopeDecision.active_locks` lists every lock in force (Private lock, project rule, secret, airplane), narrowing or not; `suggestions_for` and `no_route` key the cloud hint on it; the golden runner builds scope through `resolve_scope`.
**Accept when:** P3 yields no `use_cloud` hint; a golden row covers Private lock plus `default_scope: local` plus battery.

### HDW-129 · Escalation and the core verified retry go to a stronger model · **Before HDW-204**
**Model** Sonnet · **Effort** Medium · **Depends** 117, 123 · **Class** Core · **Refs** v3 §4.6, D10, F10
**Build:**
- `_escalate` and the new core retry (D10: one retry when `verify_required` and the check failed, no plugin needed) redecide over candidates whose task score is above the failed model's, with `escalation=None` so the optimistic rung can't fire again.
- The retry is counted in `max_attempts`, stays in scope, and is skipped after the first streamed token.
- (FU-123-1) Cover the HDW-123 case: a below-floor local pick in local scope whose mandatory verification fails retries once on a stronger *local* model; with none stronger, the answer comes back flagged `verify_failed` (D10) and no cloud client is built.
**Accept when:** tests cover cheap fail then stronger pass; no stronger candidate gives the original answer flagged; local scope retries only locally; the existing escalation test is updated so it goes up; the HDW-123 below-floor local case (the `agentic-local-scope-p2` shape) is tested both ways: a stronger local model passes, and with none stronger the answer is flagged `verify_failed`.

### HDW-130 · A mandatory gate that checked nothing is not a pass · **Before HDW-204**
**Model** Sonnet · **Effort** Low · **Depends** 116, 117 · **Class** Core · **Refs** v3 §4.5 step 4, F11
**Build:** `VerifyOutcome.checked` (checks that actually ran, skips excluded); when `decision.verify_required` and `checked == 0`, the result carries `verify="unchecked"`, the outcome row stores `unchecked`, and `below_floor` stays visible to the engine.
**Accept when:** P8's prose case reports `unchecked`; a missing grammar is a skip, not a pass; the old "not run" test is replaced.

### HDW-131 · Live rules in long-running engines · **With HDW-201**
**Model** Sonnet · **Effort** Medium · **Depends** 101, 117 · **Class** Core · **Refs** v3.3 §C, F12, F22
**Build:**
- `Router`, `Executor` and `QuotaTracker` take a rules provider (`Callable[[], Rules]`, `RulesManager.current`), read once per request in `_select` and in `enforce_scope`. `enforce_scope` re-checks `rules.private_lock` and `airplane` itself.
- The daemon schedules `poll_key` with the live `airplane`/lock state.
- (FU-122-2) `QuotaTracker.poll_key` is the only way a spent paid wall lifts early, and nothing calls it. The daemon calls it on a timer for each key that has a paid wall (`key:<alias>:paid` or `provider:<p>:paid`), under the same blocking rule as discovery (`_network_block`: airplane, Private lock, default local scope). It acts on the paid class only.
**Accept when:** a test toggles `private_lock` between two requests on one Router and the second never builds a cloud client; airplane set mid-run blocks the next cloud call; a test shows the timer calls `poll_key` for a key with a paid wall, a passing check lifts the paid wall and leaves a `:free` wall alone, and nothing is polled under airplane or a Private lock.

### HDW-132 · `primary_first` means the primary first · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 114 · **Class** Core · **Refs** v3.3 §A, F13
**Build:** under `primary_first`, every usable primary leads (flag `below_floor` when under the floor), then `openrouter/auto`, then the cost_saver ladder.
**Accept when:** a golden row with a primary scoring under the floor picks it, flagged; the walled-primary row still falls to auto.

### HDW-133 · Score terms that do something: offload, complexity · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 113 · **Class** Core · **Refs** v3 §7.1, F14, F19
**Build:** `scoring.offload_penalty` (rules) applied to `fit.status == "offload"`; make `complexity_penalty` model-dependent (scaled by the gap to 100) or remove it and its rule.
**Accept when:** P4 shows offload below fits; a golden row prefers a fitting model over an offloaded one with an equal score.

### HDW-134 · Core session stickiness (D11) · **Before HDW-302**
**Model** Sonnet · **Effort** Medium · **Depends** 117, 123 · **Class** Core · **Refs** v3 §4.5 session rules, D11, F15, F16
**Build:**
- Separate the session preference from residency: never zero `eviction_penalty`, never give a VRAM bonus to cloud models.
- The D11 rule: the last *successful* model leads while usable, in scope, passes the hard filter and still meets the floor (or is the same local best-effort choice). Pin only on `ok`.
- The reason line says "kept session model".
- (FU-120-4) `router_session.text_of` reads only message `content` (right for session keys and the executor system message) but looks like a scan helper. Rename it (for example `content_text`) or document that it is never the secret-scan path; the scan path is `_extract.collect(...).scan_text`.
**Accept when:** P5 shows no cloud warm bonus and keeps the evict penalty; a three-turn test keeps one model across a small ranking change and moves on a wall and on `verify_failed`; the helper's name or docstring says it is not the secret-scan path.

### HDW-135 · Remote images are refused, not dropped, for local models · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 115 · **Class** Core · **Refs** v3.3 §D2, F17
**Build:** a local call with a non-`data:` `image_url` raises a structured `image_url_not_local` error (no fetch); the engine may inline it outside local scope.
**Accept when:** tests cover a data URL (sent), an https URL in local scope (refused) and nothing fetched.

### HDW-136 · Unknown context is not "too small" · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 113 · **Class** Core · **Refs** F18
**Build:** `ctx is None` uses `unknown_model_defaults.ctx` (via the candidate builder) and rejects with `ctx_unknown` only if that is also too small.
**Accept when:** the filter test is updated; the suggestion is not `split_task` for unknown context.

### HDW-137 · Local fix hints name a model · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 117 · **Class** Core · **Refs** v3.3 §C, F20
**Build:** the router builds `local_fix_hint` and the `pull_model` suggestion text from `catalog.gpu_profiles[hw.profile].recommended` for the request's role (vision, agentic_coder, general).
**Accept when:** a vision request in local scope with no vision model says "pull <catalog model>".

### HDW-138 · Hard-coded tunables to rules and catalog; startup fail-safe · **Can wait**
**Model** Sonnet · **Effort** Medium · **Depends** 101, 103 · **Class** Core · **Refs** section 5 of this review, F26, F27
**Build:**
- Move every value marked "Tunable" or "Fact" in section 5 to `rules.yaml` or the catalog (one schema bump each); regenerate both schema files; delete `project_config.effective_scope` (F23) and the duplicate `CTX_HEADROOM`.
- On a startup parse error, keep any readable `private_lock`, `airplane` and `default_scope`; if none can be read, start in local scope and say why (F27).
**Accept when:** the section 5 table rows are gone from code; schema checks pass; an invalid file with `private_lock: true` starts locked.

### HDW-139 · Seed catalog: classifier and laziness patterns · **Can wait**
**Model** Sonnet · **Effort** Medium · **Depends** 103 · **Class** Core (data) · **Refs** v3 §4.6, F21
**Build:** a tiny text classifier entry for both GPU profiles; `placeholder_patterns` for the v3 4.6 laziness markers (`rest of (the )?code`, `// existing code`, `... remaining`, `TODO: implement`), all `verify: true`. (FU-122-5) Also check every seed `wall_patterns` row (OpenRouter 402 and `free-models-per-day`, OpenAI `insufficient_quota`, Anthropic credit balance, Gemini `RESOURCE_EXHAUSTED`, the generic 402/429/503 rows) against the provider's current API docs and record source and date in ASSUMPTIONS. The credit-class split in `quotas.py` (HDW-122) depends on these matching; a miss falls back to a provider-wide wall.
**Accept when:** the seed validates; the verify fixtures catch each marker; each wall pattern has a fixture body that classifies to the expected kind, scope and credit class.

### HDW-140 · Core hooks for `headwaters_think` (D12) · **Before HDW-205**
**Model** Sonnet · **Effort** Medium · **Depends** 106, 115, 117 · **Class** Core · **Refs** v3.4 §B1, D12
**Build:** `RouteRequest.task_hint` (forces `soft.task`); `RouteRequest.reasoning` (mapped to `ExecOptions.reasoning`); `ModelRow.reasoning_ok` from Ollama `capabilities: thinking` and OpenRouter `supported_parameters: reasoning` (migration 3) plus a `HardRequirements.reasoning` filter; a documented rule that reasoning text is never returned or stored.
**Accept when:** a think-style request in local scope picks a local thinking model with `think` on; reasoning text is absent from rows, events and the default result rendering.

### HDW-141 · Pin Python 3.13 for development · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 001 · **Class** – · **Refs** owner question 2026-10-31, gap 12
**Build:** add `.python-version` with `3.13`; delete `.venv313`; add `.venv*/` to `.gitignore`; keep `requires-python = ">=3.11"`; add an allowed-to-fail CI job on the newest Python. (FU-120-6) Also add a `.gitattributes` with `* text=auto eol=lf` and renormalize once (`git add --renormalize .`) in a commit of its own; the repo mixes LF and CRLF with `core.autocrlf=true` and Git warns on every touch. Already in place at triage (2026-10-31): `.python-version` 3.13, no `.venv313`, `.venv*/` in `.gitignore`; what remains is the allowed-to-fail CI job and the line-ending rule.
**Accept when:** a fresh `uv sync` builds a 3.13 env on a machine where 3.15 is the default; `ruff check .` passes on the whole tree; `git status` shows no LF/CRLF warnings on a fresh Windows checkout.

### HDW-142 · Secret detection sees joined and encoded secrets · **Before HDW-306**
**Model** Sonnet · **Effort** Medium · **Depends** 120 · **Class** Core · **Refs** v3.3 §D8, FU-120-2
**Build:**
- Per message, also scan the string leaves joined in order (with and without a separator) so a key split across adjacent JSON fields or content parts is seen. Results still hold pattern name and span only, never the text.
- Also scan decoded forms of candidate leaves: percent-encoded text and base64 blobs, one level deep, bounded in length and decoded size. A decode failure is ignored. The bounds are rules (`secrets.scan_*`), not code constants.
- Fuzz tests: a seed key split at every position across two fields; the same key base64-encoded and percent-encoded; benign base64 (hashes, long ids) does not trigger; a 1 MB `tool_result` scans within a time budget set in rules.
**Accept when:** the split and encoded forms of every seed secret pattern are detected and route local; no secret text in reasons or rows; the 1 MB case stays inside the time budget; the benign fixtures give no false positive.

### HDW-143 · CI runs on the real default branch and is green on ubuntu · **Before HDW-201**
**Model** Sonnet · **Effort** Low · **Depends** 001, 124 · **Class** – · **Refs** FU-122-6, triage
**Build:**
- `.github/workflows/ci.yml` runs on `push` to `main` only, but this repo's branch is `master` (origin has only `master`), so a push starts no CI. Trigger on `[main, master]` and add `workflow_dispatch`.
- The commits are already pushed (`573b612..8d0728e`, 2026-10-31), but that push started no CI because of the branch filter above. After the trigger fix, push it and give the session the first run's result for every cell: windows-latest and ubuntu-latest, Python 3.11 and 3.13. Fix what fails on Linux; likely causes are POSIX paths, `nvidia-smi` or `ollama` missing, cp1252 assumptions and CRLF.
- List every test that skips on Linux, each with its reason, in the report.
**Accept when:** a push to `master` starts CI; the head commit is green on all four cells; no test skips on ubuntu except ones marked Windows-only with a reason; the report names the run.

---

## Phase 2: MCP engine

### SPW-201 · Shared daemon, internal API, port handling
**Model** Sonnet · **Effort** High · **Depends** 117, 002 · **Class** Core · **Refs** v3.4 §B3, v3.1 §C3
**Build:**
- `headwaters daemon` process: single-instance lock, owns the Router, catalog refresh and keep-warm.
- An internal JSON API on 127.0.0.1 (token-authenticated via a per-user secret file) plus a **streamable HTTP MCP endpoint** for hosts that support it.
- Port selection: check the Hyper-V/WSL excluded ranges (`netsh int ipv4 show excludedportrange protocol=tcp`), fall back to the next free port, and write the chosen ports to a state file other components read.
- Health endpoint; graceful shutdown; `headwaters start|stop` commands.
- (FU-124-1, triage) `.importlinter` (HDW-124) lets only `headwaters.gateway` import `fastapi`/`starlette` and only `headwaters.mcp` import `mcp`. The repo layout puts the daemon and its internal HTTP API under `headwaters/mcp/`, and the daemon shares one process with the gateway (D7). Before writing code, settle which package owns the HTTP server and change the contract in this ticket if needed; record it in ASSUMPTIONS, or in DECISIONS if a package boundary moves. When the first web-framework import lands, show the contracts still bite.
**Accept when:** two clients share one daemon; the excluded-range parser is tested with sample output; the daemon refuses non-loopback binds; `lint-imports` keeps every contract with the real daemon code in place, and a throwaway `import starlette` from `headwaters.core` fails it.

### SPW-202 · stdio MCP shim
**Model** Sonnet · **Effort** Medium · **Depends** 201, 003 · **Class** Core · **Refs** v3.1 §C5, v3.4 §B3
**Build:**
- `headwaters-mcp` entry point: a thin stdio MCP server whose tool handlers forward to the daemon's internal API.
- Auto-starts the daemon if it isn't running.
- Strict stdout discipline (`guard_stdout`); UTF-8.
- Resolves absolute paths for host configs (`headwaters mcp-path`).
- (FU-124-1) Once `mcp>=2.3,<3` is a dependency, show the `mcp` contract bites: a throwaway `import mcp` in `headwaters.core` fails `lint-imports` (it passes vacuously today).
**Accept when:** an integration test drives the shim with an MCP client; the stdout-cleanliness test passes; a cold start launches the daemon; a throwaway import of the `mcp` SDK from `headwaters.core` fails `lint-imports`.

### SPW-203 · Workspace roots and files-by-path
**Model** Sonnet · **Effort** Medium · **Depends** 201 · **Class** Core · **Refs** v3 §5.2.2, v3.1 §C4, v3.4 §D
**Build:**
- Root resolution: MCP roots → configured root → cwd of the host.
- Path validation: drive letters, UNC paths, case-insensitive comparison, symlink escape; paths outside the roots are rejected.
- Ignore: `.gitignore`, `.headwatersignore`, and default secret/env patterns.
- Read files with token accounting; simple packing (files in the given order, truncated to the budget) until the ★ context engine replaces it through `ContextPlugin`.
**Accept when:** traversal attacks are rejected (Windows and POSIX cases); ignore rules are honored.

### SPW-204 · Front-door tools: `headwaters_do`, `headwaters_ask`, `headwaters_check`
**Model** Sonnet · **Effort** High · **Depends** 202, 203 · **Class** Core · **Refs** v3.4 §B1–B2
**Build:**
- **`headwaters_do`**:
  - inputs: `task`, `paths[]`, `images[]`, `scope?`, `want: answer|diff|plan`, `async?`
  - returns structured `{answer?, diffs[], commands[], model, tier, scope, verified, verify_reasons[], below_floor, reasons[], relay, result_uri}`
- **`headwaters_ask`**: read-only Q&A over paths.
- **`headwaters_check`**: verify a diff or code through the registered verifiers.
- Every tool has an `outputSchema`, structured content plus a short text rendering, and annotations (readOnly, idempotent hints).
- **Relay contract** `relay: verbatim|apply_diff|summarize_ok`.
- Compact results: unified diffs plus a `headwaters://result/<id>` resource for full detail.
- Tool descriptions are short, imperative and example-led, written for weak dispatcher models.
- (FU-123-2) The structured result also carries `suggestions[]` (`code`, `text`: `pull_model`, `split_task`, and `use_cloud` only when no lock applies), and the short text rendering starts with a warning line when `below_floor` is true, as `headwaters pick` does.
**Accept when:** MCP client tests validate the schemas; diffs are produced against real temp repos; local scope is honored through the tools; a local-scope request below the floor shows `below_floor`, `suggestions` and the warning line in the tool result.

### SPW-205 · Front-door tools: `_status`, `_scope`, `_handoff` (basic), `_think` (basic), `_more`
**Model** Sonnet · **Effort** Medium · **Depends** 204 · **Class** Core · **Refs** v3.4 §B1, v3.3 §C
**Build:**
- `headwaters_status`: walls, scope, locks, GPU, warm model, savings if the ledger is present.
- `headwaters_scope`: set scope, Private lock, strategy. Loosening a lock requires elicitation or user confirmation, never the model alone.
- `headwaters_handoff` (basic): minimal packet — goal, files, last error, recent turns. The ★ BridgePlugin upgrades it in SPW-607.
- `headwaters_think` (basic): single best reasoning model in scope. ★ overlays come in 1.x.
- `headwaters_more`: lists advanced tools and enables them via `tools/list_changed`. Advanced tools: `recommend_model` (with the exact host switch command, including OpenCode free models), `should_switch_host`, `list_local_models`, `quota_status`, `job_status`, `job_result`, `pin_model`, `feedback`.
**Accept when:** progressive disclosure works in the test client; a scope change persists and the change is visible in `status`.

### SPW-206 · Progress, async jobs, elicitation
**Model** Sonnet · **Effort** Medium · **Depends** 204 · **Class** Core · **Refs** v3 §5.2.2, v3.4 §B3
**Build:**
- Progress notifications during cold loads, drafting and cascades.
- Async jobs: use the spec's Tasks primitive if SPW-002 verified it, else the `job_id` pattern with `job_status`/`job_result`, triggered when the expected duration exceeds `async_threshold_s`. Job state lives in SQLite.
- Elicitation for spend above `confirm_above_usd` and for loosening a scope, where supported; otherwise require an explicit force flag.
**Accept when:** tests cover the progress stream, the job lifecycle and the elicitation fallback.

### SPW-207 · MCP prompts, command files, rules packs
**Model** Sonnet · **Effort** Medium · **Depends** 205 · **Class** Core · **Refs** v3 §5.2.3, v3.3 §C
**Build:**
- MCP prompts: `/headwaters-local`, `/headwaters-free`, `/headwaters-auto`, `/headwaters-handoff`, `/headwaters-status`.
- `.md` command files for hosts that read command folders.
- Rules packs in `integrations/rules/`: `AGENTS.md`, `.clinerules`, Kilo/Roo/Continue rules, Copilot instructions file. They teach:
  - call `headwaters_do` for implementation work
  - honor `relay`
  - apply diffs exactly
  - prefer delegating cheap subtasks
**Accept when:** prompts are listed and callable in the test client; rules files pass a lint check (length, no contradictory instructions).

### SPW-208 · MCP integration tests and host-eval harness skeleton
**Model** Sonnet · **Effort** Medium · **Depends** 204–207 · **Class** Core · **Refs** v3.4 §B2
**Build:**
- An end-to-end MCP suite (in-process client, mocked providers).
- `bench/host_eval/` harness: scripted tasks → (host profile, dispatcher model) → metrics for tool-selection rate, argument validity and relay compliance. Ships with 10 starter tasks; full suite in SPW-705.
**Accept when:** CI runs the e2e suite; the harness produces a JSON/Markdown report using a mocked dispatcher.

---

## Phase 2.5: Phase 2 review follow-ups

Tickets HDW-144 to HDW-154 come from the Phase 2 review (`docs/PHASE2_REVIEW.md`) and the triage session of 2026-11-01 (`docs/FOLLOWUPS.md`). **Before HDW-301** = merge before Phase 3 starts (only HDW-148); "Before HDW-xxx" = merge before that ticket; **Can wait** = any time before the Beta 0.1 gate (SPW-407). Ticket text is new, written from the follow-up and the live code.

### HDW-144 · Local GPU-busy check counts Ollama's own memory as another app's · **Before HDW-407**
**Model** Sonnet · **Effort** Medium · **Depends** 108, 201 · **Class** Core · **Refs** FU-201-2, FU-125-1
**Build:**
- `detect_gpu_busy` (`headwaters/core/hardware/state.py:150-155`): when Windows hides per-process memory and no Ollama process is listed, it counts all used VRAM as "other". With only Ollama's loaded model on the GPU it reads as busy, which matches the live symptom `skipped (gpu_busy)` in keep-warm. Subtract the VRAM Ollama reports for its loaded models (`/api/ps` `size_vram`) from the used total before deciding. **Reproduce first** on the dev GPU (one model loaded, nothing else running) and put the numbers in the report. This needs the owner's GPU machine.
- Record what `nvidia-smi --query-compute-apps` really prints on Windows (the encoding of process names), keep the capture in `docs/` like `ollama_tags_sample.json`, and set the decode from it (FU-125-1). A non-ASCII process name must never give a wrong busy reading.
- Unit tests with a fake `/api/ps` and fake GPU info: Ollama's model alone is not busy; a game plus Ollama's model is busy when the game's share is over the threshold; unknown per-process memory with no Ollama on the GPU still counts all used VRAM.
**Accept when:** the live repro no longer reports `gpu_busy` with only Ollama on the GPU and keep-warm pings; the unit cases pass; the `nvidia-smi` capture is in `docs/` and the assumption row is updated.

### HDW-145 · Check the daemon, shim and tools on real hosts and a real model · **Before HDW-407**
**Model** **Opus** · **Effort** High · **Depends** 206, 208 · **Class** – · **Refs** FU-202-1, FU-204-3, FU-206-3, triage
**Build:**
- A verification spike like HDW-002, on a real machine with real hosts and a real Ollama model. Record every result in `docs/ASSUMPTIONS.md` with the host version and date; every failure becomes a follow-up in the report.
- **Daemon lifetime (FU-202-1):** start the shim from VS Code, Claude Desktop and Cursor (whichever are installed), close the first IDE, and check the daemon still answers. If it dies, say which Job Object rule applies and propose the fix (scheduled task, service or `cmd /c start`).
- **Shim working folder (FU-204-3):** which folder each host starts the shim in, and whether it sends roots.
- **Real model through the shim:** `headwaters_do` end to end, a job with progress, `headwaters_more` (list, call, list again), result links after a daemon restart, job status and result tools (FU-204-6, FU-204-9, FU-205-10, FU-206-6, FU-206-7).
- **Real thinking model:** `headwaters_think` with a model that takes named levels (gpt-oss) and one that does not think (FU-205-4, FU-140-2); keep-warm against a real Ollama (FU-201-8).
- **Estimates:** measure real cold-load seconds, output tokens and cloud tokens per second against the numbers in `headwaters/mcp/tools/context.py` (FU-206-3); HDW-704 tunes them later.
- Add `-m live` tests wherever a check can be repeated.
**Accept when:** the report holds a table of host x check x result; every failure has a follow-up; ASSUMPTIONS rows exist with versions and dates; the daemon-lifetime question is answered for each installed host.

### HDW-146 · The shim forwards elicitation and cancellation; jobs can be cancelled · **Before HDW-407**
**Model** Sonnet · **Effort** High · **Depends** 206 · **Class** Core · **Refs** FU-206-1, FU-205-1, FU-202-2, FU-202-3, FU-206-4, FU-206-8
**Build:**
- The shim's client to the daemon declares the elicitation capability when the host does, forwards the daemon's question to the host and returns the answer. Scope and spend confirmations then ask inside the host instead of taking the `force` text fallback (D9: a host that can be asked is always asked).
- Forward a host's cancellation to the daemon: a cancelled call stops the model call and marks its job cancelled (progress is already relayed, `headwaters/mcp/shim.py:98`).
- Add a cancel tool for jobs and a retention rule for finished jobs (rows older than a rules value are deleted; the answer stays in memory only, as today).
- Bind a confirmation answer to the question's model and cost (FU-206-8): if the cached plan is gone and the new plan differs, ask again.
**Accept when:** a direct client and a shim client are both asked; tests with a fake host answering yes, no and cancel; cancelling a running job stops it; finished jobs past the retention are deleted; a changed plan asks again.

### HDW-147 · Workspace root rule (`workspace.roots`) · **Before HDW-403**
**Model** Sonnet · **Effort** Low · **Depends** 203, 204 · **Class** Core · **Refs** FU-203-2, FU-204-2
**Build:**
- Add `workspace.roots` (list of folders) to `rules.yaml` and its schema. `workspace_select.py` already applies the D9 order (host roots inside a configured root, else the configured root, else valid host roots, else the shim's folder); nothing supplies the configured part yet.
- Pass the configured roots from `Daemon.tool_context()`. A project config may only tighten.
**Accept when:** with `workspace.roots` set, a host root outside it is ignored and a path outside it is refused; unset behaves as today; a schema test covers the key; HDW-403 can write it.

### HDW-148 · Rebuild the engine when rules change · **Before HDW-301**
**Model** Sonnet · **Effort** Medium/High · **Depends** 131, 201 · **Class** Core · **Refs** FU-131-1, FU-131-2, FU-131-4
**Build:**
- `build_engine` takes one reading of the rules for the candidate builder, hardware, feature flags, `TargetBuilder`, `build_chain` and keep-warm. Make the daemon rebuild the engine on `on_reload` (or give each of them the provider) so edits to `tiers.paid.models`, the local host, `features`, endpoints and `kv_cache_type` apply without a restart. A rebuild must not drop a request in flight.
- Remove the `Daemon.rules` and `Engine.rules` startup snapshot once nothing reads it.
- The gateway (HDW-301) uses the same hook, so it never holds its own copy of the rules. Airplane and Private lock already apply per request and must keep doing so.
- (FU-131-4, owner answered (b) on 2026-11-01) A privacy switch must be true when it answers. Add an authenticated `POST /v1/reload` to the daemon API (next to `/v1/shutdown` in `headwaters/gateway/server.py`) that calls the rules manager's `reload()` now and returns whether new rules were applied, plus the live `private_lock`, `airplane` and `default_scope`; add the matching call to `core.daemon_client`. `headwaters private on|off` and `headwaters scope <x>` call it after writing `rules.yaml` and print the daemon's confirmation. No daemon running: say so and exit 0 (the file is the truth and the daemon reads it at start). Daemon running but unreachable, or it rejected the file: exit non-zero with "saved, but the running daemon has not confirmed it". The MCP `headwaters_scope` tool already reloads synchronously (`mcp/tools/scope_tool.py`); keep it. There is no airplane command today (`airplane` is not in `EDITABLE_KEYS`); any command added later uses the same call. A hand edit of `rules.yaml` is covered by the next item.
- (FU-131-4, hand edits) Close the 0.3 s window for a hand edit too. `RulesManager.current` compares the file's `(mtime_ns, size)` with the signature of the last read (one `os.stat`, measured at 25 microseconds on this machine) and, when it differs, calls `reload()` before returning. Record the signature even when the file is invalid, so an unfinished save is not re-parsed on every request; the next save changes it and reloads. An invalid file keeps the last good rules, as today. `on_reload` may now run on a request thread, so the engine rebuild above must be safe to call there. The watcher stays for the idle case. Tests: write `private_lock: true` by hand and read `current` at once, with no sleep; a half-written file keeps the last good rules and is read again after the next write; many reads of an unchanged file parse it once.
**Accept when:** tests change each listed key in a running daemon and the next request uses it; a request in flight finishes on the old engine; no reader of the snapshot is left; Private and airplane still apply at once; `headwaters private on` returns only after the daemon reports `private_lock` true, and a request sent right after it cannot reach a cloud model; a daemon that is unreachable gives the non-zero exit and the message above; a hand edit of `rules.yaml` that sets `private_lock: true` is seen by the very next request, with no wait.

### HDW-149 · Real seed catalog entries and the catalog gaps behind hints · **Before HDW-407**
**Model** Sonnet · **Effort** Medium · **Depends** 139, 140 · **Class** Core · **Refs** FU-139-1, FU-139-2, FU-140-1, FU-136-1, FU-137-1, FU-204-4
**Build:**
- Replace the `example-*` seed models with real catalog entries: a real tiny classifier slug with measured tps, local model recommendations per GPU profile (including a vision model) and free-limit values. Check each against current official docs and record sources in `docs/ASSUMPTIONS.md` (clean room).
- Pin the exact OpenRouter free-limit, Gemini daily-limit and Anthropic credit-balance body texts with live captured bodies; update `wall_patterns` and their tests.
- Add an optional `reasoning_ok` field to the catalog `ModelEntry` so a model known only from the catalog (or from OpenCode and OpenAI-compatible endpoints) can serve `think`.
- `_from_live` in `catalog/merge.py` must not present a defaulted `ctx` as known: mark it assumed (a flag on `ModelRow`) or skip the fill, so those models stop reporting `ctx_too_small`.
- Hints name real models: `pull_model` for an image request when only text-only local models exist (use `gpu_profiles.*.recommended.vision`), and the local fix hints from HDW-137.
**Accept when:** no `example-*` id in the seed; hints name real models; the catalog schema check is clean and the catalog is signed (`scripts/sign_catalog.py`); a model with unknown context is no longer rejected for it; every wall pattern row has a source and a date.

### HDW-150 · OpenCode free models can be called · **Before HDW-407**
**Model** Sonnet · **Effort** Medium · **Depends** 201 · **Class** Core · **Refs** FU-201-3, A-OC-3
**Build:**
- Check the OpenCode free-model endpoint (base URL, auth, error shapes) against its official docs and record it in `docs/ASSUMPTIONS.md`. A-OC-3 says no base URL is verified, so today these models are discovered and ranked but raise `provider_unsupported` in `headwaters/core/engine.py`.
- If the endpoint is documented: route these models through the executor and classify their walls. If no public endpoint exists: hide OpenCode free models from the picker, say so in `doctor`, and close the ticket as a decision.
**Accept when:** a mocked-HTTP test calls an OpenCode free model at the verified URL and classifies its walls, or the decision is written down; ASSUMPTIONS is updated; a Private lock or local scope never reaches it.

### HDW-151 · Move the remaining tunables to rules and catalog · **Can wait**
**Model** Sonnet · **Effort** Medium · **Depends** 138 · **Class** Core · **Refs** FU-138-1, FU-138-2, FU-138-3
**Build:**
- To `rules.yaml`: the remaining design section 5 tunables (reserve defaults, busy percent, residency and sysmem thresholds in `core/hardware/`), `streak_reset_s` in `quotas.py`, executor timeouts, `max_attempts`, and `DEFAULT_EFFORT`.
- To the catalog: GPU-range and max-loaded facts, the analyzer heuristics in `analyzer/classifier.py` and `requirements.py`, and the OpenRouter URLs in `cloud_map.py`.
- Defaults stay the same, and an invalid value fails safe at startup as in HDW-138.
**Accept when:** a test searches `headwaters/core` for the listed constants and finds none left in code; the golden results are identical with the defaults; each new key has a schema test.

### HDW-152 · Test and cleanup sweep · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 143, 208 · **Class** – · **Refs** FU-125-2, FU-126-1, FU-126-2, FU-127-1, FU-134-2, FU-136-2, FU-140-4, FU-141-2, FU-143-1, FU-143-2, FU-203-6, FU-205-7, FU-205-8, FU-208-3
**Build:**
- Tests: pass `encoding` to every `read_text()` and `write_text()` (10 calls, FU-125-2); give `tests/test_cli_misc.py` a fixture feature so the `_`-hiding rule is really tested (FU-126-2); list litellm's loggers after import and check none writes to stdout, re-checked on each bump (FU-127-1); a deterministic test for the WAL lock retry by holding an exclusive lock from one connection while another calls `_enable_wal` (FU-143-2).
- CI: run `ruff check` and `ruff format --check` on the same paths as the local gate and add `bench` (FU-141-2, FU-143-1, FU-208-3); print skip reasons on ubuntu and check the symlink tests ran there (FU-203-6; ubuntu shows 10 skips and nobody has read why).
- Cleanup: mark the old `features/_example` sentence in `docs/ASSUMPTIONS.md` as superseded (FU-126-1); remove `RouterConfig.residency`, defined at `router.py:107` and never read (FU-134-2); add `set_context` to the suggestion docstring in `mcp/tools/schemas.py` (FU-136-2); `models list --json` shows `reasoning_ok` and `pick` gets a `--reasoning` flag (FU-140-4).
- MCP tools: `should_switch_host` uses the catalog's id and alias lookup, not a name-suffix match (FU-205-7); `recommend_model` sizes with the token estimate of attached files when paths are given (FU-205-8).
**Accept when:** each item has its test or file change named in the report; ruff, mypy, pytest and lint-imports are clean; CI is green on windows and ubuntu.

### HDW-153 · Remote-model and feature guards · **Before HDW-306**
**Model** Sonnet · **Effort** Medium · **Depends** 121, 126, 135 · **Class** Core · **Refs** FU-121-4, FU-126-3, FU-135-2, Phase 1 recheck R4
**Build:**
- `CandidateBuilder` re-checks every local row's name and fields against the catalog's remote patterns, so a stale `local` row for a cloud model never becomes a candidate (R4). The executor guard stays as defence in depth.
- `discover_features` skips packages whose name starts with `_` unless asked; the test fixtures ask (FU-126-3).
- A request carrying a remote image URL drops local candidates in the hard filter instead of wasting one attempt on a refusal (FU-135-2), and the reasons say why.
**Accept when:** a stale `local` row for `gpt-oss:120b-cloud` is dropped before the executor; a `_`-prefixed package is not registered in production; a golden row covers the remote-image case; R4 is marked closed in the next review.


### HDW-154 · Escalation keeps tool-call strength for agentic requests · **Before HDW-304**
**Model** Sonnet · **Effort** Low · **Depends** 129 · **Class** Core · **Refs** FU-129-2, D10
**Build:**
- `Router._redecide` (`headwaters/core/router.py:413`) keeps only candidates whose task score is above the failed model's (`stronger_than`). For an agentic request (`run.reqs.soft.agentic`), also keep only candidates whose `agentic_tools` score is **not below** the failed model's. Take the failed model's score from its candidate (`run.candidate(decision.model)`); a model with no `agentic_tools` score counts as 0.
- Not-below, not strictly-above: requiring both scores to rise would empty the pool more often, and the answer would then come back flagged `verify_failed` when a perfectly good escalation existed.
- When this filter removes the only stronger candidate, add a note to the result saying so (`no stronger model keeps tool strength`), so the flagged answer explains itself.
- Non-agentic requests are unchanged. The rule is written down in D10's detail (`docs/DECISIONS.md`, refinement of 2026-11-01).
**Accept when:** an agentic request whose model failed verification escalates to a model with a higher task score and an equal or higher tool score; a candidate with a higher task score but a lower tool score is skipped; if it was the only stronger one, the answer comes back flagged `verify_failed` with the note; local scope stays local; a non-agentic request escalates as before (the existing escalation tests pass unchanged).

---

## Phase 3: Gateway

### SPW-301 · OpenAI-compatible endpoints and virtual models
**Model** Sonnet · **Effort** High · **Depends** 117, 201, 002 (D7) · **Class** Core · **Refs** v3 §5.1, v3.3 §B
**Build:** following D7 (thin FastAPI gateway + litellm SDK; the LiteLLM proxy is not used):
- `/v1/chat/completions` (streaming and non-streaming) and `/v1/models`, served inside or alongside the daemon on 127.0.0.1.
- `/v1/models` lists core picker models `headwaters/auto|free|local|cloud`, plus `headwaters/quality|duck` only when `picker.show_extras`.
- Requests call `Router.handle`.
- Pin the LiteLLM version (`uv.lock`).
- (FU-201-6) The daemon only probes and records the gateway and dashboard ports in `daemon.json`; nothing binds them. Bind through `netports.bind_first_free` and rewrite the state file with the port actually taken.
- (FU-135-3) A remote image URL sent to a local model is refused as an executor error; the gateway error must tell the user to send the image as a `data:` URL.
**Accept when:** an OpenAI SDK client completes chat (streaming and non-streaming) against mocked providers; the model list respects the config; the gateway port is bound by `bind_first_free` and `daemon.json` shows it; a remote image URL gives the `data:` URL advice.

### SPW-302 · Streaming failover, headers, session key
**Model** Sonnet · **Effort** High · **Depends** 301 · **Class** Core · **Refs** v3 §4.8, §5.1
**Build:**
- Fail over to the next fallback only **before the first token**. After the first token, emit a clean terminal error event in the protocol's format.
- Cold-load first-token timeout.
- Response headers `x-headwaters-model`, `x-headwaters-tier`, `x-headwaters-scope`, `x-headwaters-reason`.
- Session key from `x-session-id` or a hash.
- Advice header and inline note when `advice` is configured (handback advice itself is in SPW-502).
- (FU-123-2) Add `x-headwaters-below-floor: true` (and `x-headwaters-suggestion` with the first suggestion code) when the decision is below the floor. The body is not changed for it, so clients keep parsing.
- (FU-130-1, FU-130-2) Show `verify_status == "unchecked"` with the below-floor warning, never as verified. A streamed answer is verified after the fact, so the unchecked flag arrives on the final event; say so in the header or terminal event.
- (FU-134-3) A session on a streaming request that fails mid-stream is unpinned; today only verification failures unpin.
**Accept when:** tests cover a 429 / 402 / 503 before the first token (transparent retry) and a mid-stream failure (clean error); a below-floor local response carries the below-floor header; an unchecked answer is never shown as verified; a mid-stream failure unpins the session.

### SPW-303 · Anthropic `/v1/messages` endpoint
**Model** Sonnet · **Effort** High · **Depends** 302 · **Class** Core · **Refs** v3 §5.1, v3.2 §D
**Build:**
- Anthropic Messages API compatibility: system prompt, content blocks (text, image, tool_use, tool_result), streaming SSE event sequence, stop reasons.
- Use LiteLLM's translation (D7: `litellm.anthropic.messages`), with fixture tests.
- Document the Claude Code `ANTHROPIC_BASE_URL` setup (API keys only).
**Accept when:** an Anthropic SDK client works for text, tool use and streaming against mocked backends.

### SPW-304 · Tools, agentic handling, aliases, pin, suffixes
**Model** Sonnet · **Effort** Medium · **Depends** 302 · **Class** Core · **Refs** v3 §5.1, v3.3 §B3
**Build:**
- Tool-schema pass-through.
- Agentic requests require `agentic_tools ≥ floor`.
- Validate returned tool calls with `BasicVerifier`.
- Alias table (rules) for IDE-sent model names.
- Explicit model passthrough with failover unless `x-headwaters-pin: true`.
- Model suffixes `+verify`, `+quality`.
**Accept when:** tests pass for an agent tool loop (multiple tool turns), alias mapping, pin behaviour and suffix parsing.

### SPW-305 · Gateway integration tests: credits gone → multi-wall chain
**Model** Sonnet · **Effort** Medium · **Depends** 303, 304 · **Class** Core · **Refs** v3.2 §F
**Build:** a scenario suite with a programmable mock provider:
- host credits exhausted → user selects `headwaters/free` → free 429 → local completes an agent-mode tool loop
- the same with `headwaters/auto` (paid 402 → free 429 → local) behind the ★ flag
- a Copilot BYOK-style request shape
**Accept when:** all scenarios pass in CI on Windows.

### SPW-306 · Privacy egress test suite (local scope proof)
**Model** Sonnet implements, **Opus** reviews · **Effort** High · **Depends** 305, 205 · **Class** Core · **Refs** v3.3 §D
**Build:**
- A socket-level network mock that fails on any non-loopback connection.
- Runs local-scoped requests through the gateway, MCP tools, the classifier, verification, cascades, handoff and error paths.
- Also covers airplane mode (zero egress, including the catalog).
- Private-lock precedence across engines.
- (FU-120-3) Besides the monkeypatched socket spy (`tests/net_spy.py`), install a `sys.addaudithook` that fails on `socket.connect` and `socket.getaddrinfo` to a non-loopback host. Record in ASSUMPTIONS that a C extension opening its own sockets is still out of reach.
- (FU-120-1) After a secret-locked request, read the decision and outcome rows back from SQLite and assert no secret text in any column (reasons, alternatives, notes), for every HDW-120 shape: content part, `tool_calls` arguments, `tool_use` input, `tool_result`, system block. This closes the one HDW-120 accept-when item that was left open.
**Accept when:** the suite passes; Opus signs off that no code path constructs a cloud client in local scope; stored decision and outcome rows hold no secret text for every HDW-120 shape, and the audit hook records no non-loopback connect.

---

## Phase 4: Ship Beta 0.1

### SPW-401 · Key management
**Model** Sonnet · **Effort** Medium · **Depends** 101 · **Class** Core · **Refs** v3.1 §C7, v3 §11
**Build:**
- `keyring` (Windows Credential Manager) with an environment-variable fallback.
- `headwaters keys set|list|remove|test` with masked display.
- Validation pings per provider (no prompt content).
- Never written to logs or diagnostic bundles.
- (FU-122-1) Supply the callbacks the executor already accepts: `api_key_for(provider)` and `key_alias_for(provider)` (the alias only, never the secret), wired wherever the daemon, gateway and CLI build the `Executor`. No production code passes either today, so key-scoped walls (`key:<alias>:paid`) fall back to the provider subject.
- (FU-131-3, FU-201-4) The key-poll timer and the executor read keys only from environment variables (`OPENROUTER_API_KEY`, `openai_compatible.api_key_env`, litellm's own). Read keys saved by alias in the credential store too, so the poller and the executor see the same keys.
**Accept when:** tests run with a fake keyring backend; masking is verified; a 402 on a stored key records `key:<alias>:paid` in a test that builds the Executor from the key store (fake keyring); a key saved by alias is used by both the poll timer and the executor.

### SPW-402 · `doctor`
**Model** Sonnet · **Effort** Medium · **Depends** 108, 109, 201, 401 · **Class** Core · **Refs** v3.1 §C1–C3, v3 §5.3
**Build:** `headwaters doctor [--host X] [--bundle]` checks:
- Ollama install and version (≥ minimum); env vars `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_HOST`, read from the registry (user + system) and the process. Explains the "restart the Ollama tray app" step.
- GPU, reserve and sysmem-fallback hint (NVIDIA Control Panel → "CUDA – Sysmem Fallback Policy").
- Ports and excluded ranges; daemon health; keys validity; catalog freshness; git `core.autocrlf`.
- `--host`: live round trip, a tool-call test and a scope test for that host.
- `--bundle`: redacted diagnostic zip (no prompts, keys or paths beyond the app dirs).
- (FU-121-1) Hint: set `OLLAMA_NO_CLOUD=1` (or `disable_ollama_cloud` in `~/.ollama/server.json`) and restart Ollama when Private Mode is used.
- (FU-201-5) Note that on Windows `daemon.token` relies on the per-user profile folder with no explicit ACL, so another process of the same user can read it; say so in `doctor` and the privacy docs.
- (FU-201-9, owner confirmed (a) on 2026-11-01, D14) Read daemon state through `core.daemon_client`, never by importing `headwaters.daemon` (the `daemon-is-a-root` contract forbids it). A `sys.modules` test shows that importing the doctor loads neither the web framework nor the MCP SDK.
- (FU-201-10) Offer `headwaters stop --force`, which kills the pid in `daemon.json` when the API does not answer (a hung daemon).
- (FU-202-6) The "cannot start the daemon" error tells a locked-out port, an invalid `rules.yaml` (exit 2) and an already-running lock holder apart.
**Accept when:** each check is unit-tested with mocks; the bundle redaction test passes; importing the doctor module loads neither `starlette` nor `mcp`.

### SPW-403 · Host installer framework + first 4 hosts
**Model** Sonnet · **Effort** High · **Depends** 202, 301, 207, 002 · **Class** Core · **Refs** v3.4 §H, v3.2 §D, v3.1 §C5–C6
**Build:**
- `headwaters install|uninstall --host <id>|all`.
- Each host adapter detects the host, computes config changes, **shows a diff and asks for consent**, backs up the original, writes the change, and verifies it.
- Absolute launcher paths everywhere.
- Hosts in this ticket:
  - **VS Code / Copilot Chat:** MCP entry, BYOK custom endpoint with tool calling, `chat.agentHost.byokModels.enabled`, detect an org BYOK policy block
  - **Cline**
  - **OpenCode:** provider `baseURL` + MCP
  - **Claude Desktop:** MCP
- Templates in `headwaters/installer/templates/`.
- (FU-202-5) `mcp-path` assumes `headwaters-mcp` sits next to the interpreter; a pipx, `uv tool` or frozen install may not. The installer checks the path exists before writing a host config.
- (FU-205-3) Write the OpenCode provider and expose its real id, so the local-model switch command in `recommend.py` is exact.
- (FU-207-1, FU-207-2) Verify each host's command folder, `$ARGUMENTS` convention and rules-file location and front matter against current official docs, then install the command files and rules pack from `integrations/` into the project or host config.
**Accept when:** golden tests run against sample config files (merge without clobbering, idempotent re-run, uninstall restores the backup).

### SPW-404 · Status page (core, read-only)
**Model** Sonnet · **Effort** Medium · **Depends** 201 · **Class** Core · **Refs** v3.5 §B
**Build:** a minimal page on the dashboard port showing scope, locks, walls with ETAs, warm model, recent decisions (metadata), and the features list with the ★ legend. No editing controls (those are ★ dashboard, SPW-503). Loopback only. (FU-122-4) Show wall subjects readably (for example `OpenRouter: paid credits`) instead of the raw `...:paid` / `...:free` ids, and give `headwaters status` the same labels. Drop its "after a balance check" text for a wall with no end time; every wall now gets an end time (HDW-122).
- (FU-128-1) `headwaters status` (`cli/cmd_status.py`, `locks_in_force`) reads `decision.active_locks` instead of rebuilding the lock list by hand.
- (FU-128-2, FU-128-3) The decision log records `active_locks` as well as `locks`, and `headwaters pick` shows every lock in force, so Private Mode plus local scope does not show "none".
- (FU-133-1) Show `ScoreBreakdown.offload_penalty` in the decision reasons and the status page, so a user can see why an offloaded model ranked low.
- (FU-205-9) The `quota_status` advanced tool lists local models and shows a wall's provider and key scope separately.
**Accept when:** it renders with test data; nothing is reachable from a non-loopback address; wall labels are readable in the page and in `headwaters status`.

### SPW-405 · Windows installer, autostart, itch packaging
**Model** Sonnet · **Effort** High · **Depends** 402, 403 · **Class** Core · **Refs** v3.1 §C7
**Build:**
- Inno Setup script: per-user install (no admin), bundled `uv` binary + `uv.lock`, private venv in `%LOCALAPPDATA%\Headwaters\runtime`, launcher shims `headwaters.exe` and `headwaters-mcp.exe`.
- Task Scheduler "At log on" task for the current user (toggle).
- Uninstaller: removes the task and runtime; optionally removes state and keys (asks first).
- Code-signing hook in the build script (cert supplied by the owner).
- `.itch.toml` launch actions: Open status page, Doctor, Install for a host. `butler push` scripts for the channel `windows-beta`.
- Detects an itch-managed install and avoids self-update conflicts.
- Avoids PyInstaller one-file builds.
**Accept when:** installation works on a clean Windows VM (manual checklist in `docs/RELEASE_CHECKLIST.md`); install, run and uninstall leave no residue.

### SPW-406 · Linux service (second target)
**Model** Sonnet · **Effort** Low–Medium · **Depends** 201 · **Class** Core · **Refs** v3.1 §D2
**Build:** a systemd user-service unit installer/uninstaller; an install script using `uv`; a `butler` channel `linux-beta`.
**Accept when:** CI smoke test starts the daemon under a user service (or a documented manual check).

### SPW-407 · Beta 0.1 docs and release gate
**Model** Sonnet writes docs; **Opus** runs the gate · **Effort** Medium/High · **Depends** 401–406, 306
**Build:**
- README.
- Per-host setup pages, generated from the installer templates.
- Troubleshooting: context truncation, tool-call failures, cold loads, model-name mismatch, port conflicts, sysmem fallback.
- Privacy notice; ★ legend; beta promises (v3.5 §C, with the chosen tester promise filled in); itch store page draft (v3.5 §H).
- (FU-121-3) Publish `ollama_remote_patterns` in the signed catalog only after clients that know the key have shipped, or bump `schema_version`: an older client rejects unknown catalog keys.
**Opus gate:**
- Run the closed-alpha exit criteria: 10-minute install on 12 GB and 16 GB machines; stable in Cline, OpenCode, Claude Desktop and VS Code Copilot BYOK; no P1 bugs for 1 week.
- Open-source readiness spot check.
- Go/no-go for the open beta.

---

## Phase 5: Beta 0.2

### SPW-501 · Ledger and savings ★
**Model** Sonnet · **Effort** Medium · **Depends** 117 · **Class** ★ (`features/ledger`) · **Refs** v3 §4.9, v3.2 §E3
**Build:**
- `LedgerPlugin`: actual cost, plus counterfactual cost against the user's configured reference model.
- Reports by day, week and model.
- Optional "subscription credits avoided" estimate from dated catalog rates, labelled as an estimate.
- Exposed to status, CLI `headwaters savings` and the dashboard.
- (FU-205-6) `headwaters_status` shows savings only if a `DashboardPlugin.render_summary()` exists under `ledger`. Fix the shape of that dict and test the status tool against the real ledger.
**Accept when:** the calculations are unit-tested; disabling the feature removes it cleanly.

### SPW-502 · Paid scopes, budgets, handback advice ★
**Model** Sonnet · **Effort** High · **Depends** 114, 501 · **Class** ★ (`features/paid_scopes`) · **Refs** v3 §6, v3.2 §E2, v3.3 §B
**Build:**
- Enables paid tiers in `auto` and `cloud`: user paid models, optional `:floor` variant with the quantization filter, `openrouter/auto`.
- Budgets: `paid_daily_usd` and `confirm_above_usd` (elicitation / force flag).
- Battery and GPU-busy suggestion of `headwaters/cloud`.
- **Handback advice:** triggers when the best option is below floor AND there is no paid tier AND `hosts.subscription` is set. Prefixes the answer with an advisory and stores a handoff packet.
**Accept when:** golden scenarios cover budget caps and advice triggers; nothing in core changes behaviour when the feature is off.

### SPW-503 · Dashboard ★
**Model** Sonnet · **Effort** Medium · **Depends** 404, 501 · **Class** ★ · **Refs** v3 §5.3
**Build:** extends the status page with:
- live decisions (SSE)
- walls with ETAs
- savings charts
- model fit for this GPU
- verify catches
- 👍/👎 feedback per decision
- scope and strategy controls (lock loosening requires a confirm dialog)
- feature toggles
**Accept when:** UI tests (Playwright or httpx-level) pass; it binds to loopback only.

### SPW-504 · Tray app ★
**Model** Sonnet · **Effort** Medium · **Depends** 201 · **Class** ★ · **Refs** v3.1 §C7, v3.3 §C
**Build:**
- `pystray` icon: green / amber / red, plus **purple lock** for Private Mode.
- Menu: scope, Private toggle, open dashboard, doctor, quit.
- Rate-limited toasts on tier switches.
- Starts with the daemon.
**Accept when:** manual checklist on Windows; menu actions are covered by unit tests through the internal API.

### SPW-505 · Setup wizard ★
**Model** Sonnet · **Effort** High · **Depends** 402, 403, 401 · **Class** ★ · **Refs** v3 §5.3
**Build:** a local web wizard:
1. detect GPU, Ollama and env, and fix env with consent
2. recommend models from catalog GPU profiles and pull them with progress
3. select IDEs → installer with diffs
4. keys
5. default scope and strategy
6. autostart toggle
7. opt-in usage-report preview (SPW-508)
8. self-test
**Accept when:** an end-to-end walkthrough with mocks passes; every step is skippable and re-runnable.

### SPW-506 · VS Code extension: model provider ★
**Model** Sonnet · **Effort** High · **Depends** 301, 002 · **Class** ★ (`vscode-extension/`, TypeScript) · **Refs** v3.2 §E1
**Build:**
- An extension implementing `LanguageModelChatProvider` (per the API status found in SPW-002) that registers Headwaters Auto / Free / Local / Cloud (+ extras).
- Thin client to the gateway; discovers the port from the state file.
- Capabilities per model (tools, vision, context) from the gateway.
- Streams responses and handles tool calls.
**Accept when:** extension tests run in `@vscode/test-electron`; the models appear in the picker and work in agent mode against a mocked gateway.

### SPW-507 · VS Code extension: status bar, commands, publishing ★
**Model** Sonnet · **Effort** Medium · **Depends** 506 · **Class** ★ · **Refs** v3.2 §E1
**Build:**
- Status bar: tier/model, wall state, savings, purple lock.
- Commands: set scope, Private toggle, pin, open handoff, run doctor, open dashboard.
- Packaging for the VS Code Marketplace and Open VSX; listing text links to the itch page.
**Accept when:** the packaged `.vsix` installs and the commands work (manual checklist plus unit tests).

### SPW-508 · Opt-in usage report, feedback, survey ★
**Model** Sonnet · **Effort** Medium · **Depends** 102, 501 · **Class** ★ (`features/usage_report`) · **Refs** v3.5 §D2, §D4
**Build:**
- Off by default; shows a preview of the exact payload before it can be turned on.
- Weekly aggregate counts: version, OS, GPU class, scope/engine counts, ★ enabled + 14-day retention, verify catches, escalations, failovers, savings bucket.
- "Send report now" one-shot button.
- `headwaters feedback`: prefilled form, optional redacted doctor bundle.
- Survey prompt at 7 and 30 active days, at most twice, and never again after a dismissal.
**Accept when:** a schema test proves there are no free-text fields; no network call is made while it's disabled (socket mock).

### SPW-509 · Beta 0.2 review gate
**Model** **Opus** · **Effort** High · **Depends** 501–508
**Do:** check ★ isolation (import-linter, contract tests), the UX flow wizard → IDE → savings, privacy of the usage report, and readiness of the VS Code Marketplace listing. Write the release notes. File a punch list.

---

## Phase 6: Beta 0.3

### SPW-601 · Repo context engine A: symbol index ★
**Model** Sonnet · **Effort** High · **Depends** 203 · **Class** ★ (`features/context_engine`) · **Refs** v3.4 §D
**Build:**
- Incremental tree-sitter index (definitions, references, imports, file summaries placeholder) in SQLite.
- File watcher plus git-aware invalidation.
- Respects ignore files; secrets and env files excluded.
- Per-workspace DB under the cache dir.
- (FU-203-4) Ignore rules also read `.git/info/exclude`, the global git ignore file and ignore files above the root.
- (FU-203-7) `pack_files` reads each file in full up to 1 MiB before counting; use a size-first shortcut for large budgets or many files.
**Accept when:** index build and update tests pass on a sample multi-language repo; a 10k-file repo indexes within the documented time budget.

### SPW-602 · Context engine B: embeddings and packing ★
**Model** Sonnet · **Effort** High · **Depends** 601, 109 · **Class** ★ · **Refs** v3.4 §D
**Build:**
- CPU embeddings via Ollama (`num_gpu=0`), stored in `sqlite-vec`.
- `ContextPlugin.pack(task, paths, num_ctx)`: target files → referenced symbols → callers/callees → tests → docs, with token accounting against the chosen model's computed `num_ctx`.
- Wired into `headwaters_do`/`ask` when enabled.
- Local-only by construction.
- (FU-203-5) Token counts in `workspace_files.py` use chars/3.5; use `Analyzer.estimate_tokens` or the chosen model's tokenizer once packing knows the model's `num_ctx`.
**Accept when:** packing tests cover budget adherence and ranking order; it falls back to simple packing when disabled.

### SPW-603 · Analyzer layer 2: embedding kNN ★
**Model** Sonnet · **Effort** High · **Depends** 107, 602 · **Class** ★ (`features/knn`) · **Refs** v3.4 §C
**Build:**
- `ClassifierLayerPlugin`: kNN over a labeled prompt bank (from the catalog plus the user's local bank).
- Confidence from neighbor agreement, calibrated on a held-out set.
- Inserted between layers 1 and 3.
- `scripts/measure_coverage.py` reports the confident-coverage % and accuracy (target ≥ 90% confident coverage).
**Accept when:** the calibration script runs on the starter bank; latency is ≤ 30 ms on CPU (benchmark test, marked slow).

### SPW-604 · Verification v2 ★
**Model** Sonnet · **Effort** High · **Depends** 116, 601 · **Class** ★ (`features/verify_v2`) · **Refs** v3.4 §F, v3 §4.6
**Build:**
- `VerifierPlugin` adding a grounding check: every referenced symbol or file must exist in the index or the diff.
- Project checks from `.headwaters.yaml`, run with an allowlist (`pwsh` / `powershell` / `cmd` explicitly; no `shell=True` with untrusted strings) and timeouts.
- Machine-readable failure reasons.
- (FU-204-5) `headwaters_check` does not run the project's tests or linters (`.headwaters.yaml` `checks:` is read but unused) and does not check that a diff touches only the named `paths`. Add both.
**Accept when:** fixtures cover hallucinated symbols, failing lint and timeouts.

### SPW-605 · Worktree execution ★
**Model** Sonnet · **Effort** High · **Depends** 604 · **Class** ★ (`features/worktree`) · **Refs** v3.4 §E
**Build:**
- `exec: worktree`: create a throwaway `git worktree`, apply the diff, run checks, collect results, clean up.
- Windows Job Object limits (CPU time, memory, process count); network blocking best-effort and documented.
- Hard-deny destructive commands (force push, recursive delete outside the worktree, credential access).
- Returns only diffs that passed.
**Accept when:** tests cover cleanup on failure and the deny-list; the main working tree is untouched.

### SPW-606 · Verify-then-escalate loop ★
**Model** Sonnet · **Effort** High (Opus reviews at 608) · **Depends** 604, 114 · **Class** ★ (EscalationPlugin) · **Refs** v3 §4.5–4.6
**Build:**
- `EscalationPlugin` used by the Router: on verify failure, escalate one rung within the effective scope, carrying the failure reasons in the prompt.
- Limited by `max_escalations` and the remaining budget.
- Enables the optimistic rung.
- Local scope escalates locally only.
- Records pass/fail per (model, task) in outcomes.
- (FU-206-2) The spend question covers the first pick only; a cascade or verified retry may call a costlier model. Ask again before a paid escalation, or cap spend per request.
**Accept when:** scenario tests cover a cheap-model fail → escalate → pass, the cap reached, and local-only escalation.

### SPW-607 · Handoff packets and sticky sessions ★
**Model** Sonnet · **Effort** High · **Depends** 117, 205 · **Class** ★ (`features/handoff`) · **Refs** v3 §4.7, §4.5 session rules
**Build:**
- `BridgePlugin` packet: goal, constraints, decisions, files (path + sha + role), open items, last error, last N turns verbatim, older-turn summary (a model that fits, in scope), reasoning summary (never raw reasoning tokens).
- Deterministic extraction first; code is never summarized.
- Sticky sessions: choose the tier at session start or on a context threshold; switch only on a wall or repeated verify failures; write a packet on every switch.
- Full `headwaters_handoff`.
- (FU-134-1) Revisit the below-floor local session rule if users see flapping: keep the lead only when the same local best-effort model still ranks first.
- (FU-140-3) Decide whether the short reasoning summary that v3 4.7 allows is wanted; D12 builds none.
- (FU-205-5) Add an older-turn summary and the router session key to the handoff packet, and have `should_switch_host` return its `handoff_packet_id` (always null today).
**Accept when:** packet schema tests pass; a switch scenario writes a packet; local scope keeps summarization local.

### SPW-608 · Beta 0.3 review gate
**Model** **Opus** · **Effort** High · **Depends** 601–607
**Do:**
- Measure, on reference prompts, local-model quality with and without the context engine and verify v2 (a dry run of HeadwatersBench Suite 2).
- Security review of worktree execution.
- Punch list and release notes.

---

## Phase 7: Beta 0.4

### SPW-701 · Hooks engine framework + Claude Code ★
**Model** Sonnet · **Effort** High · **Depends** 117, 403, 002 · **Class** ★ (`features/hooks`) · **Refs** v3.4 §G
**Build:**
- Pre-prompt hook runtime: classify; then either answer simple, read-only, in-scope prompts (short-circuit or inject the verified answer, depending on what the host API allows) or pass through with a compact context pack.
- Fail-open by default; fail-closed only when "strict local" is enabled.
- Installer adapter for **Claude Code** hooks (verify the current hook API first).
**Accept when:** simulated hook-event tests cover answer, inject and pass-through; the installer golden test passes.

### SPW-702 · Hooks for additional hosts ★ (repeat per host)
**Model** Sonnet · **Effort** Medium · **Depends** 701 · **Class** ★ · **Refs** v3.4 §G
**Template:** one ticket per host: Cursor, Codex CLI, Gemini CLI, Copilot CLI, Windsurf.
1. Verify the host's current hook API and record it in ASSUMPTIONS.
2. Write the adapter on top of the 701 framework.
3. Add installer support with diff, consent and backup.
4. Add `doctor --host` checks.
**Accept when:** golden config tests pass and simulated events are handled.

### SPW-703 · Installers for remaining hosts ★ (repeat per host)
**Model** Sonnet · **Effort** Medium · **Depends** 403 · **Class** ★ · **Refs** v3 §9, v3.4 §H
**Template:** one ticket per host:
- Kilo, Roo, Continue: gateway + MCP + rules
- Claude Code: `ANTHROPIC_BASE_URL` through the settings `env` block + MCP
- Cursor: MCP only
- Trae: MCP plus custom endpoint if available
- Windsurf

Each ticket: detect the host, show a diff, back up the config, verify, uninstall, add `doctor --host` checks, and generate a per-host docs page.
- (FU-207-1, FU-207-2) Per host: verify the command folder, `$ARGUMENTS` convention and rules-file location and front matter against current docs, and install the command files and rules pack from `integrations/`.
**Accept when:** golden merge, idempotency and uninstall tests pass.

### SPW-704 · HeadwatersBench
**Model** Sonnet · **Effort** High · **Depends** 208, 602, 604 · **Class** Core (harness may be published) · **Refs** v3.4 §I, v3.5 §E1 Q4
**Build:** `bench/headwatersbench/` with four suites:
1. routing accuracy vs. labeled prompts
2. quality vs. a paid baseline (pass@1 with tests, verified-diff rate), **with and without** context engine + verify v2
3. savings at equal quality (counterfactual $, host tokens avoided)
4. host tool-call reliability (uses SPW-705)

Also: reference GPU profiles (12 GB, 16 GB); reproducible configs; reports with methodology, n and dates.
- (FU-133-2) Tune the offload penalty values (30, 10) with bench data.
- (FU-206-3) Tune the estimate numbers in `mcp/tools/context.py` (output tokens, load seconds per GB, cloud tokens per second), or learn them from `outcomes.latency_ms`.
**Accept when:** the full run completes on one reference machine and produces a Markdown/HTML report.

### SPW-705 · Host tool-call eval suite (full)
**Model** Sonnet · **Effort** Medium · **Depends** 208 · **Class** Core · **Refs** v3.4 §B2
**Build:**
- Expand to 100+ scripted tasks across the front-door tools.
- Run them per (host profile, dispatcher model), including weak free models and local dispatchers.
- Nightly optional live job.
- Feeds tool-description tuning.
- (FU-208-1, FU-208-2) Real dispatcher models, a 100+ task set, per-host tool-description variants and host quirks in the profiles, and a CLI entry point plus CI job that publishes a report.
- (FU-204-7, FU-204-8) Score the tool descriptions on tool-selection rate, argument validity and relay compliance, and measure on real local-model output whether a diff written mid-answer without a fence needs extraction heuristics.
**Accept when:** the report shows per-model selection, argument validity and relay rates; a regression threshold fails CI when breached (mocked mode).

### SPW-706 · Beta 0.4 review gate
**Model** **Opus** · **Effort** High · **Depends** 701–705
**Do:** review hook safety (fail-open semantics, strict-local), host breadth QA, and HeadwatersBench methodology. Approve publishing benchmark results (no unverified % claims). Decide on a RouterArena submission.

---

## Phase 8: RC → 1.0

### SPW-801 · RC hardening and open-source readiness
**Model** **Opus** (plan/review) + Sonnet (fixes) · **Effort** High · **Depends** 706
**Do:**
- Feature freeze; performance profiling (routing overhead, daemon memory); bug bash checklist.
- Security review: path validation, worktree, hooks, local API auth, update signature.
- Dependency licence audit: permissive only in any closed package; complete `THIRD_PARTY_NOTICES.md`.
- Secrets scan of the repo; ★ separation report; a "ready to open-source core" checklist (for D1).
- Start the ≥ 4-week evidence window.
- (FU-143-3) Pin GitHub Actions (`checkout`, `setup-uv`, `upload-artifact`) by commit SHA, not tag, before the public release.

### SPW-802 · Decision evidence report
**Model** Sonnet (scripts) + **Opus** (analysis) · **Effort** Medium/High · **Depends** 508, 704, 801
**Build:**
- `scripts/decision_report.py` aggregates opt-in reports, survey CSV exports and HeadwatersBench results into the Q1–Q5 table from v3.5 §E1, with sample sizes.
- Opus drafts `docs/DECISION_1_0.md` with a recommendation per the v3.5 §E2 outcomes. **The owner makes the decision.**

### SPW-803 · Apply the 1.0 decision (conditional)
**Model** Sonnet · **Effort** High · **Depends** 802 + owner decision
**If split:**
- Extract `headwaters/features/*` into a private package registered via `headwaters.plugins` entry points; separate builds and itch channels (`windows-lite`, `windows-pro`).
- Tester download keys per the beta promise (verify itch's key mechanism).
- Update the store pages.

**If single paid product, extension add-on, or staying free:** adjust packaging, channels and store copy accordingly.
**Accept when:** contract tests pass with the external plugin package; a clean install of each edition works.

---

## Phase 9: 1.x extras

### SPW-901 · Fusion "deliberate" ★
**Model** Sonnet · **Effort** Medium · **Depends** 502, 205 · **Refs** v3 §6, v3.4 §B1
**Build:**
- `headwaters_think(mode="deliberate", preset=quality|budget, max_usd)` via OpenRouter Fusion (verified slug and presets).
- Opt-in only, budget-capped, elicitation-confirmed.
- Never in local or free scope.
**Accept when:** budget-cap and scope-refusal tests pass.

### SPW-902 · Rubber duck overlay ★
**Model** Sonnet · **Effort** Low–Medium · **Depends** 205 · **Refs** v3 §4.5 overlays, v3.3 §B2
**Build:**
- `headwaters_think(mode="duck")` and the optional picker model `headwaters/duck`: a Socratic system prompt on the cheapest fast model allowed by the lock state.
- `/headwaters-duck` prompt.
**Accept when:** works in every scope, including local.

### SPW-903 · Image generation tool ★
**Model** Sonnet · **Effort** Medium · **Depends** 205, 105 · **Refs** v3 §5.2.1
**Build:**
- Advanced tool `generate_image` via `headwaters_more`: OpenRouter `/api/v1/images`, model chosen dynamically from image-model discovery.
- Saves the file to the workspace; reports cost.
- Refused in local and free scope unless a free or local image option exists.
**Accept when:** mocked API tests cover model selection and scope refusal.

### SPW-904 · Learning ★
**Model** Sonnet · **Effort** Medium · **Depends** 606, 503 · **Refs** v3 §4.9, v3.4 §C
**Build:**
- Learned score per (model, task) as an EMA from verify pass/fail, thumbs, and implicit failure (re-ask within N seconds).
- Local prompt-bank growth for kNN.
- Opt-in export of anonymized labels only.
- Reset and override controls.
**Accept when:** convergence tests pass; the export payload schema contains no free text.

### SPW-905 · MCP sampling tier ★ · **DEFERRED (D9): do not build**
**Model** Sonnet · **Effort** Medium · **Depends** 206, 002 · **Refs** v3.4 §B3
**Build:**
- When the host supports MCP sampling, offer the host model as an optional tier for `headwaters_do` subtasks.
- **Never in local scope.** User opt-in.
**Accept when:** capability detection and scope-refusal tests pass.

---

## 3. Ticket count summary

| Phase | Tickets | Opus-led |
| --- | --- | --- |
| 0 | 4 | SPW-002 |
| 1 | 19 | SPW-119 |
| 2 | 8 | – |
| 3 | 6 | SPW-306 review |
| 4 | 7 | SPW-407 gate |
| 5 | 9 | SPW-509 |
| 6 | 8 | SPW-608 |
| 7 | 6 (+ per-host repeats of 702/703) | SPW-706 |
| 8 | 3 | SPW-801, 802 |
| 9 | 5 | – |
| **Total** | **75 base tickets** | 9 |
