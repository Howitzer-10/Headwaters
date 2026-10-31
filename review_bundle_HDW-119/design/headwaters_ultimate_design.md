# Headwaters: Ultimate Design (v3)

*Working name. It replaces "HyperMesh Router". See §11.1 for why the name has to change.*

> **One-line pitch:** Use your best model until it runs out, and never pay for work a free or local model can do just as well. This works in any IDE, on your own GPU.

---

## 0. Goals this design must meet

| # | Goal | Where it is handled |
| --- | --- | --- |
| G1 | Minimize paid usage without losing quality | Policy engine downgrade + verification gate (§4.5, §4.6) |
| G2 | Automatic failover on rate-limit and out-of-credit walls | Wall engine (§4.4) in both the gateway and the MCP server |
| G3 | Smart local routing by prompt requirements (vision, tools, context, task) | Analyzer + catalog + hardware guard (§4.1–4.3) |
| G4 | 100% local mode | `local_only` mode; works with no keys at all |
| G5 | Works in every IDE (Cline, Kilo, Roo, Continue, OpenCode, Claude Code, VS Code, Cursor, Trae, …) | Two surfaces: gateway + MCP (§5, §9) |
| G6 | Complement OpenRouter Auto and Fusion instead of reinventing them | Treated as tiers, never reimplemented (§6) |
| G7 | Pick the best OpenCode free model, or local if that is better | Catalog ranks OpenCode free models alongside everything else (§4.2) |
| G8 | Hardware safety on 12 GB / 16 GB RTX | VRAM and KV-cache budget math, keep-warm, single-resident policy (§7) |
| G9 | Sellable on itch.io: easy install, low support burden, legally clean | Editions, packaging, ToS rules (§10, §11) |

---

## 1. Comparison: HMR spec vs. my v2 proposal vs. this design

| Area | HMR spec | My v2 proposal | Ultimate (v3) |
| --- | --- | --- | --- |
| Primary surface | MCP only | Gateway first, MCP for delegation | **Both, sharing one core.** Gateway handles walls on the host model. MCP handles delegation and works in locked hosts. |
| Host model gets walled | Not addressed | Gateway handles it; MCP needs a dispatcher | Gateway does it transparently. In MCP-only mode, a local dispatcher fallback takes over. |
| Model list | Hard-coded slugs (several are probably retired by now) | Dynamic discovery + seed scores | Dynamic discovery **plus a curated catalog that updates without a release** |
| Requirement detection | Caller passes flags + "complexity keyword lists" | Heuristics + small classifier | Heuristics + classifier on CPU; flags only as **overrides** |
| VRAM safety | "Over 15,000 chars → send to cloud" | VRAM-fit check | **Token count + KV-cache budget per model per GPU**. The 15k-char rule is wrong both ways (§7.2). |
| `num_ctx` | Fixed at 16k/32k | Per model | **Computed** as the largest context that fits the budget |
| Quality control | AST check (Python) + placeholder scan | Quality floor + feedback | **Verify, then escalate**: tree-sitter (all languages), placeholder scan, `git apply --check`, optional project lint/tests, JSON schema |
| Premium tiers | `openrouter/fusion`, `fusion-flash` (opt-in) | Not covered | Fusion kept as an **opt-in, budget-capped** tier (`fusion-flash` slug not verified; use Fusion presets) |
| Rubber-duck mode | Yes | No | Yes, as a mode overlay on any tier |
| Context on failover | "Context bridge" (summarize) | Sticky sessions | **Structured handoff packet**: deterministic extraction, recent turns verbatim, code passed as file references (never summarized) |
| Reasoning traces | Capture `reasoning` / `thinking` | No | Capture and store in the ledger; pass only a short summary to a different model |
| Long-running calls | `ctx.info` heartbeat + keep-alive | Long first-token timeout | Keep-warm + **progress notifications** + **async job pattern** (hosts are not required to reset timeouts on logs) |
| 429/503 handling | Backoff with jitter, cascade | Classifier + cooldowns | Both, plus proactive quota tracking, and treating 402 and quota errors as "exhausted" (no retry) |
| Session stickiness | No | Yes | Yes (needed for prompt caching and consistency) |
| Learning | No | EMA feedback | EMA feedback + **savings ledger** (dollars saved, the main selling feature) |
| Image generation | `POST /api/v1/images` tool | No | Kept, with model chosen dynamically from `/api/v1/images/models` |
| Slash commands | `.md` command files | No | `.md` command files **and** MCP *prompts* (many hosts show these as slash commands) |
| Config | `hypermesh_rules.json`, hot-swap | YAML | YAML with a published JSON Schema (editor autocomplete) and hot reload |
| Passing files | `context_files` as **contents** through the host | Same | Pass **paths**. The server reads them itself, so no paid host tokens are spent carrying files. |
| Install | Not covered | `uv` native | Installer + first-run wizard + `doctor` + itch.io app updates |
| Commercial | No | No | Editions, packaging, licensing, ToS guardrails |

