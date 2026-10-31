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
                      usage_report, fusion, duck, image, learning, sampling)
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
| 9 Extras | 1.x | SPW-901 → 905 | Fusion, duck, image, learning, sampling |

**Critical path to Beta 0.1:** 001 → 002 → 003/004 → 101 → 102 → 103 → 105 → 106 → 108 → 109 → 110 → 112 → 113 → 114 → 115 → 117 → 201 → 204 → 301 → 302 → 403 → 405 → 407.

**Scope-risk valve (v3.5 §G):** if 0.1 slips, ship Phases 1, 2 and 4 to closed-alpha testers without the gateway, then finish Phase 3 before the open beta.

### Open decisions flagged while ticketing (decide before Phase 1 ends)
1. **Escalation in core?** v3.5 makes verify-then-escalate ★. These tickets follow that: core verifies and reports, and the optimistic rung only activates if an escalation plugin is registered. Consider allowing a single retry on the next candidate in core.
2. **Sticky sessions in core?** v3.5 makes them ★. Without them, gateway agent loops can switch models between turns. These tickets put session keying and warm-model preference in core and full stickiness in ★ (SPW-607). Consider moving basic stickiness to core.
3. **`headwaters_think` in 0.1:** shipped as single-model reasoning (core). Fusion and duck modes arrive in 1.x as ★ overlays.

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
- Docs stubs: `docs/ASSUMPTIONS.md`, `docs/PROVENANCE.md`, `docs/DECISIONS.md` (seed with D1–D6), `THIRD_PARTY_NOTICES.md`, `LICENSE` placeholder ("All rights reserved" for now).
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
4. optimistic rung (within margin δ AND verifiable) **only if an `EscalationPlugin` is registered**
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

## Phase 2: MCP engine

### SPW-201 · Shared daemon, internal API, port handling
**Model** Sonnet · **Effort** High · **Depends** 117, 002 · **Class** Core · **Refs** v3.4 §B3, v3.1 §C3
**Build:**
- `headwaters daemon` process: single-instance lock, owns the Router, catalog refresh and keep-warm.
- An internal JSON API on 127.0.0.1 (token-authenticated via a per-user secret file) plus a **streamable HTTP MCP endpoint** for hosts that support it.
- Port selection: check the Hyper-V/WSL excluded ranges (`netsh int ipv4 show excludedportrange protocol=tcp`), fall back to the next free port, and write the chosen ports to a state file other components read.
- Health endpoint; graceful shutdown; `headwaters start|stop` commands.
**Accept when:** two clients share one daemon; the excluded-range parser is tested with sample output; the daemon refuses non-loopback binds.

### SPW-202 · stdio MCP shim
**Model** Sonnet · **Effort** Medium · **Depends** 201, 003 · **Class** Core · **Refs** v3.1 §C5, v3.4 §B3
**Build:**
- `headwaters-mcp` entry point: a thin stdio MCP server whose tool handlers forward to the daemon's internal API.
- Auto-starts the daemon if it isn't running.
- Strict stdout discipline (`guard_stdout`); UTF-8.
- Resolves absolute paths for host configs (`headwaters mcp-path`).
**Accept when:** an integration test drives the shim with an MCP client; the stdout-cleanliness test passes; a cold start launches the daemon.

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
**Accept when:** MCP client tests validate the schemas; diffs are produced against real temp repos; local scope is honored through the tools.

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

## Phase 3: Gateway

### SPW-301 · OpenAI-compatible endpoints and virtual models
**Model** Sonnet · **Effort** High · **Depends** 117, 201, 002 (D7) · **Class** Core · **Refs** v3 §5.1, v3.3 §B
**Build:** following D7 (LiteLLM proxy + hooks **or** thin FastAPI + litellm SDK):
- `/v1/chat/completions` (streaming and non-streaming) and `/v1/models`, served inside or alongside the daemon on 127.0.0.1.
- `/v1/models` lists core picker models `headwaters/auto|free|local|cloud`, plus `headwaters/quality|duck` only when `picker.show_extras`.
- Requests call `Router.handle`.
- Pin the LiteLLM version if it's used.
**Accept when:** an OpenAI SDK client completes chat (streaming and non-streaming) against mocked providers; the model list respects the config.

### SPW-302 · Streaming failover, headers, session key
**Model** Sonnet · **Effort** High · **Depends** 301 · **Class** Core · **Refs** v3 §4.8, §5.1
**Build:**
- Fail over to the next fallback only **before the first token**. After the first token, emit a clean terminal error event in the protocol's format.
- Cold-load first-token timeout.
- Response headers `x-headwaters-model`, `x-headwaters-tier`, `x-headwaters-scope`, `x-headwaters-reason`.
- Session key from `x-session-id` or a hash.
- Advice header and inline note when `advice` is configured (handback advice itself is in SPW-502).
**Accept when:** tests cover a 429 / 402 / 503 before the first token (transparent retry) and a mid-stream failure (clean error).