**Keep from HMR:** verification gate, rubber duck, Fusion tier, image tool, keep-warm, backoff with jitter, `num_ctx` enforcement, reasoning capture, context bridge, slash commands, hot-swappable rules, attribution headers.
**Keep from v2:** gateway for transparent failover, dynamic catalog, quality floors, agentic floor, hardware profiles, sticky sessions, feedback, CPU-only classifier, Anthropic `/v1/messages` endpoint.
**New in v3:** verify-then-escalate ladder, path-based file passing, async jobs, KV-budget math, savings ledger, curated catalog updates, control center, editions.

---

## 2. Architecture: one brain, two engines, one control center

```
                 ┌──────────────────────── IDEs / agents ────────────────────────┐
                 │ Cline · Kilo · Roo · Continue · OpenCode · Claude Code · VS Code│
                 │ Cursor · Copilot · Trae · Claude Desktop · any MCP host         │
                 └───────────────┬───────────────────────────────┬───────────────┘
           custom base URL       │                               │  MCP (stdio / HTTP)
   (OpenAI /v1/chat/completions  │                               │
    + Anthropic /v1/messages)    ▼                               ▼
                 ┌──────────────────────────┐     ┌──────────────────────────────┐
                 │  ENGINE A: GATEWAY        │     │  ENGINE B: MCP SERVER         │
                 │  LiteLLM Proxy (native,   │     │  FastMCP: route · delegate ·  │
                 │  no Docker) + hooks       │     │  verify · duck · deliberate · │
                 │  transparent failover     │     │  image · status · handoff     │
                 └────────────┬─────────────┘     └──────────────┬───────────────┘
                              └──────────────┬───────────────────┘
                                             ▼
   ┌────────────────────────────── HEADWATERS CORE (Python) ──────────────────────────────┐
   │ Catalog (live discovery + curated catalog) · Analyzer · Hardware Guard · Wall Engine │
   │ Policy Engine · Verification Gate · Context Bridge · Sessions · Ledger · Learning    │
   │ Executor (litellm SDK for cloud, Ollama native API for local)                        │
   │ State: SQLite (WAL) shared by both engines · rules.yaml (hot reload)                 │
   └───────────────┬──────────────────────┬───────────────────────┬──────────────────────┘
                   ▼                      ▼                       ▼
            Paid APIs / OpenRouter   Free cloud (OR :free,     Ollama :11434
            (Auto, Fusion, :floor)   OpenCode free, others)    (your RTX GPU)

   CONTROL CENTER: tray icon + local dashboard (:4001) · setup wizard · doctor · CLI
```

**Why both engines:**
- The **gateway** is the only thing that can see the *host model's* 429 or 402 and switch automatically with no manual action. It works in any tool that accepts a custom base URL.
- The **MCP server** works in hosts you can't re-point (Cursor, Copilot, Claude Desktop). It provides delegation, verification, Fusion, rubber duck and images. It is also the fallback when you don't want a proxy.
- They share SQLite. A wall the gateway sees is immediately known to the MCP server, and the reverse.

**Valid deployment modes (the user picks one in the wizard):**
1. **MCP only.** Lightest. Host-model walls need a dispatcher fallback (§5.2.3).
2. **Gateway only.** Fully transparent, but no delegation or verification tools.
3. **Both (recommended).**

---

## 3. Data model (SQLite)

| Table | Purpose |
| --- | --- |
| `models` | id, tier, provider, modalities, tools_ok, json_ok, ctx, price_in/out, size_gb, arch params, source, last_seen |
| `scores` | model_id, task, seed, override, learned_ema, n_samples |
| `walls` | model_id/provider/key, kind, since, until, consecutive_count, last_error |
| `quotas` | model/provider, window, count, limit, soft_exhausted |
| `sessions` | session_key, pinned_model, tier, created, last_switch, handoff_packet_id |
| `decisions` | id, ts, surface, session, requirements_json, chosen, alternatives, floor, score, below_floor, reasons |
| `outcomes` | decision_id, status, latency_ms, tokens_in/out, cost_usd, counterfactual_usd, verify_result, escalated_to, user_feedback |
| `jobs` | job_id, status, progress, result_ref |
| `handoffs` | id, session, packet_json, created |

Enable WAL mode. Both engines open the same database. Use short transactions only.

---

## 4. Core modules

### 4.1 Analyzer
**Inputs:** messages, attachments, tools, response_format, file paths, headers.
**Deterministic pass (always on, under 5 ms):**
- `needs_vision` (image parts), `needs_tools`, `needs_json` (schema)
- `est_tokens` (tokenizer; fall back to chars/3.5 for code, chars/4 for prose)
- `languages` (fence tags, file extensions)
- `agentic` = tools present OR a large system prompt OR a known agent user-agent
- `reasoning_hint` (math, proofs, "design", "architecture", "security audit")
- `rubber_duck_hint` ("help me think", "why does", "explain my bug")

**Classifier pass (only when the deterministic pass is ambiguous):** a tiny Ollama model forced to CPU (`num_gpu: 0`) so it never evicts the main model from VRAM. It returns `{task, complexity 1–5, risk}`. Timeout 2 s, cached by hash, falls back to heuristics.

**Output:** `Requirements{hard:{vision,tools,json,min_ctx}, soft:{task,complexity,risk,agentic,languages}}`

### 4.2 Catalog
Three layers, merged in this order:
1. **Live discovery**
   - Ollama: `/api/tags`, `/api/show` (capabilities, `model_info` architecture params for KV math, context), `/api/ps` (which model is warm)
   - OpenRouter: `/api/v1/models` (modalities, supported parameters, context, pricing, `:free`), `/api/v1/images/models` (image generation), `/api/v1/key` (credit and limits)
   - OpenCode: free models from the OpenCode config/provider listing, or a user list
   - Other free providers the user adds (OpenAI-compatible)
2. **Curated catalog** (`catalog.json`, signed, versioned). Contains per-task quality scores, `agentic_tools` reliability, known free-tier limits, deprecations and renames, Ollama tag aliases, and recommended local models per GPU profile. It is fetched from your GitHub releases at startup (with an ETag) and **works offline with the last cached copy**. This is the main way to cut support load: when models change, you ship data, not a new build.
3. **User overrides** in `rules.yaml`.

Unknown models get conservative default scores, labelled "unscored" in the dashboard.

### 4.3 Hardware Guard
See §7. It answers: *Can model M run here at context C, how fast, and does loading it evict the warm model?*

### 4.4 Wall Engine
`classify(status, headers, body, provider) → {kind, retry_after, reset_at, scope}`

| kind | Typical signals | Action |
| --- | --- | --- |
| `rate_limit` | 429; `Retry-After`; `x-ratelimit-*`; Anthropic `rate_limit_error` | Cooldown until the reset time; exponential backoff with full jitter on repeats; cascade immediately if the wait is longer than a threshold (default 8 s) |
| `exhausted` | 402; OpenAI `insufficient_quota`; "credit balance is too low"; OpenRouter insufficient credits; Gemini `RESOURCE_EXHAUSTED` with a daily quota | **No retry.** Mark the tier/key exhausted until a balance check passes or the daily reset |
| `overloaded` | 503, 529, Anthropic `overloaded_error` | Short backoff (1–2 tries), then cascade |
| `context` | Context-length errors | Re-route to a model with a larger context (not a wall) |
| `other` | Anything else | Surface the error; do not mark a wall |

**`scope`:** model, provider, or key. For example, OpenRouter credits exhausted affects every paid OpenRouter model, not just one.

**Proactive checks:** poll `/api/v1/key` every N minutes and after each paid call. Keep rpm/rpd counters per free model. Treat a model as **soft-exhausted at 90%** of its known limit.

All regex patterns and limits live in the catalog and `rules.yaml`, never in code.

### 4.5 Policy Engine
**Modes:** `cost_saver` (default), `quality_first`, `local_only`, `paid_only_until_wall`.
**Overlays:** `rubber_duck` (Socratic system prompt, fast model), `premium` (Fusion), `pin`.