### SPW-303 · Anthropic `/v1/messages` endpoint
**Model** Sonnet · **Effort** High · **Depends** 302 · **Class** Core · **Refs** v3 §5.1, v3.2 §D
**Build:**
- Anthropic Messages API compatibility: system prompt, content blocks (text, image, tool_use, tool_result), streaming SSE event sequence, stop reasons.
- Use LiteLLM's translation if D7 chose it; otherwise build a translation layer with fixture tests.
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
**Accept when:** the suite passes; Opus signs off that no code path constructs a cloud client in local scope.

---

## Phase 4: Ship Beta 0.1

### SPW-401 · Key management
**Model** Sonnet · **Effort** Medium · **Depends** 101 · **Class** Core · **Refs** v3.1 §C7, v3 §11
**Build:**
- `keyring` (Windows Credential Manager) with an environment-variable fallback.
- `headwaters keys set|list|remove|test` with masked display.
- Validation pings per provider (no prompt content).
- Never written to logs or diagnostic bundles.
**Accept when:** tests run with a fake keyring backend; masking is verified.

### SPW-402 · `doctor`
**Model** Sonnet · **Effort** Medium · **Depends** 108, 109, 201, 401 · **Class** Core · **Refs** v3.1 §C1–C3, v3 §5.3
**Build:** `headwaters doctor [--host X] [--bundle]` checks:
- Ollama install and version (≥ minimum); env vars `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_MAX_LOADED_MODELS`, `OLLAMA_HOST`, read from the registry (user + system) and the process. Explains the "restart the Ollama tray app" step.
- GPU, reserve and sysmem-fallback hint (NVIDIA Control Panel → "CUDA – Sysmem Fallback Policy").
- Ports and excluded ranges; daemon health; keys validity; catalog freshness; git `core.autocrlf`.
- `--host`: live round trip, a tool-call test and a scope test for that host.
- `--bundle`: redacted diagnostic zip (no prompts, keys or paths beyond the app dirs).
**Accept when:** each check is unit-tested with mocks; the bundle redaction test passes.

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
**Accept when:** golden tests run against sample config files (merge without clobbering, idempotent re-run, uninstall restores the backup).

### SPW-404 · Status page (core, read-only)
**Model** Sonnet · **Effort** Medium · **Depends** 201 · **Class** Core · **Refs** v3.5 §B
**Build:** a minimal page on the dashboard port showing scope, locks, walls with ETAs, warm model, recent decisions (metadata), and the features list with the ★ legend. No editing controls (those are ★ dashboard, SPW-503). Loopback only.
**Accept when:** it renders with test data; nothing is reachable from a non-loopback address.

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
**Accept when:** index build and update tests pass on a sample multi-language repo; a 10k-file repo indexes within the documented time budget.

### SPW-602 · Context engine B: embeddings and packing ★
**Model** Sonnet · **Effort** High · **Depends** 601, 109 · **Class** ★ · **Refs** v3.4 §D
**Build:**
- CPU embeddings via Ollama (`num_gpu=0`), stored in `sqlite-vec`.
- `ContextPlugin.pack(task, paths, num_ctx)`: target files → referenced symbols → callers/callees → tests → docs, with token accounting against the chosen model's computed `num_ctx`.
- Wired into `headwaters_do`/`ask` when enabled.
- Local-only by construction.
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
**Accept when:** scenario tests cover a cheap-model fail → escalate → pass, the cap reached, and local-only escalation.

### SPW-607 · Handoff packets and sticky sessions ★
**Model** Sonnet · **Effort** High · **Depends** 117, 205 · **Class** ★ (`features/handoff`) · **Refs** v3 §4.7, §4.5 session rules
**Build:**
- `BridgePlugin` packet: goal, constraints, decisions, files (path + sha + role), open items, last error, last N turns verbatim, older-turn summary (a model that fits, in scope), reasoning summary (never raw reasoning tokens).
- Deterministic extraction first; code is never summarized.
- Sticky sessions: choose the tier at session start or on a context threshold; switch only on a wall or repeated verify failures; write a packet on every switch.
- Full `headwaters_handoff`.
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
**Accept when:** golden merge, idempotency and uninstall tests pass.

### SPW-704 · HeadwatersBench
**Model** Sonnet · **Effort** High · **Depends** 208, 602, 604 · **Class** Core (harness may be published) · **Refs** v3.4 §I, v3.5 §E1 Q4
**Build:** `bench/headwatersbench/` with four suites:
1. routing accuracy vs. labeled prompts
2. quality vs. a paid baseline (pass@1 with tests, verified-diff rate), **with and without** context engine + verify v2
3. savings at equal quality (counterfactual $, host tokens avoided)
4. host tool-call reliability (uses SPW-705)

Also: reference GPU profiles (12 GB, 16 GB); reproducible configs; reports with methodology, n and dates.
**Accept when:** the full run completes on one reference machine and produces a Markdown/HTML report.

### SPW-705 · Host tool-call eval suite (full)
**Model** Sonnet · **Effort** Medium · **Depends** 208 · **Class** Core · **Refs** v3.4 §B2
**Build:**
- Expand to 100+ scripted tasks across the front-door tools.
- Run them per (host profile, dispatcher model), including weak free models and local dispatchers.
- Nightly optional live job.
- Feeds tool-description tuning.
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

### SPW-905 · MCP sampling tier ★
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