**Quality floor:**
`floor = base[task] + k[task]·complexity + (risk=="high" ? 10 : 0) + (agentic ? 15 : 0)`
Agentic requests also require `agentic_tools ≥ floor_agentic`.

**Score:**
`score = quality[task] − complexity_penalty + warm_bonus − latency_penalty − evict_penalty − cost_weight·cost`

**Decision ladder (cost_saver):**
```
0. Overrides: pin > force_* > mode
1. Hard filter: requirements ∩ availability (walls/quotas) ∩ hardware fit ∩ mode
2. CHEAP RUNG  = best of {local, free_cloud} with score ≥ floor
                 (local wins ties: no quota burn, private, unlimited)
   OPTIMISTIC  = if no cheap model clears the floor but one is within margin δ (default 8)
                 AND the task is verifiable (code/JSON) → try it with the verification gate
3. PAID RUNG   = user's paid primary (optional :floor variant) → openrouter/auto
4. If paid is walled → best-effort cheap (below_floor=true, verification gate mandatory)
                 → openrouter/free only if enabled AND complexity ≤ 2
5. Nothing at all → structured error with reset ETAs and a suggestion (split the task / wait / pull model X)
PREMIUM (Fusion) is never automatic: only via force flag or explicit command, and budget-capped.
```

**Session rules:**
- Choose the tier at session start, or when the context crosses a threshold.
- Switch mid-session only on a wall, or after repeated verification failures.
- Every switch writes a handoff packet (§4.7).

### 4.6 Verification Gate (verify, then escalate)
The gate is what makes aggressive downgrading safe.

| Output type | Checks (cheap → expensive) |
| --- | --- |
| Code / diffs | tree-sitter parse for the detected language (Python can also use `ast.parse`); placeholder/laziness scan (`TODO`, `... rest of code`, `// existing code`, truncated fences); `git apply --check` for diffs; optional project command from `.headwaters.yaml` (lint / type-check / a test subset) with a timeout |
| JSON | Parse + JSON Schema validation |
| Tool calls | Valid tool name, args match the schema |
| Prose | Length/completeness heuristics; optional self-check by a cheap model (off by default) |

**On failure:** escalate one rung, carrying the failure reason as context ("previous draft failed: SyntaxError line 42"). Limits: at most `max_escalations` (default 2), and the remaining paid budget. Record every pass or fail per (model, task) and feed it into the learned scores.

### 4.7 Context Bridge (handoff packet)
Written on every mid-session model switch, and produced on demand for host switches.

```json
{
  "goal": "...", "constraints": ["..."], "decisions": ["..."],
  "files": [{"path": "src/a.py", "sha": "…", "role": "editing"}],
  "open_items": ["..."], "last_error": "...",
  "recent_turns_verbatim": ["… last N turns …"],
  "older_summary": "… LLM summary of older turns only …",
  "reasoning_summary": "… short summary of captured reasoning traces …"
}
```

**Rules:**
- Code is never summarized. Pass file paths and hashes, and re-read the files.
- Extract deterministically first (tool calls, file paths, errors).
- The LLM summarizes only older turns, using a model whose context fits them.
- Do not send another model's raw reasoning tokens to a different model.

### 4.8 Executor
- **Cloud:** `litellm` SDK (client library) with OpenRouter attribution headers set to **your product's** URL and name.
- **Local:** Ollama native `/api/chat` with computed `num_ctx`, `keep_alive`, and `format` (JSON schema) when needed. The first-token timeout is longer when the model is cold.
- **Reasoning:** request it using the provider's current reasoning parameter (verify the OpenRouter field name). Capture `reasoning` / `thinking` separately from `content`.
- **Streaming failover (gateway):** fail over only before the first token. After that, send a clean terminal error event.

### 4.9 Ledger and Learning
- Each outcome stores the actual cost and a **counterfactual cost**: what the user's paid primary would have charged for the same tokens.
- The dashboard shows *"Saved $X this week · Y% of requests served free/local · Z verification catches"*. **This is the screenshot that sells the product.**
- Learned score = EMA over (verification pass, user thumbs, quick re-ask = implicit failure).

---

## 5. Surfaces

### 5.1 Engine A: Gateway
- LiteLLM Proxy, run natively (`uv`, pinned version), no Docker, Postgres or Redis. Headwaters logic plugs in through custom hooks / router. **First verify** that the current hook API can rewrite the model before the call and retry before the first token. Otherwise use a thin FastAPI gateway that still uses the litellm SDK.
- Endpoints: `/v1/chat/completions`, `/v1/models`, `/v1/messages` (Anthropic, for Claude Code).
- Virtual models:
  - `headwaters/auto`
  - `headwaters/local`
  - `headwaters/quality`
  - `headwaters/verified` (buffers the response, runs the gate, then streams)
  - `headwaters/duck`
- An alias table maps whatever model names the IDE sends. Explicit models pass through with wall failover, unless the request carries the header `x-headwaters-pin: true`.
- Response headers: `x-headwaters-model`, `x-headwaters-tier`, `x-headwaters-reason`.
- Session key: `x-session-id` header if present, else hash(system prompt + first user message).

### 5.2 Engine B: MCP server (FastMCP, stdio + optional streamable HTTP)

#### 5.2.1 Tools
| Tool | Key parameters | Returns |
| --- | --- | --- |
| `route` | `task`, `files?: [path]`, `images?`, `requirements?`, `overrides?{force: paid\|free\|local\|premium\|duck, pin_model}`, `output: answer\|diff`, `async?` | `{answer, diffs[], commands[], model, tier, verified, below_floor, reasons[], cost, saved}` or `{job_id}` |
| `delegate` | Same as `route`, but restricted to free/local | Same |
| `verify` | `content` or `diff`, `language?` | Gate report |
| `rubber_duck` | `problem`, `files?` | Socratic questions / hints |
| `deliberate` | `question`, `preset: quality\|budget`, `max_usd` | Fusion result (opt-in, budget-capped) |
| `generate_image` | `prompt`, `aspect_ratio`, `model?` | Saved file path + cost |
| `recommend_model` | `task summary`, `flags` | Ranked list + the exact command to switch the host |
| `should_switch_host` | `current_model` | Proactive warning + recommendation + handoff packet id |
| `handoff` | `session?` | Handoff packet (to paste into the new host or model) |
| `quota_status` / `savings_report` | – | Tables |
| `list_local_models` | – | Fit / context / speed per model on this GPU |
| `set_mode` / `pin_model` | – | Confirmation |
| `job_status` / `job_result` | `job_id` | Progress / result |

#### 5.2.2 MCP protocol details
- **Files by path:** the server reads files from the workspace (MCP roots, or a configured root), so paid host tokens never carry file contents. Results come back as unified diffs, not whole files.
- **Progress:** send progress notifications during cold loads, drafts and escalations, plus log messages. Don't rely on logs alone to keep a host from timing out.
- **Async:** if the expected duration is above a threshold (cold load, Fusion, escalation), return a `job_id` right away. The host polls `job_status`.
- **Elicitation** (where the host supports it): confirm premium or paid spend above a threshold. Otherwise require the force flag.
- **Prompts primitive:** `/headwaters-route`, `/headwaters-duck`, `/headwaters-deliberate`, `/headwaters-status`, `/headwaters-handoff`. Also ship `.md` command files for hosts that read command folders.

#### 5.2.3 Dispatcher fallback (MCP-only mode)
- A rules pack (`AGENTS.md`, `.clinerules`, Kilo/Roo/Continue rules) tells the host model: *"For implementation work call `route`; apply returned diffs exactly; don't do the work yourself."*
- Dispatcher ladder: the free cloud model with the best `agentic_tools` score → a local dispatcher model (always available, never rate-limited).
- Hosts with a fallback-model setting switch automatically. Others need one manual switch, and `should_switch_host` warns before the wall.

### 5.3 Control Center
- Tray icon (green / amber / red = all tiers ok / paid walled / everything except local walled).
- Dashboard: live decisions, walls with reset ETAs, savings, model fit on this GPU, verification catches, thumbs up/down.
- **Setup wizard:**
  1. Detect the GPU, Ollama and its environment settings
  2. Recommend local models for the GPU and pull them with one click
  3. Ask which IDEs you use and write or show their config snippets
  4. Optional keys
  5. Pick a mode
  6. Run a self-test
- `headwaters doctor`: checks Ollama env (`OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_MAX_LOADED_MODELS`), versions, port conflicts and key validity, and exports a redacted diagnostic bundle for support.

### 5.4 CLI
`headwaters start|stop|status|pick|models refresh|mode|pin|feedback|doctor|savings|handoff`

---

## 6. Tier map

| Tier | Members | Notes |
| --- | --- | --- |
| Premium (opt-in) | `openrouter/fusion` (Quality/Budget presets) | Costs the sum of panel calls plus a judge, and takes longer. Never automatic. Budget cap per call. |
| Paid primary | User's model(s), optional `:floor` price-sort variant | If you use `:floor`, also set provider quantization filters so you don't land on low-precision endpoints (verify the field) |
| Paid smart | `openrouter/auto` | Treated as one candidate; never reimplemented |
| Free cloud | OpenRouter `:free`, OpenCode free models, user-added free endpoints | **Ranked by the catalog**, not a fixed order |
| Local | Ollama models that fit this GPU | Wins ties with free cloud |
| Last resort | `openrouter/free` | Off by default; complexity ≤ 2 only |

---

## 7. Hardware Guard (RTX 4080 laptop 12 GB / RTX 5080 desktop 16 GB)

### 7.1 Budget math
```
budget      = VRAM_total − reserve (1.5 GB default; more on laptops driving a display)
kv_bytes    = 2 · n_layers · n_kv_heads · head_dim · ctx · bytes_per_elem
              (bytes_per_elem: f16=2, q8_0≈1, q4_0≈0.5; params from /api/show model_info)
fits(M, ctx)= weights(M) + kv_bytes(ctx) + overhead(~0.5–1 GB) ≤ budget
num_ctx(M)  = largest ctx in {8k,16k,24k,32k,48k,64k} that fits and is ≥ 1.2 × est_tokens
```
If nothing fits, classify the model as `offload` (slow, large latency penalty) or `no_fit` (excluded).

### 7.2 Why the HMR "15,000 characters" rule is replaced
Worked example for Qwen2.5-Coder-14B (48 layers, 8 KV heads, head_dim 128; Q4_K_M weights ≈ 9 GB):
- KV per token at f16 ≈ 2·48·8·128·2 B ≈ 0.19 MB. At q8_0 it is ≈ 0.1 MB.
- **12 GB laptop (budget ≈ 10.5 GB):** with q8 KV, about 12–14k tokens fit. That is roughly **45k characters**, so the 15k-char rule sends too much to the cloud. But HMR's fixed `num_ctx: 32768` would **not** fit, and would spill to CPU.
- **16 GB desktop (budget ≈ 14.5 GB):** 32k context with q8 KV fits (≈ 9 + 3.2 + overhead).

Only computed budgets get both cases right. Treat these numbers as estimates and calibrate them against `/api/ps` actual sizes on first run.

### 7.3 Residency policy
- **12 GB:** `OLLAMA_MAX_LOADED_MODELS=1`. Strongly prefer the warm model (warm bonus + eviction penalty). The classifier stays on CPU. Vision requests that need a different model are allowed but carry an eviction cost.
- **16 GB:** one large model, or a mid-size model plus a small vision model if both fit.
- **Keep-warm:** `keep_alive` (default 30 m) on every call. An optional background ping keeps the "primary local" model loaded. Pause it when the GPU is busy with other work (an optional check of `nvidia-smi` utilization).

### 7.4 Recommended local models
These ship in the curated catalog, not in code, because local model releases change monthly. Recommend by role: agentic coder, general/reasoning, vision, classifier. Pick for each GPU profile using the fit math above. Example seeds at the time of writing: Qwen2.5/3 Coder 14B-class, gpt-oss-20b, Devstral Small, Gemma 3 12B, Qwen2.5-VL 7B. Verify the exact Ollama tags, for example `qwen2.5vl:7b`.

---

## 8. Configuration: `rules.yaml` (hot reload, JSON Schema published)

```yaml
version: 1
mode: cost_saver              # cost_saver | quality_first | local_only | paid_only_until_wall
budget:
  paid_daily_usd: 5.00
  premium_max_usd_per_call: 1.00
  confirm_above_usd: 0.25     # elicitation / force flag required above this
tiers:
  paid:
    models: ["<your paid model slug>"]
    use_floor_variant: false
  openrouter_auto: { enabled: true }
  premium:
    fusion: { enabled: true, preset: quality }
  free_cloud:
    ranking: catalog          # never a fixed list
    opencode_free: []         # auto-discovered or user-listed
  openrouter_free_router: { enabled: false, max_complexity: 2 }
  local:
    host: http://localhost:11434
    profile: auto             # auto | laptop_12gb | desktop_16gb | custom
    reserve_gb: 1.5
    kv_cache_type: q8_0
    keep_alive: 30m
    keep_warm_model: auto
    classifier: { model: auto_small, num_gpu: 0, timeout_s: 2 }
floors:
  code:      { base: 70, per_complexity: 5 }
  reasoning: { base: 70, per_complexity: 6 }
  chat:      { base: 50, per_complexity: 3 }
  summarize: { base: 50, per_complexity: 3 }
  extract:   { base: 55, per_complexity: 3 }
  vision:    { base: 60, per_complexity: 4 }
  agentic_bonus: 15
  high_risk_bonus: 10
  optimistic_margin: 8
verify:
  enabled: true
  max_escalations: 2
  project_checks: auto        # read from .headwaters.yaml in the repo
walls:
  cascade_if_wait_over_s: 8
  soft_exhaust_pct: 90
bridge:
  verbatim_turns: 6
sessions:
  switch_on: [wall, verify_failures]
gateway: { enabled: true, port: 4000 }
mcp:     { transport: stdio, async_threshold_s: 20, roots: auto }
attribution: { referer: "https://<your-product-site>", title: "Headwaters" }
telemetry: false
catalog: { auto_update: true, url: "https://github.com/<you>/headwaters-catalog/releases/latest" }
```

---

## 9. IDE integration matrix

| Host | Engine | Automatic host failover? | Notes |
| --- | --- | --- | --- |
| Cline / Kilo / Roo | Gateway + MCP | Yes | OpenAI-compatible base URL; needs a large `num_ctx` |
| Continue | Gateway + MCP | Yes | Separate autocomplete model → small local model |
| OpenCode | Gateway + MCP | Yes | Custom provider `baseURL`. Also `recommend_model` for choosing an OpenCode free model. |
| Claude Code | Gateway (`ANTHROPIC_BASE_URL`) + MCP | Yes, with API keys | Do not route subscription tokens |
| VS Code Copilot Chat | MCP (+ BYOK endpoint if available) | Partial | Copilot's own models aren't interceptable |
| Cursor | MCP | No (MCP delegation only) | The base-URL override is called from Cursor's servers, so localhost needs a tunnel. Not recommended. |
| Trae | MCP (+ custom endpoint if your version has one) | Partial | Version-dependent |
| Claude Desktop / other MCP hosts | MCP | No | Delegation, verify, image, deliberate |
| Devin / cloud agents | – | No | Out of scope |

The wizard generates the exact snippet for each host (base URL, model name `headwaters/auto`, MCP JSON entry, rules file).

---

## 10. Commercial design for itch.io

### 10.1 Editions
| | **Headwaters Lite** (free / pay-what-you-want) | **Headwaters Pro** (one-time, e.g. $19–29) |
| --- | --- | --- |
| MCP server (route, delegate, status, local + free routing, wall failover) | ✅ | ✅ |
| `local_only` mode, hardware guard, keep-warm | ✅ | ✅ |
| CLI + doctor | ✅ | ✅ |
| Gateway (transparent host failover, Anthropic endpoint) | – | ✅ |
| Verification gate with project checks, verify-then-escalate | Basic (syntax + placeholders) | Full |
| Context bridge / handoff, sticky sessions | – | ✅ |
| Control Center (tray, dashboard, savings ledger, wizard) | Status page only | ✅ |
| Learning (EMA), premium Fusion tier, image tool | – | ✅ |
| Curated catalog updates | ✅ (funnel + trust) | ✅ |

**Why this split:** Lite proves value (it routes, survives walls, runs locally) and gets you into r/LocalLLaMA and Cline/Kilo Discord communities. Pro sells the automation and the "$ saved" dashboard. Consider publishing Lite's core as MIT open source on GitHub for discovery and trust, and keeping Pro closed.

### 10.2 Distribution and licensing on itch.io
- Page type: **Tool**. Upload builds with **butler** on channels `windows`, `linux`, `mac`. Buyers get updates through the itch app.
- **Licensing: keep it light.** On itch, paid files can only be downloaded after purchase, so ship Pro as its own download. Do **not** embed an itch API key in the client to verify purchases (it would leak). If you later want license checks, use a tiny server you control, or signed offline license files. Your audience dislikes DRM, so don't overinvest.
- **Packaging:** Windows first (most RTX laptop and desktop buyers). Two options:
  - (a) a `uv`-bootstrap installer that sets up a pinned environment
  - (b) a frozen build (Nuitka/PyInstaller)

  Option (a) is easier to maintain with LiteLLM's dependencies. Either way, the first-run wizard does the rest.
- **Don't bundle model weights.** The wizard pulls them through Ollama, under each model's own license.
- **Pricing tactics:** launch discount, a bundle with "catalog updates for 12 months" messaging (honor system), and a demo GIF of the savings counter. Itch audiences skew toward games, so also cross-post the page to dev communities. Gumroad or Lemon Squeezy can be added later without changing the product.

### 10.3 Support-cost reducers
- Model churn goes into the **catalog**, not code releases.
- `doctor` + a redacted diagnostic bundle.
- A per-IDE setup page generated from the same templates the wizard uses.
- Pinned LiteLLM version, with an upgrade test suite before you bump it.

---

## 11. Legal, ToS and trust guardrails (must-haves for selling)

1. **Name:** "HyperMesh" is an established Altair engineering software brand, and the spec also mixes in "HydraMesh". Rename before selling. "Headwaters" is a working name only. Run a trademark search on whatever you choose.
2. **Bring your own keys only.** Never resell, pool or proxy other people's keys.
3. **No rate-limit evasion.** Do not rotate multiple accounts or keys to get around free-tier limits. It violates provider terms and could get the listing pulled. The router *respects* limits and routes around them.
4. **Subscriptions:** don't route consumer subscription credentials (for example Claude or Copilot plans) through the gateway. Support API keys only.
5. **Privacy:** no telemetry by default, all state local, secrets in the OS keychain (`keyring`), logs redacted. Make "prompts never leave your machine in local mode" a headline feature.
6. **OSS licenses:** include a third-party notices file (LiteLLM core MIT, but don't use code from its enterprise directory; MCP SDK; tree-sitter; etc.).
7. **Attribution headers:** use your product's URL and title. This is free marketing in OpenRouter's app rankings.
8. **Employment:** if you have an employer, check your outside-activity and IP agreements, and build it on personal time and equipment.

---

## 12. Roadmap

| Milestone | Scope | Ship as |
| --- | --- | --- |
| M1 Core | Catalog (live + seed), analyzer, hardware guard, wall engine, policy, executor, SQLite, CLI, tests | Internal |
| M2 MCP | `route`/`delegate`/status tools, files-by-path, progress + async jobs, basic verify, rules packs, prompts/commands | **Lite 0.1 on itch** |
| M3 Gateway | LiteLLM hooks, failover before first token, sessions, Anthropic endpoint, virtual models | Pro beta |
| M4 Quality | Full verification gate, verify-then-escalate, handoff packets, ledger | Pro 1.0 |
| M5 Control Center | Tray, dashboard, wizard, doctor bundle | Pro 1.0 |
| M6 Extras | Fusion tier, image tool, learning, rubber duck, speculative drafting (experimental) | Pro 1.x |

---

## 13. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Provider error formats and limits change | Patterns and limits live in the catalog; fixture tests; catalog hot update |
| LiteLLM hook API changes | Pin the version; adapter layer; fallback thin FastAPI gateway |
| Weak models damage repos in agent mode | Agentic floor + verification gate + diffs instead of direct writes |
| Cold-load timeouts | Keep-warm, progress notifications, async jobs, warm bonus |
| VRAM contention with games or other apps | Calibrate against `/api/ps`; optional GPU-busy check; `local` tier degrades to `offload` |
| Seed scores are wrong | Verification-based learning; easy overrides; catalog updates |
| Support load from varied setups | Wizard, doctor, diagnostic bundle, generated per-IDE docs |
| Competition (OpenRouter Auto, LiteLLM alone, claude-code-router, RouteLLM, others) | Differentiate on local-hardware awareness, wall cascades, verification and the savings ledger, and one installer for every IDE |

---

## 14. External facts to verify before coding

- LiteLLM: current custom hook / router API (rewrite before the call, observe failures, retry before the first streamed token)
- OpenRouter: Fusion slug, presets and plugin fields; whether a `fusion-flash` slug exists; the reasoning request parameter; `:floor` behaviour and provider quantization filter; `/api/v1/key` fields; current free-tier limits
- Ollama: `/api/show` `model_info` keys for KV math; current vision model tags
- OpenCode: how free models are listed in config/providers; custom provider `baseURL` format
- MCP: progress notification and timeout behaviour per host; elicitation support per host; prompts-as-slash-commands support per host
- itch.io: current butler workflow, revenue-share settings, tax handling
