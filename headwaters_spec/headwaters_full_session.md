# Smart Fallback Router / Headwaters: Full Session Transcript

> **Rename note:** The product formerly called "Spillway" is now **Headwaters**. Every name has been updated: product name, picker models (`headwaters/auto`, `/free`, `/local`, `/cloud`), MCP tools (`headwaters_do`, …), CLI (`headwaters`), config files (`.headwaters.yaml`, `.headwatersignore`), headers (`x-headwaters-*`), env vars (`HEADWATERS_HOME`), resource URIs (`headwaters://`), packages (`headwaters`, `headwaters_pro`), the benchmark (HeadwatersBench) and referenced file names (`headwaters_*.md`). Ordinary uses of the word "spill" (e.g. VRAM spilling into system RAM) are unchanged.


This file contains the full conversation (questions and answers, with all tables), followed by every design document produced during the session as appendices.

**Contents**
1. Turn 1: Smart Fallback Router design and implementation plan
2. Turn 2: Any IDE, RTX 4080 laptop / RTX 5080 desktop, automatic switching
3. Turn 3: Does LiteLLM Proxy require a container?
4. Turn 4: MCP-only design catches walls itself
5. Turn 5: Comparison with the HyperMesh Router spec and the ultimate design
6. Turn 6: Open source, Windows first, pricing
7. Turn 7: GitHub Copilot use case, and proxy vs. MCP roles
8. Turn 8: Do other VS Code extensions fall back to a smart list of Ollama models?
9. Turn 9: Scopes (auto / free / local / cloud), Private Mode, competitor comparison
10. Turn 10: Build our own MCP server instead of using llm-router
11. Appendices: all generated design files

---

## Turn 1: Smart Fallback Router design and implementation plan

### Assistant

# Smart Fallback Router: Design and Implementation Plan

## 0. Constraints that shape the design

**1. An MCP server cannot catch a rate-limit or out-of-credit wall by itself.**
MCP tools only run when the host's current model decides to call them. By the time a paid model returns 429 or 402, the host has already failed and nothing calls your MCP server. So MCP alone can't do automatic failover inside the main chat loop. It can:
- advise which model to use next (*"what should I switch to?"*)
- delegate subtasks to free or local models while the main model is still working (this is how you cut paid usage)
- track quotas and walls when told about them or when it checks them itself

Automatic interception needs a hook in the request path. That means a proxy (which caused you problems), a **host plugin**, or a wrapper.

**2. OpenCode has a plugin system.** As far as I know, OpenCode has no built-in auto router. It does have JS/TS plugins that can listen to session and error events and hook into chat parameters. That is the clean "in-path" hook you want, and it avoids the proxy. Check the current plugin hook names against the OpenCode docs, because they change.

**3. `openrouter/free` should be a last-resort fallback, not something you rely on.** It picks a free model that supports the request's features, but it doesn't route by quality. You already suspected this. The better approach is to pull the free model list from OpenRouter's `/api/v1/models` (filter for `:free`) and rank those models yourself using the same quality matrix you use for local models.

**Recommended approach:** one routing core, used by three thin adapters:

| Adapter | Purpose | Automatic? |
| --- | --- | --- |
| **MCP server** | Advise the next model, delegate subtasks to free/local models, report quota status | Advisory or agent-driven |
| **OpenCode plugin** | Detect walls from error events and switch or suggest the model for the session | Yes (in-path) |
| **CLI (`routectl`)** | "What should I use for this prompt?" and quota dashboard; works with any tool | Manual |
| *(Optional, later)* minimal proxy | Only if you need transparent failover in tools with no plugin support | Yes |

---

## 1. Architecture

```
            ┌─────────────────────────────────────────────┐
            │                ROUTER CORE                   │
            │  ┌──────────────┐  ┌──────────────────────┐ │
 prompt ───▶│  │ Prompt       │  │ Capability Registry   │ │
            │  │ Analyzer     │  │ (paid / free cloud /  │ │
            │  │ (heuristics +│  │  OpenCode free /      │ │
            │  │  tiny local  │  │  Ollama local)        │ │
            │  │  classifier) │  └──────────────────────┘ │
            │  └──────┬───────┘  ┌──────────────────────┐ │
            │         ▼          │ Quota/Wall Tracker    │ │
            │  ┌──────────────┐  │ (429/402 state,       │ │
            │  │ Scoring &    │◀─│  cooldowns, daily     │ │
            │  │ Policy Engine│  │  counters, key usage) │ │
            │  └──────┬───────┘  └──────────────────────┘ │
            │         ▼          ┌──────────────────────┐ │
            │   RouteDecision    │ Feedback Store        │ │
            │  (model, tier, why)│ (thumbs/retries →     │ │
            │                    │  quality adjustments) │ │
            └─────────┬──────────┴──────────────────────┘─┘
         ┌────────────┼──────────────┬──────────────┐
         ▼            ▼              ▼              ▼
     MCP server   OpenCode plugin   CLI        (opt.) proxy
```

All state lives in SQLite (for example `~/.smartroute/state.db`), so every adapter shares it.

---

## 2. Components

### 2.1 Prompt Analyzer: "what does this request need?"
This happens in two passes.

**Pass A: deterministic heuristics (free and instant)**
- `needs_vision`: image parts or attachments are present
- `needs_tools`: tools/functions are defined, or it's an agent session
- `needs_json`: a response_format or schema is present
- `est_input_tokens`: tokenizer estimate, used to filter by context window
- `has_code`: code fences, file diffs, language keywords
- `needs_reasoning_hint`: math, proofs, multi-step planning keywords

**Pass B: tiny local classifier (only if heuristics are ambiguous)**
- Use Ollama structured output with a small model (for example a 1–3B Qwen, Gemma or Llama) to return:
```json
{"task":"code|reasoning|chat|summarize|extract|creative|vision",
 "complexity":1-5, "risk":"low|med|high", "domain":"..."}
```
- Cache the result by prompt hash.

The output is a `Requirements` object with hard constraints (vision, tools, context length) and soft ones (task, complexity).

### 2.2 Capability Registry: "what can each model do?"
| Source | How it's discovered |
| --- | --- |
| **Ollama local** | `GET /api/tags` lists models. `POST /api/show` returns `capabilities` (`completion`, `vision`, `tools`, `thinking`, `embedding`) and context length. `GET /api/ps` shows what's loaded (warm models are faster). Compare VRAM/RAM against model size. |
| **OpenRouter** | `GET /api/v1/models` returns modalities, supported parameters (tools), context and pricing. Free models end in `:free`. |
| **OpenCode free models** | Read the OpenCode config and provider list, or keep a user-maintained list (these change often). |
| **Paid primary** | Whatever the user configured. Marked `tier=paid`. |

Each model gets a **quality matrix row**: a score from 0 to 100 per task type, seeded from a shipped `quality_seed.yaml` (based on public benchmarks and editable by the user) and adjusted over time by feedback.

### 2.3 Quota and Wall Tracker
**Reactive detection (normalize every provider's error into a `WallEvent`):**

| Signal | Meaning |
| --- | --- |
| HTTP 429 plus a `Retry-After` or `x-ratelimit-reset*` header | Rate limit. Set a cooldown until the reset time. |
| HTTP 402, or OpenRouter "insufficient credits" | Out of credit. Mark the tier exhausted until the user resets it or a balance check passes. |
| OpenAI `insufficient_quota` | Out of credit (not a transient error) |
| Anthropic "credit balance is too low" or `rate_limit_error` / `overloaded_error` | Credit wall, or a rate limit / overload |
| Gemini `RESOURCE_EXHAUSTED` | Quota wall |
| OpenCode error events with quota or limit text | Same handling, matched by regex |

**Proactive detection (avoid hitting the wall at all):**
- Check OpenRouter's `GET /api/v1/key` for usage, limit and remaining credit.
- Keep local counters for free-tier limits (requests per minute and per day). Put the limits in config, because providers change them.
- Mark a model "soft-exhausted" once it reaches 90% of its known limit.

Distinguish a **rate limit** (wait it out or route around it briefly) from **exhaustion** (route around it until reset). Use exponential cooldown for repeated 429s.

### 2.4 Scoring and Policy Engine
```
candidates = all models
  → filter: hard requirements (vision, tools, context ≥ est_tokens*1.2, json)
  → filter: availability (not walled, not cooling down, local fits in memory)
  → filter: policy mode (local_only → only Ollama)
score(m) = quality[m][task] 
         − complexity_penalty(m, complexity)
         + warm_bonus (local model already loaded)
         − latency_penalty
         − cost_weight * cost(m)
quality_floor = f(task, complexity, risk)   # e.g. code/5/high → 85
```

**Decision tree (the core of what you described):**
```
1. If mode == local_only        → best local ≥ floor, else best local + warning
2. Downgrade check (save money even when paid works):
     best_free_cloud ≥ floor?   → use free cloud
     best_local ≥ floor AND local ≥ best_free_cloud? → use local
     else                        → stay on paid
3. Paid walled?
     → OpenRouter Auto available (user has credits)? → suggest/use it
     → free cloud ranked by OUR matrix (OpenRouter :free / OpenCode free)
     → local Ollama
     → openrouter/free ONLY if user enabled it AND complexity ≤ 2
4. Nothing ≥ floor?             → pick best anyway, flag "below quality floor",
                                  suggest splitting the task or waiting (show reset ETA)
```
The router doesn't recreate OpenRouter Auto. It treats Auto as a single "smart paid" option and only ever routes *around* it.

**Policy modes:** `cost_saver` (default), `quality_first`, `local_only`, and `paid_only_until_wall`.

### 2.5 Feedback loop
- Record each decision, the outcome (success, error, user retry, user thumbs up/down), and latency.
- If a user re-asks right after a local or free answer, count that as an implicit failure for that (model, task) pair.
- Adjust `quality[m][task]` slowly with an exponential moving average (EMA). Add an optional "shadow eval" mode that occasionally compares a local answer against a paid answer.

---

## 3. Adapter details

### 3.1 MCP server tools
| Tool | Input | Output |
| --- | --- | --- |
| `recommend_model` | prompt summary, has_images, needs_tools, est_tokens, current_model | ranked list, the chosen model, reason, quality floor met or not |
| `delegate` | prompt, optional images, requirements | runs it on the best free or local model and returns the answer, model used, and confidence |
| `report_wall` | model, error text or status | normalized WallEvent, updated state |
| `quota_status` | – | table of each model's status (ok, cooldown with ETA, exhausted) |
| `list_local_models` | – | Ollama models with their capabilities and fit |
| `set_mode` | mode | confirmation |

**Where most of the savings come from:** `delegate` lets your paid main agent hand off cheap subtasks (summarizing a file, writing tests, renaming, extracting data, describing an image) to free or local models. Add an instruction to AGENTS.md or the system prompt along the lines of *"Before doing low-complexity subtasks, call `delegate`."*

### 3.2 OpenCode plugin
- Subscribe to session error events and classify them with the shared wall detector.
- When a wall is detected: call the core, get a `RouteDecision`, then either switch the session's model (if the plugin API allows it) or show a toast/message with the recommended model and the exact `/model` command.
- Before a request: optionally run the downgrade check and suggest *"This task can run on qwen-coder locally (score 82 ≥ floor 75). Switch?"* Keep this suggest-only by default, so it doesn't annoy you.
- This covers your use case where OpenCode Go runs out and you don't know which free model to pick.

### 3.3 CLI
- `routectl pick "refactor this module" --tools --tokens 30000` prints the recommendation.
- `routectl status` shows the quota dashboard.
- `routectl models refresh` re-runs discovery.

---

## 4. Config example (`~/.smartroute/config.yaml`)
```yaml
mode: cost_saver
allow_openrouter_free_router: false
providers:
  paid:      [{id: anthropic/claude-sonnet, tier: paid}]
  openrouter: {api_key_env: OPENROUTER_API_KEY, use_auto: true, use_free_models: true}
  opencode_free: [ {id: "opencode/<free-model>", limits: {rpd: null}} ]
  ollama: {host: http://localhost:11434, max_vram_gb: 16}
quality_floors:
  code:      {base: 70, per_complexity: 5}
  reasoning: {base: 70, per_complexity: 6}
  chat:      {base: 50, per_complexity: 3}
free_limits: {openrouter_free: {rpm: 20, rpd: 50}}   # verify current values
```

## 5. Tech stack
- **Core and MCP server:** TypeScript, using `@modelcontextprotocol/sdk`, because the OpenCode plugin is also TS and can import the core directly. Python with FastMCP also works if you don't need the plugin.
- **State:** SQLite (`better-sqlite3`).
- **Tokenizer estimate:** `gpt-tokenizer` or a chars/4 fallback.
- **Local calls:** the Ollama native API (which avoids the OpenAI-compat quirks you ran into).

## 6. Phases
1. **Core:** registry discovery, wall classifier with unit tests on real error samples, scoring, decision tree, SQLite, CLI.
2. **MCP server:** `recommend_model`, `quota_status`, `delegate`, `report_wall`.
3. **OpenCode plugin:** wall detection, then suggest or switch.
4. **Feedback learning,** shadow evals, and a small web dashboard (optional).
5. **(Optional)** minimal proxy reusing the core, only for tools without plugins.

## 7. Risks
- **Provider error formats and free limits change.** Keep the regexes and limits in config files, not code.
- **Local models are slow to cold-start.** Add a bonus for warm models, plus optional preloading (`keep_alive`).
- **Seeded quality scores are rough.** Feedback has to correct them, and the scores should be easy to edit.
- **A plugin may not be able to switch models mid-session.** Fall back to suggest mode.

---

## 8. Implementation prompt for an AI agent

```
You are implementing "smartroute": a local-first model routing engine with an MCP server,
an OpenCode plugin, and a CLI. Goal: minimize paid LLM usage while maintaining quality by
(a) downgrading requests from paid → free cloud → local Ollama when those meet a quality
floor for the prompt's requirements, and (b) failing over when rate-limit or out-of-credit
walls are hit. Do NOT reimplement OpenRouter's Auto Router; treat "openrouter/auto" as one
paid candidate. Treat "openrouter/free" as a disabled-by-default last resort only for
complexity ≤ 2.

CONSTRAINTS
- TypeScript (Node 20+), pnpm monorepo: packages/core, packages/mcp-server,
  packages/opencode-plugin, packages/cli.
- Use @modelcontextprotocol/sdk (stdio transport) for MCP.
- Use Ollama NATIVE API (/api/tags, /api/show, /api/ps, /api/chat with format=json schema),
  not the OpenAI-compatible endpoint.
- SQLite via better-sqlite3 at ~/.smartroute/state.db; config at ~/.smartroute/config.yaml
  (validate with zod). Provide config.example.yaml.
- No network calls in unit tests; mock HTTP. Vitest for tests.
- Verify current OpenCode plugin API and OpenRouter API shapes from their docs before coding
  those parts; isolate them behind interfaces so changes are localized.

CORE (packages/core)
1. Requirements analyzer:
   - Heuristics: needs_vision (image parts), needs_tools, needs_json, est_input_tokens,
     has_code, reasoning hints.
   - Optional classifier: small Ollama model (configurable) returns
     {task: code|reasoning|chat|summarize|extract|creative|vision, complexity 1-5,
      risk low|med|high}; cache by sha256 of prompt; fall back to heuristics on failure/timeout (2s).
2. Capability registry:
   - Ollama discovery: names, capabilities from /api/show, context length, size; loaded
     models from /api/ps; mark "fits" by comparing size to configured max_vram_gb/ram.
   - OpenRouter discovery: GET /api/v1/models → modalities, supported_parameters (tools),
     context_length, pricing; tag ":free" models tier=free_cloud.
   - OpenCode free models + paid models from config (tier=opencode_free / paid).
   - Quality matrix: quality_seed.yaml (model-id glob → per-task scores 0-100), user overrides,
     learned EMA adjustments.
3. Wall detector: function classifyError({status, headers, body, provider}) →
   {kind: rate_limit|exhausted|overloaded|other, retryAfterMs?, resetAt?}.
   Cover: 429 + Retry-After / x-ratelimit-reset*, 402, OpenAI insufficient_quota,
   Anthropic credit-balance-too-low / rate_limit_error / overloaded_error, Gemini
   RESOURCE_EXHAUSTED, OpenRouter insufficient credits, and regex patterns from config.
   Include fixture-based tests for each.
4. Quota tracker: per-model state (ok|cooldown|exhausted), exponential cooldown on repeated
   429s, rpm/rpd counters for free tiers from config, proactive soft-exhaustion at 90%,
   OpenRouter GET /api/v1/key balance check.
5. Policy engine: modes cost_saver (default) | quality_first | local_only |
   paid_only_until_wall. Filter by hard requirements (vision, tools, json,
   context ≥ 1.2×tokens), availability, and mode. Score = quality[task] − complexity
   penalty + warm bonus − latency penalty − cost_weight×cost. Quality floor =
   base[task] + per_complexity×complexity (+10 if risk high).
   Decision order: local_only → downgrade check (free cloud ≥ floor, local ≥ floor and
   ≥ free) → paid if available → openrouter/auto if credits → ranked free cloud → local →
   openrouter/free if enabled → best-effort with below_floor=true and reset ETA.
   Return RouteDecision {model, tier, score, floor, below_floor, reasons[], alternatives[]}.
6. Feedback: record decisions/outcomes; implicit failure on fast retry; EMA update of
   quality per (model, task).

MCP SERVER tools: recommend_model, delegate (executes on best free/local model and returns
answer + model + score), report_wall, quota_status, list_local_models, set_mode. Clear
descriptions so host agents know to call delegate for low-complexity subtasks. Include
a sample AGENTS.md snippet.

OPENCODE PLUGIN: subscribe to session error events → classifyError → on wall, get
RouteDecision and either switch session model (if API supports) or show a message with the
recommended model and exact command. Optional pre-request downgrade suggestion
(suggest-only by default, configurable).

CLI: `routectl pick <prompt> [--image f] [--tools] [--tokens n]`, `routectl status`,
`routectl models refresh`, `routectl mode <mode>`, `routectl feedback <decisionId> up|down`.

DELIVERABLES: working code, tests (≥80% core coverage), README with setup for OpenCode,
Claude Desktop / other MCP hosts, and a troubleshooting section. Implement in phases:
core+CLI → MCP → plugin → feedback. After each phase, run tests and summarize what's done
and what assumptions were made about external APIs.
```

---

**Open questions for you:**
- Which tools do you use besides OpenCode (Claude Code, Cursor, Continue)? That decides whether the optional proxy is worth building.
- What GPU or RAM do you have for Ollama?
- Do you want automatic switching or suggestions you confirm? I defaulted to suggestions.

Also, the free-tier limits and OpenCode's free model list change often. Check them before you seed the config.

---

## Turn 2: Any IDE, RTX 4080 laptop / RTX 5080 desktop, automatic switching

### User
> I may use any IDE, vs code, cline, kilo, claude code, cursor, cortex, continue, trae, devin, others. targeting either a rtx 4080 laptop (12GB) or rtx 5080 desktop (16GB). automatic switching

### Assistant

# Smartroute v2: Automatic Switching in Any IDE

## 1. Your answers change the design

You want automatic switching, and you want it in about ten different tools. Only a local gateway (a proxy) can do that. An MCP server or an OpenCode plugin can only help after the current model has already failed. A gateway sits in the request path, so it can see a 429 or 402 before your IDE does and quietly retry on the next model.

The proxy route you tried before probably broke for known, fixable reasons. So the plan is to keep the gateway but **not write the protocol layer yourself**:

- **LiteLLM Proxy** handles the transport. It already does OpenAI- and Anthropic-format endpoints, streaming, tool-call translation, Ollama, OpenRouter, retries and cooldowns.
- **Smartroute** is your code: the routing brain, plugged into LiteLLM through its hooks.
- **MCP server** stays, but now it does two things: `delegate` (hand cheap subtasks to local models) and status/override. It no longer detects walls.
- **OpenCode plugin** is dropped. OpenCode points at the gateway like everything else.

Check LiteLLM's current hook API before building. The plan assumes a pre-call hook can rewrite `model` and that failure callbacks can be observed. If the hooks can't do this cleanly, build a thin gateway in your own code, still using LiteLLM's SDK for the provider calls.

## 2. Why your proxy attempt probably failed, and how to fix it

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Local model "forgets" things or agent loops | Ollama's default context is small and silently cuts off long prompts | Set `num_ctx` per model (32k–64k for agent tools) |
| Tool calls broken or appear as plain text | Ollama's OpenAI-compatible endpoint is shaky with streamed tool calls; weak models | Use LiteLLM's native `ollama_chat/` provider; only route agent traffic to models with tested tool calling |
| Claude Code won't work through the proxy | Claude Code speaks the Anthropic Messages API, not OpenAI's | The gateway must expose `/v1/messages` (LiteLLM does) |
| First request times out | Cold model load (10–40s) | Longer first-token timeout for local models; prefer the already-loaded model; keep models loaded |
| Streams hang or get cut off | Failover after tokens already went to the IDE | Only fail over before the first token is sent; after that, return a clean error |
| "Model not found" | Each IDE sends its own model name | One virtual model name (e.g. `smartroute/auto`) plus an alias table |

## 3. How each tool connects

| Tool | How to point it at the gateway | Notes |
| --- | --- | --- |
| **Cline / Kilo Code / Roo** | "OpenAI Compatible" base URL `http://localhost:4000/v1` | Works well. These tools use long system prompts, so they need a big context. |
| **Continue** | `apiBase` in config | Works well. Can also use separate small models for autocomplete and chat. |
| **OpenCode** | Custom provider `baseURL` | Works well. |
| **Claude Code** | `ANTHROPIC_BASE_URL=http://localhost:4000` | Needs the Anthropic endpoint. Routing works with API keys. Passing a Claude subscription through the gateway is a gray area, so check the terms. |
| **VS Code (Copilot Chat)** | Bring-your-own-key / Ollama / custom endpoint (depends on version) | Partial support. Copilot's own models never go through the gateway. |
| **Cursor** | "Override OpenAI Base URL" | Cursor sends requests from its own servers, so `localhost` doesn't work without a public tunnel. Tab completion and some agent features always use Cursor's models. Limited value. |
| **Trae** | Custom model / OpenAI-compatible, if your version has it | Check your version. |
| **Cortex** | Depends which product you mean | Probably can't be routed if it's a managed cloud agent. |
| **Devin** | Runs in the cloud | Can't be routed. |

So you get automatic switching in the open-endpoint tools: Cline, Kilo, Roo, Continue, OpenCode and Claude Code. In Cursor, Copilot and Devin you only get the MCP `delegate` savings. Those vendors' own paid usage can't be intercepted.

## 4. Routing changes for automatic mode

**1. Sticky sessions.** Agent tools resend the whole conversation every turn. Switching models every turn breaks prompt caching and consistency.
- Pick a session key: a hash of the system prompt plus the first user message, or an `x-session-id` header if the tool sends one.
- Decide whether to downgrade only at the start of a session, or when the context crosses a threshold.
- Switch mid-session only when you hit a wall. Log every switch.

**2. Agent traffic gets a higher quality bar.** If the request includes tools or a large agent system prompt, raise the quality floor and require models with proven tool calling. Small local models fail badly in agent loops, so better to show a clear "below floor" error than corrupt your repo.

**3. Failover inside one request.** Send to the first model. If you get a 429, 402 or quota error *before the first token*, mark that model as walled and immediately try the next one. The IDE only sees one slightly slower response. Add a response header `x-smartroute-model` and a short log line so you can see what happened.

**4. Downgrade order (cost_saver mode), at session start:**
1. Use free cloud if it meets the quality floor.
2. Use local if it meets the floor and scores at least as well as free cloud.
3. Otherwise stay on paid.

**5. When paid hits a wall:**
1. OpenRouter Auto (if you have credits)
2. The top free cloud models, ranked by your own scores
3. Local
4. `openrouter/free`, only if you've enabled it and the task is simple
5. Otherwise, a best-effort pick, flagged as below the quality floor

**6. Routing decisions shouldn't use the GPU.** Run a small classifier model on the GPU and it can push your main model out of VRAM, causing a reload. So:
- Use heuristics first.
- If you need a classifier, run it on the CPU (`num_gpu: 0`), or use a free cloud model to classify.

## 5. Local model suggestions for your hardware

These are approximate sizes at Q4. Check for newer releases before you set this up.

| Role | RTX 4080 laptop (12GB) | RTX 5080 desktop (16GB) |
| --- | --- | --- |
| **Agent coding (tools)** | Qwen2.5-Coder 14B or Qwen3-14B (~9GB); Qwen3-Coder-30B-A3B (a mixture-of-experts model) with part of it on CPU if you have 32GB+ RAM | Devstral Small 24B (~14GB, tight), gpt-oss-20b (~13GB, good at tool use), Qwen3-Coder-30B-A3B with part offloaded |
| **General / reasoning** | Qwen3-14B (thinking mode) | gpt-oss-20b, Qwen3-14B with long context |
| **Vision** | Gemma 3 12B or Qwen2.5-VL 7B | Mistral Small 3.x 24B (vision) or Gemma 3 12B |
| **Classifier / autocomplete** | Qwen3 1.7B or Qwen2.5-Coder 1.5B on CPU | Same |

**Ollama settings:**
- `OLLAMA_FLASH_ATTENTION=1`
- `OLLAMA_KV_CACHE_TYPE=q8_0` (roughly halves the memory used by long context)
- `OLLAMA_MAX_LOADED_MODELS=1` on the 12GB laptop
- `keep_alive` of about 30 minutes

**Hardware profiles.** Smartroute should detect the GPU (`nvidia-smi`) and load a matching profile. Each profile sets VRAM budget = total VRAM minus about 1.5GB. It also sets a context limit per model so the KV cache fits. A model only counts as "fits" if weights plus KV cache at the needed context stay under budget. Otherwise it's marked "offload (slow)" and gets a latency penalty.

## 6. Architecture

```
IDE (Cline/Kilo/Continue/OpenCode/Claude Code/…)
        │  OpenAI /v1/chat/completions   or   Anthropic /v1/messages
        ▼
┌──────────── LiteLLM Proxy :4000 ────────────┐
│ pre-call hook ──▶ SMARTROUTE CORE (Python)  │
│                   analyzer · registry ·     │
│                   wall tracker · policy ·   │
│                   session affinity · VRAM   │
│ failure hook  ──▶ classify wall → cooldown  │
│ retry loop: next candidate before 1st token │
└───────┬─────────────┬──────────────┬────────┘
     paid APIs   OpenRouter     Ollama :11434
                 (auto/:free)
Smartroute MCP server (stdio) ── delegate / status / mode / pin
Dashboard :4001 (status, decisions, feedback 👍👎)
```

## 7. Updated prompt for an AI agent

```
Build "smartroute": a local LLM gateway giving AUTOMATIC cost-minimizing routing and
wall failover for any IDE/agent that accepts a custom OpenAI or Anthropic base URL
(Cline, Kilo, Roo, Continue, OpenCode, Claude Code, VS Code BYOK), plus an MCP server.

STACK
- Python 3.11+, uv. Transport = LiteLLM Proxy (do NOT hand-write provider protocols,
  SSE, or tool-call translation). Smartroute logic plugs in via LiteLLM custom hooks /
  custom router. FIRST verify LiteLLM's current hook API can (a) rewrite model pre-call,
  (b) observe failures, (c) retry to another deployment before first streamed token.
  If not, write a thin FastAPI gateway that still uses the litellm SDK for calls.
- Expose /v1/chat/completions, /v1/models, AND Anthropic /v1/messages on :4000.
- Ollama via litellm "ollama_chat/" (native API), never Ollama's OpenAI-compat endpoint.
- State: SQLite ~/.smartroute/state.db. Config ~/.smartroute/config.yaml (pydantic).
- MCP server with the official Python SDK (FastMCP), stdio.
- pytest; mock all HTTP in unit tests.

CORE MODULES
1. analyzer: heuristics (vision parts, tools present, json schema, token estimate,
   code, reasoning hints, agentic = tools or large system prompt). Optional classifier
   {task, complexity 1-5, risk} using a tiny Ollama model forced to CPU (num_gpu=0) or a
   free cloud model; 2s timeout; cache by hash.
2. registry: Ollama /api/tags,/api/show (capabilities, context),/api/ps (warm);
   OpenRouter /api/v1/models (modalities, tools, context, pricing, ":free");
   paid + OpenCode-free models from config. quality_seed.yaml (glob → per-task 0-100,
   plus "agentic_tools" score) + overrides + learned EMA.
3. hardware: detect GPU via nvidia-smi; profiles laptop_12gb / desktop_16gb; budget =
   VRAM−1.5GB; fit = weights + KV(ctx, kv_cache_type) ≤ budget else "offload" with
   latency penalty; per-model num_ctx (≥32k for agentic if it fits); keep_alive.
   Prefer the warm model to avoid VRAM swaps (warm bonus).
4. walls: classify_error(status, headers, body, provider) → rate_limit | exhausted |
   overloaded | other with retry_after/reset_at. Cover 429+Retry-After/x-ratelimit-*,
   402, OpenAI insufficient_quota, Anthropic credit-balance/rate_limit_error/
   overloaded_error, Gemini RESOURCE_EXHAUSTED, OpenRouter insufficient credits,
   config regexes. Fixture tests for each. Quota tracker: cooldowns w/ exponential
   backoff, rpm/rpd counters, soft-exhaust at 90%, OpenRouter /api/v1/key balance.
5. sessions: key = x-session-id header or hash(system prompt + first user msg).
   Sticky model per session; downgrade decisions only at session start or context
   threshold; switch mid-session only on wall; log every switch.
6. policy: modes cost_saver(default)|quality_first|local_only|paid_only_until_wall.
   Filter hard reqs (vision, tools, json, ctx ≥ 1.2×tokens, fit) + availability + mode.
   Score = quality[task] − complexity penalty + warm bonus − latency − cost.
   Floor = base[task] + k×complexity (+10 high risk, +15 agentic; agentic requires
   agentic_tools ≥ floor). Order: downgrade check (free ≥ floor; local ≥ floor and ≥
   free) → paid → openrouter/auto → ranked free cloud → local → openrouter/free (opt-in,
   complexity ≤2) → best-effort below_floor. Return RouteDecision with reasons and
   ordered fallback list.
7. failover: try candidates in order; on wall error before first token, mark wall
   and retry next transparently; after tokens are sent, return clean error. Long
   first-token timeout for cold local loads. Add response header x-smartroute-model
   and x-smartroute-reason.
8. feedback: log decisions/outcomes/latency; implicit failure on rapid re-ask; EMA.

SURFACES
- Virtual models: "smartroute/auto", "smartroute/local", "smartroute/quality",
  plus alias table mapping IDE-sent model names; pass-through for explicit models
  (still with wall failover unless header x-smartroute-pin: true).
- MCP tools: delegate, recommend_model, quota_status, set_mode, pin_model,
  list_local_models. Sample AGENTS.md / .clinerules snippet telling agents to delegate
  low-complexity subtasks.
- CLI routectl: start, status, pick, models refresh, mode, pin, feedback, doctor
  (checks Ollama env: FLASH_ATTENTION, KV_CACHE_TYPE, MAX_LOADED_MODELS, num_ctx).
- Minimal web dashboard :4001 (status, recent decisions, 👍👎).

DOCS: README with exact setup for Cline, Kilo, Roo, Continue, OpenCode, Claude Code
(ANTHROPIC_BASE_URL), VS Code; note Cursor requires a public tunnel and only partially
routes; Devin/cloud agents unsupported. Troubleshooting table: context truncation,
tool-call failures, cold-load timeouts, model name mismatch.

PHASES: core+walls+tests → gateway+failover+sessions → hardware profiles+Ollama tuning
→ MCP+CLI → dashboard+feedback. After each phase run tests and list assumptions about
external APIs to verify.
```

## Things to know before you build

1. **A model you pick explicitly in the IDE still gets automatic failover.** Add a "pin" option for when you really want only that model.
2. **Subscription usage can't be routed.** If your paid usage is a subscription (Cursor Pro, Copilot, a Claude plan used inside its own app), the gateway only sees traffic you route through it with API keys. Plan to put paid API keys in the gateway.
3. **Weak free and local models will do real damage in agent mode.** Set a strict agent quality floor from day one. A clear error is better than a broken refactor.

---

## Turn 3: Does LiteLLM Proxy require a container?

### User
> does LiteLLM Proxy require running a container?

### Assistant

No. LiteLLM Proxy runs fine as an ordinary Python process. Docker is optional.

## Run it without a container

```bash
# with uv (fits the smartroute stack)
uv tool install "litellm[proxy]"
# or: pip install "litellm[proxy]"

litellm --config ~/.smartroute/litellm_config.yaml --port 4000
```

This works on Windows, macOS and Linux. For your setup, that means it runs natively on the 4080 laptop or the 5080 desktop, next to Ollama.

## What else you need (or don't)

| Component | Required? | Notes |
| --- | --- | --- |
| Docker | No | Only a convenience for deployment |
| Postgres | No | Only needed for LiteLLM's virtual keys, spend tracking and admin UI. Smartroute keeps its own state in SQLite, so you can skip it. |
| Redis | No | Only for sharing state across several proxy instances. One local instance doesn't need it. |
| Python 3.11+ | Yes | Install with uv and it stays isolated |

## Smartroute hooks work the same way

Custom hooks and callbacks are plain Python modules referenced from the config file. LiteLLM loads them in-process, with or without a container:

```yaml
litellm_settings:
  callbacks: smartroute.hooks.proxy_handler_instance
```

## Points for the plan

- **Auto-start:** add `routectl start` to launch the proxy, the dashboard and the MCP server together. Optionally register it as a Windows Scheduled Task or a systemd user service so it starts at login.
- **Pin versions:** LiteLLM releases very often, and its hook APIs sometimes change. Pin the version in `pyproject.toml`.
- **Install size:** the `[proxy]` extra pulls in a fair number of dependencies (FastAPI, uvicorn and others). uv keeps them separate from your other Python environments.
- **Docker as a later option:** if you ever want to run the gateway on another machine or share it, the same config works in the official container.

Add one line to the agent prompt: *"Install LiteLLM via `uv` (`litellm[proxy]`, pinned version), run it natively without Docker, Postgres or Redis, and launch it with `routectl start`."*

---

## Turn 4: MCP-only design catches walls itself

### User
> My current mcp only design does catch rate limit and out of credit by looking at error codes returned. if host fails we switch to a free cloud model to call the mcp.

### Assistant

You're right, and I put it too strongly earlier. I said MCP "can't catch walls." More precisely: an MCP server can't see errors from the host's own model calls, but it sees every error code from calls it makes itself. Your design uses that. The host model becomes a thin dispatcher, and the MCP server does the real model calls, so it can detect walls and route around them. That's a valid architecture, and it avoids the proxy problems you had.

## How your design works

```
Host (IDE) ──model A (paid)──▶ fails (429/402) ──▶ host switches to free cloud model B
                                                       │
                                     B calls MCP tool (e.g. route_and_run)
                                                       ▼
                         MCP server: analyze → pick model → call it → read error codes
                                     → on wall: mark, try next (free → local Ollama)
                                                       ▼
                                            result returned to host via B
```

Inside the MCP server, error detection and failover are fully automatic. That's the same wall classifier and policy engine from the earlier plan, so it carries over directly.

## Where it works and where it doesn't

| Part | Status | Notes |
| --- | --- | --- |
| Detecting walls on calls the MCP server makes | ✅ Solid | Real status codes and headers, full control |
| Failover across free cloud → local | ✅ Solid | Transparent inside one tool call |
| Paid → free/local downgrade while paid still works | ✅ Works | The paid host model calls `route_and_run`/`delegate` for subtasks that cheaper models can handle |
| **Switching the host from A to dispatcher B** | ⚠️ Depends on the host | Automatic only if the host has its own fallback-model setting. Otherwise you switch manually once. |
| **Dispatcher B also gets walled** | ⚠️ Gap | If B is rate-limited, nothing can call the MCP server. Use a local Ollama model (small, with good tool calling) as the last-resort dispatcher, since it can't be rate-limited. |
| **Dispatcher quality** | ⚠️ Needs care | B only needs to call tools reliably, not be smart. Pick it by tool-calling ability, not general score. |
| **IDE actions (editing files, running terminal)** | ⚠️ Main gap | The routed model inside the MCP server can't use the IDE's tools. Either return structured results (diffs, file contents, commands) for B to apply, or give the MCP server its own guarded file/command tools. |
| Token overhead | ⚠️ Moderate | Results pass through B's context. Return compact output (diffs, not whole files). |
| Works across all your IDEs | ✅ Better than the proxy | Any MCP-capable host works, including Cursor and Copilot, which the proxy couldn't reach |

That last row is a real advantage over the gateway design, so the MCP-first approach is the better primary.

## Changes to make it robust

1. **Dispatcher tiers:** free cloud model with strong tool calling → local Ollama dispatcher. On 12GB, use a small model kept loaded, or the same model you route to, so VRAM doesn't swap. Add a `dispatcher_check` tool so the host can confirm it's working.
2. **Dispatcher-mode rules file.** Add a rules file for AGENTS.md / .clinerules. It should tell the dispatcher model to call `route_and_run` for every task, apply the returned diffs or commands exactly as given, and not attempt the work itself.
3. **Structured results:** `{answer, diffs[], commands[], model_used, below_floor, reasons}`.
4. **Agentic tasks inside the MCP server (optional):** for multi-step work, the routed model runs its own tool loop. Give it a sandbox of read files, propose diffs and run allowlisted commands, and leave the final writes to the host.
5. **Check quotas proactively so you fail less often.** Use OpenRouter `/api/v1/key`, rpm/rpd counters, and soft exhaustion at 90%. Add a `should_switch_host` tool so the paid model gets warned before it hits the wall.
6. **Optional MCP sampling:** where a host supports it, the MCP server can ask the host's model to do work. Treat this as a bonus, because support varies between hosts.
7. **Keep the gateway as an optional extra** for hosts with no model fallback, if you want hands-free switching of the host model later. You don't need it now.

## Addition to the agent prompt

```
ARCHITECTURE CHANGE: MCP-first "thin dispatcher" design is primary; LiteLLM gateway is
optional/phase-later.
- The MCP server performs all real LLM calls itself (litellm SDK as client library,
  Ollama native API) and applies wall detection/failover on its own responses.
- Host model acts as dispatcher. Provide tool route_and_run(task, context_files?,
  images?, requirements?) returning {answer, diffs[], commands[], model_used, tier,
  below_floor, reasons[]}; keep payloads compact (unified diffs, not full files).
- Dispatcher tiers: configurable free-cloud dispatcher (ranked by tool-calling score)
  → local Ollama dispatcher (always available). Ship dispatcher rules files
  (AGENTS.md, .clinerules, Continue/Kilo rules) instructing "always call route_and_run;
  apply returned diffs/commands verbatim."
- Tools: route_and_run, delegate, recommend_model, should_switch_host (proactive quota
  warning), quota_status, dispatcher_check, set_mode, pin_model, list_local_models.
- Optional internal agent loop for multi-step tasks with sandboxed read/diff/allowlisted
  commands; final writes returned to host.
- Use MCP sampling opportunistically when the host supports it.
- Document per host: whether it supports automatic fallback models (automatic dispatcher
  switch) or needs a one-time manual switch.
```

**Two questions so I can tighten this:**
1. How does your host currently switch to the free model: does the host do it automatically, or do you switch by hand?
2. Do the results from the routed model come back as text for the dispatcher to act on, or does your MCP server write files itself?

---

## Turn 5: Comparison with the HyperMesh Router spec and the ultimate design

### User
> I'm not ruling out using the LiteLLM proxy, it may give distinct advantages toward my goal. but I still want to consider just an MCP server as well. Compare your current proposal to this spec attached. Then create the ultimate design(s). I'd like to sell the solution on itch.io. In the end I want the best solution to meet all my potential goals.
>
> *(Attached: HyperMesh Router (HMR) spec; included in full as Appendix A.)*

### Assistant

The best design combines both ideas: one shared Python core with two engines on top, an MCP server and a LiteLLM gateway. You don't have to choose between them. Neither one alone meets all your goals. Your spec is stronger than my earlier proposals in several places, and I've folded those parts in. I've written up the full design and a new agent prompt in two files.

## Checking your spec

Your spec references OpenRouter features that are newer than what I knew, so I looked them up:

- **Fusion exists.** You can call it with the `openrouter/fusion` model slug, and it runs a panel of models, an analyst, and a final answer in a single API call.
  - It's expensive: Fusion runs N panel calls + 1 analyst call in addition to your normal request.
  - It's slow: it can take two to three times longer than a standard model call.
  - So your "opt-in only" rule is right. I couldn't find a `fusion-flash` slug. Fusion's panel has Quality and Budget presets instead: by default the panel is the Quality preset; switch to Budget for cheaper members.
- **The image tool is valid.** OpenRouter has a dedicated image endpoint, `POST /api/v1/images`. Choose the model at runtime rather than hard-coding it: check whether the model was retired or renamed, which happens often in the image catalog.

## What to keep from each design

| Area | Your spec | My v2 | Combined design |
| --- | --- | --- | --- |
| Host model hits a wall | Not covered | Gateway handles it | Gateway switches automatically; with MCP only, a local model takes over as dispatcher |
| Model list | Fixed model names, some probably retired | Discovered live | Discovered live, plus a curated catalog you update without shipping a new build |
| Protecting your GPU | Over 15,000 characters goes to the cloud | Checks whether the model fits in VRAM | Calculates memory from token count and context size |
| Quality checks | Python syntax check + scan for placeholders like `# TODO` | Minimum quality score per task | Checks the cheap model's output in any language, and moves up a tier if it fails |
| Switching models mid-task | Summarize the conversation | Stay on one model per session | Structured handoff; code is passed as file paths and never summarized |
| Slow responses | Keep the model loaded + status messages | Longer first-response timeout | Keep the model loaded + progress updates + long tasks run in the background |
| Rubber duck, Fusion, images, slash commands, editable rules | ✅ | ❌ | ✅ kept |
| Passing files to the router | Through the IDE's paid model | Same | As paths; the server reads them itself, so paid tokens aren't spent carrying file contents |

Three fixes to your spec:

- **The 15,000-character rule is wrong in both directions.** On your 12 GB laptop, a 14B coder model fits about 45k characters, so the rule sends too much to the cloud.
- **A fixed `num_ctx` of 32k won't fit on the laptop.** The model would spill onto the CPU and slow down badly. The design works out the context size per model and per GPU instead.
- **Status messages alone may not stop IDE timeouts.** MCP hosts aren't required to reset their timeout when they receive log messages. So the design adds progress updates and runs long tasks in the background.

## What the combined design adds

1. **Check, then move up a tier.** The router tries the cheap model first and checks its output. If the check fails, it moves up one tier. This is what makes routing to free and local models safe.
2. **Savings tracker.** It records what the paid model would have cost, so it can show "Saved $X this week". That's your selling point on the store page.
3. **Model data updates separately from the app.** When free models get renamed or retired, you update the catalog, not the code. That keeps your support work down.
4. **OpenCode use case.** Routing ranks OpenCode's free models next to OpenRouter's free models and your local ones. When you're out of credits, it either recommends a model or switches automatically through the gateway.

## Selling on itch.io

- **Lite edition, free or pay-what-you-want:** the MCP server, routing to local and free models, automatic switching when you hit a limit, and local-only mode.
- **Pro edition, a one-time price around $19–29:** the gateway, full output checking, the handoff between models, the dashboard and setup wizard, learning from your feedback, and the Fusion and image tools.
- **Licensing:** itch only lets buyers download paid files, so ship Pro as a separate download and skip DRM. Never put an itch API key inside the app.
- **Must-dos before selling:**
  - Rename the product. HyperMesh is an established Altair engineering software brand, and your spec also says "HydraMesh" in one place. "Headwaters" is now the product name (it replaced the working name "Spillway"); run a trademark search on it before selling.
  - Users bring their own API keys.
  - The app must never rotate multiple accounts to get around free-tier limits.
  - Don't route subscription logins (such as a Claude or Copilot plan) through the gateway; support API keys only.
  - No usage tracking by default.
  - If you have an employer, check your outside-work and IP agreement.

## Files

- **`headwaters_ultimate_design.md`:** the full design, including the routing decision order, the VRAM calculation, an example config file, which setup to use in each IDE, the two editions, the roadmap, the risks, and a list of things to verify before coding. *(Appendix B)*
- **`headwaters_agent_implementation_prompt.md`:** the prompt for your coding agent. It builds the MCP server first, which is your free Lite 0.1 release, then the gateway, then the checks and the dashboard. *(Appendix C)*

**Questions that would change the plan:**
1. Do you want Lite open-sourced on GitHub to attract users, or kept closed?
2. Is Windows your main target for the first release?
3. Would you cut Pro's price if it shipped only the gateway and dashboard at first, with the rest added in updates?

---

## Turn 6: Open source, Windows first, pricing

### User
> 1. haven't decided, 2. yes, 3. probably not but not sure

### Assistant

Your answers lead to three changes in the plan: Windows becomes the main target, Pro code moves into its own package, and the pricing plan changes slightly. I've put these into an addendum file that applies to both the design and the agent prompt. *(Appendix D)*

## 1. Open source: decide later, but set things up now

You can open-source later, but you can't take it back: once code is public, forks stay public. So the safe default is to keep everything closed until Lite 0.1 has real users, then decide.

**One change has to happen now, whatever you decide.** My earlier prompt used a build-time flag to switch between Lite and Pro. That only works while the code is closed. If Lite's code is public, anyone can flip the flag and unlock Pro for free. The fix:
- `headwaters` (core, MCP server, basic verification) is a package that could be published.
- `headwaters_pro` (gateway, dashboard, full verification, handoff, learning) is a separate closed package that plugs into `headwaters` through a fixed plugin interface. It is never in the same repo.

With that split, you can open-source Lite at any point without rework.

| Option | Upside | Downside |
| --- | --- | --- |
| Keep both closed | Simplest; most control | Harder to get noticed; users of local-model tools prefer code they can inspect |
| Open Lite, keep Pro closed (open core) | GitHub discovery, trust, possible contributors | Someone could rebuild Pro features. Most of Pro's value is convenience (installer, dashboard), which people still pay for. |
| Source-available (e.g. FSL, which becomes Apache after 2 years) | Code is visible but others can't resell it | Some developers distrust licenses that aren't fully open source |
| Open only the model catalog | Others can contribute model scores and limits without exposing your code | You have to review contributions |

**Decide before you publish Lite 0.1.** Opening only the catalog is a low-risk middle option. If you have an employer, check whether publishing open source needs approval.

## 2. Windows first

The addendum includes these Windows-specific items:

- **Silent overflow into system RAM:** recent NVIDIA drivers can spill VRAM into system RAM instead of failing. The result is a model that runs very slowly with no error. `doctor` should detect this (by comparing measured speed with the size `/api/ps` reports) and point you to the NVIDIA Control Panel's "CUDA – Sysmem Fallback Policy" setting.
- **How much VRAM to reserve:** on the 5080 desktop, the GPU also drives your monitor, so reserve about 2–2.5 GB. On a laptop with switchable graphics where the integrated GPU drives the screen, about 1 GB is enough. Detect this automatically.
- **Battery awareness (laptop):** the GPU slows down on battery, so favor free cloud models then. Users will notice this feature.
- **Ollama settings:** on Windows the environment variables have to be set at user level, and the Ollama tray app restarted, before they apply. `doctor` should check both.
- **Blocked ports:** Hyper-V and WSL can silently reserve port ranges. Check before binding a port, fall back to another, and bind to `127.0.0.1` only so the Windows Firewall doesn't prompt.
- **Line endings:** Windows CRLF line endings make `git apply --check` fail on diffs. Normalize line endings in the verification step.
- **Packaging:** Defender often flags PyInstaller builds as malware. Use a uv-based installer (built with Inno Setup) instead, and sign the code so Windows SmartScreen doesn't warn buyers. Itch.io's `.itch.toml` file sets the launch actions in the itch app.
- **MCP over stdio:** IDE configs need the full path to the executable. Nothing may be written to stdout except MCP messages; all logs go to stderr. This is the most common cause of broken MCP servers on Windows.
- **Other Windows details:** start at login through Task Scheduler (no admin rights needed). Store keys in Windows Credential Manager. Handle setups where Ollama runs on Windows but the IDE runs inside WSL.

## 3. Pricing: early access instead of a lower price

You don't have to cut the price. Use a rising early-access price instead:

| Stage | Contents | Price (example) |
| --- | --- | --- |
| Lite 0.1 | MCP server, local and free routing, wall failover | Free or pay what you want |
| Pro Early Access | Gateway, dashboard, savings tracker | ~$15, clearly labeled early access |
| Pro 1.0 | Adds full verification, handoff, learning | ~$25 |

On itch, buyers keep access to every future file uploaded to the same page. So early buyers get 1.0 at no extra cost, which is the honest promise to make.

Ship the early-access version only once the savings counter works, since that is the screenshot that sells the product. If early buyers don't find the gateway alone worth paying for, that tells you to hold back until 1.0.

The addendum has these updates plus a patch for the agent prompt. The patch covers the package split, the Windows requirements, and an open-source decision point after phase 2.

---

## Turn 7: GitHub Copilot use case, and proxy vs. MCP roles

### User
> How will the new design handle this use case: GitHub Copilot use case. Using 'auto' model or new ones like HydraFusion, or whatever new comes out. When you're out of credits, what happens next? I want to continue my vibe coding. can pointing to the proxy then be the answer? do i need to switch to opencode or cline or others, use up any creds and have the proxy do the rest? What does the proxy do and what does the MCP server do in any use case?

### Assistant

Yes, pointing Copilot Chat at the gateway is the answer. You don't need to switch to OpenCode or Cline, and you don't need to burn other credits first.

## What happens now when Copilot credits run out

GitHub changed this on June 1, 2026. Premium request units were replaced by GitHub AI Credits, and GitHub stated that fallback experiences are no longer available: users who used to fall back to a lower-cost model after exhausting their requests now depend on available credits and admin budget controls. So when credits run out, credit-consuming Copilot features stop; there is no automatic fallback to a lower-cost model, but code completions keep working. Copilot also has no free model left to drop to: every remaining model, including the lightweight ones, now meters through the credit pool.

In practice: autocomplete keeps working, but chat and agent mode stop. That's true whether you were on Auto, "HydraFusion" or any future Copilot model.

## Staying in VS Code

VS Code Copilot Chat accepts your own model endpoints ("bring your own key"). It surfaces models from external providers (Azure, Anthropic, Hugging Face, Gemini, OpenAI, OpenRouter or a local runtime) in the same model picker, and accepts any endpoint compatible with the OpenAI Chat Completions API. These models don't use Copilot credits: local and bring-your-own-key models work without signing into GitHub and without any Copilot subscription, and they run in both Chat and Agent mode.

1. **Once:** add the Headwaters gateway as a custom endpoint in Copilot Chat.
2. **While you still have credits:** to make them last, use `headwaters/auto` for routine work and switch the picker to Copilot Auto for hard tasks.
3. **When credits run out:** switch the picker to `headwaters/auto` one time. From then on it's automatic: free cloud models first, then your local Ollama models, and it switches again each time it hits a limit.

**Limits:**
- Headwaters can't call Copilot's own models, because that would mean reusing your Copilot login, which breaks GitHub's terms. It can only escalate to paid models you have your own API keys for.
- Headwaters can't see your Copilot credit balance. That one switch in step 3 is manual.
- In VS Code's separate Agent Host window, custom models are hidden by default. The setting `chat.agentHost.byokModels.enabled` (default false) exposes them, and the setup wizard turns it on.
- Copilot Business and Enterprise admins can block custom models entirely.

## Why the MCP server alone stops working here

MCP tools only run when the chat model decides to call them. Once Copilot's credits are gone, no Copilot model is left to call them, so an MCP-only setup goes dead too. MCP keeps working when the chat model is `headwaters/auto` or local Ollama.

## What each piece does

| | Gateway (proxy) | MCP server |
| --- | --- | --- |
| Role | Is your chat and agent model | A toolbox your chat model uses |
| Catches limits on the main model and switches without you noticing | ✅ | ❌ (only on its own calls) |
| Hands cheap subtasks to free/local models while you're on a paid model | ❌ | ✅ |
| Checks output, Fusion, rubber duck, images, slash commands | Partly (checking output) | ✅ |
| Works in tools that won't accept a custom endpoint (Cursor, Claude Desktop) | ❌ | ✅ |
| Keeps working after the IDE's own credits are gone | ✅ | Only if the chat model is the gateway or local |

**Short version:** the gateway keeps you coding; MCP makes the coding cheaper and better. For every tool that accepts a custom endpoint, use both.

## Which to use in each tool

| Situation | Use |
| --- | --- |
| VS Code Copilot, credits gone | Gateway (as a BYOK model), with MCP on top |
| VS Code Copilot, credits left, saving them | `headwaters/auto` as default, or Copilot plus MCP delegation |
| Cline, Kilo, Roo, Continue, OpenCode, Claude Code (with API keys) | Gateway for failover, plus MCP extras |
| OpenCode Go credits gone | Gateway as an OpenCode provider (picks free or local for you), or MCP `recommend_model` |
| Cursor, Claude Desktop, Trae (no custom endpoint) | MCP only |
| Fully offline | Gateway with `local_only`, or Ollama directly with MCP |
| Devin and other cloud agents | Neither |

## Additions to the design

The new addendum file *(Appendix E)* covers:
1. **A Headwaters VS Code extension (Pro):** "Headwaters Auto" appears directly in Copilot's model picker, with no URL to type, plus a status-bar badge showing the current model and money saved. Similar fallback extensions already exist, so Headwaters has to stand out on quality ranking, GPU fit, output checking and the savings counter.
2. **"Switch to Copilot" advice:** when a task is too hard for any free or local model and you have no paid keys, Headwaters still answers and adds a note suggesting you switch the picker to Copilot Auto, with a handoff summary ready.
3. **Savings in Copilot terms:** an estimate of "Copilot credits not spent".
4. **A rule against proxying Copilot logins,** plus a test that simulates running out of credits and then hitting a limit at every tier in turn.

---

## Turn 8: Do other VS Code extensions fall back to a smart list of Ollama models?

### User
> do the other vs code extentions also fallback to a smart list of ollama local models?

### Assistant

Mostly no. Of the extensions I found, none picks the best local Ollama model for each prompt from the Copilot model picker. Some extensions show your Ollama models in the picker, one switches through a fixed list you set up, and one picks a model per prompt but only works as a separate `@router` chat, not as a model you select for agent mode.

## What I found

| Extension | Fallback? | Picks a model per prompt? | Notes |
| --- | --- | --- | --- |
| Built-in Ollama option in VS Code / official Ollama extension / Opilot | No | No | They only list your models. VS Code queries your local Ollama server and lists whatever you've pulled, and you choose. |
| OAIProvider | No | No | Brings models from NVIDIA NIM, Ollama, LM Studio, vLLM, Together AI or any other OpenAI-format API into the Copilot Chat model picker. You list the models yourself. |
| **AnyModel** | **Yes, fixed order** | No | The closest fallback match: if the first model is down, rate-limited or errors before answering, the next one takes over (e.g. local first, cloud as backup). It also reads each Ollama model's real context size and keeps models loaded. But you set the order once, and it doesn't look at the prompt. Small user base: 4 installs. |
| **Model Router (`@router`)** | Yes | **Yes** | It classifies the prompt by task (explain/generate/refactor/debug/…) and complexity (trivial/moderate/complex), then falls back across models and tiers if a provider fails. It can use a local Ollama model to do that classifying, and it has a savings dashboard. The catch: it's a chat participant you type `@router` to. It isn't a model in the picker, so it can't drive Copilot's own agent mode. |
| Ollama Cloud (JKagi) | Yes | Partly (based on cost and speed) | It has its own chat panel instead of plugging into Copilot, and it switches providers when one fails. |

None of these advertise:
- choosing between your local models by need (vision, coding, reasoning)
- checking whether a model fits your 12/16 GB of VRAM, or avoiding swapping one loaded model out for another
- treating out-of-credit errors differently from rate limits
- checking the output and moving up a tier if it's bad

The marketplace changes weekly, so a newer extension may do some of this.

## Bigger competitors outside the VS Code picker

- **llm-router (ypollak2), the closest overall.** An open-source universal LLM router for AI coding tools that works through MCP and hooks, with a free-first fallback chain. It supports Claude Code, OpenCode, Cursor, Trae and VS Code. Its Copilot support is limited: the listing calls it a capability extension with no cost-routing. Its README also points out that a proxy cannot intercept a session authenticated by a subscription, because there is no key to forward, which is the same constraint Headwaters's design works around.
- **pi-model-router.** For the pi agent only. It can detect HTTP 402/429/503/529 and transparently fall back to local models through a wildcard pattern like `ollama/*`.
- **opencode-rate-limit-fallback.** An OpenCode plugin that switches to one fallback model you choose.

## What this means for Headwaters

The specific gap still looks open: a "Headwaters Auto" model inside Copilot's picker that works in agent mode, picks local models with VRAM in mind, and checks its output. That makes the VS Code extension (E1 in the v3.2 addendum) your strongest differentiator, not just an add-on.

llm-router overlaps heavily with the MCP half of Headwaters. You said you don't want to reinvent the wheel, so try it for a day before building Headwaters's MCP server. Then you can either:
- build only what it lacks (the Copilot picker model, smart local-model selection, the gateway, the Windows installer), or
- sell a polished product with a clearly different focus.

If you sell on itch, be ready to answer "why not the free open-source one?" on your store page.

Should I add a competitor comparison section to the design, with feature-by-feature positioning against these and a sharper pitch for the store page?

---

## Turn 9: Scopes (auto / free / local / cloud), Private Mode, competitor comparison

### User
> yes, also in addition to headwaters/auto selection, would we have headwaters/local (all free all local gives privacy) and headwaters/cloud-only (not sure we need or what use cases that would call for) Or did you plan for another way to switch or turn on 'all local' mode?

### Assistant

I've added both things you asked for to the design. `headwaters/local` was already in the plan, and two more scopes join it. All the details are in a new design addendum *(Appendix F)*.

## Four model choices in your IDE's picker

| Picker model | What it promises | Uses | Can cost money? | Can a prompt leave your PC? |
| --- | --- | --- | --- | --- |
| `headwaters/auto` | Best result at the lowest cost | Your paid keys, OpenRouter Auto, free cloud, local | Only on your own keys, within your budget | Yes |
| `headwaters/free` (new) | Never costs anything | Free cloud models, then local | Never | Yes (free cloud) |
| `headwaters/local` | Private and offline | Your Ollama models only | Never | Never. It returns an error rather than use the cloud. |
| `headwaters/cloud` | Leaves your GPU alone | Your paid keys and free cloud | Only on your own keys, within your budget | Yes |

- **`headwaters/free` is probably the one you'll use most once Copilot runs out.** `auto` is allowed to use paid keys you've added; `free` never spends anything.
- **`headwaters/cloud` is for times when local isn't a good choice:**
  - the laptop is on battery, so the GPU is slowed down and running models drains it
  - you're gaming, rendering or training
  - you don't want fan noise or heat, say in a meeting
  - you're on a machine without an NVIDIA GPU or without Ollama
  - you want the fastest replies
- **To keep the picker short,** `headwaters/quality` and `headwaters/duck` are hidden extras. "Check the output" is now a setting rather than a separate model.

## Other ways to go all-local

Picking `headwaters/local` affects one chat. Stronger switches:

1. **Private Mode** forces everything local, even when a tool asks for `auto`. Turn it on from:
   - the tray icon (it shows a purple lock while on)
   - the VS Code status bar
   - the command line: `headwaters private on`
   - the `/headwaters-local` slash command
2. **A per-project rule:** add `.headwaters.yaml` to a repo with `privacy: local`, and that repo always stays on your machine.
3. **Secret detection:** a prompt containing an API key or private key is automatically sent to a local model.
4. **A default** in your settings file, used when a tool doesn't pick a model.

The strictest of these always wins. Only you can loosen one, never a tool. The old `mode` setting now just controls how Headwaters ranks models within whichever of the four choices is active (cheapest first or best quality first).

**What "local" guarantees:**
- Everything Headwaters does behind the scenes also stays local, including deciding which model to use, checking the output, and summarizing a chat when it switches models.
- If no local model can handle the request, for example an image when you haven't installed a vision model, you get an error naming the model to install. It never quietly uses the cloud.
- A test checks that no network connection ever leaves your PC in local mode, including when things fail.

## Competitor comparison

**llm-router is the main competitor.** From its README:
- It's mature, with about 1,700 commits and support for about 15 tools.
- It sends prompts to free and local models first. It has secret detection and honest savings reports.
- By default its free or local answers are only suggestions passed to Claude.
- In Copilot it doesn't do cost routing.
- By default it lets local models write files and run commands.

**Where Headwaters stands out**, which becomes the store pitch:
1. A model in Copilot's picker that works in agent mode.
2. Local model choice based on your GPU's memory and speed.
3. Output checked before you see it, falling back to a stronger model if the check fails. Local models don't edit files directly.
4. `free` and `local` choices that keep their promises, and Private Mode can't be overridden by a tool.
5. A one-click Windows install.

The addendum also has:
- a comparison table against AnyModel, Model Router, OAIProvider/Opilot, pi-model-router, opencode-rate-limit-fallback, LiteLLM, OpenRouter Auto, claude-code-router and RouteLLM
- draft store-page text, with the tagline "One model in your picker that never runs out" and an answer to "why not use a free router?"
- an update to the implementation prompt for your coding agent

Some comparison rows come from earlier research or general knowledge, so check each competitor again before you publish claims about them. Don't copy any of their code without checking its license.

If you ever use Headwaters on your employer's code, `headwaters/local` plus a project rule is the safe setup, though your company's tool approval rules still apply.

---

## Turn 10: Build our own MCP server instead of using llm-router

### User
> I don't want to have to use llm-router AND my gateway, i'd much rather reinvent the MCP server and make it better than anything else

### Assistant

Agreed: Headwaters is now one self-contained product with no llm-router. The design's goal is for its own MCP server to beat llm-router on things you can measure, not just to match it. I've added a new addendum (v3.4) to the design *(Appendix G)*.

## How Headwaters's MCP server beats llm-router

| Area | llm-router today (from its README) | Headwaters's target |
| --- | --- | --- |
| Using cheaper answers | Free or local answers are only hints by default; Claude still answers | The checked answer is the result, and the IDE's model is told to pass it on unchanged |
| Recognising the prompt | Its pattern rules confidently classify about half of prompts; the rest get a default | Three layers: rules first, then matching against similar labelled prompts, then a tiny model only when still unsure. Goal: confident on at least 90% of prompts, with the accuracy published. |
| Context for cheap models | A knowledge index | An index of your repo's code, packed to fit what the chosen local model can hold. This is what lets local models do good work. |
| Safety | By default, local models can write files and run commands | Changes come back as diffs by default. Optionally, they're tested in a throwaway copy of the repo first. |
| Checking output | Rejects answers that mention files or functions that don't exist | That check, plus syntax checks, "does the diff apply", and your own lint and tests |
| Local hardware | Ollama is one option in its list | Only picks models that fit your GPU memory, prefers the one already loaded, and knows about Windows and battery issues |
| Copilot | MCP tools only, no cost-based routing | A model in Copilot's picker that works in agent mode |
| Proof | Honest savings numbers | The same honesty, plus a public, repeatable benchmark (HeadwatersBench) |

## Two points that make your own MCP server worth more than it first looked

1. **Copilot now bills by tokens.** When Headwaters reads your files and a local model writes the code, Copilot only pays for a short request and a short diff, not for reading the files. That's real savings even before your credits run out.
2. **A third engine: hooks.** Some tools, such as Claude Code and Cursor, let a program see the prompt before their own model does; check this for each tool. Headwaters can answer simple prompts there with zero host tokens, which works even on subscription plans the proxy can't reach.

## Also in the addendum

- **Eight main tools,** with more available on request, written so even weak free models call them correctly. A test suite measures that for each tool and model combination.
- **One shared background process,** so several IDEs share one loaded GPU model instead of competing for memory.
- **Installer and diagnostics:** one command sets up Headwaters for each tool, shows you the config change before writing it, and can undo it.
- **A realistic order of work for one developer:** ship what competitors don't have first (GPU-aware local routing, checked answers, the Copilot picker model, provable privacy), then catch up on how many tools it supports.
- **An update to the agent prompt.**

## Clean-room rule

Don't read llm-router's source code while you build. Its README and feature list are fine to learn from, because ideas aren't protected but code is. Keep a short file noting where each design idea came from. That protects you if anyone claims you copied their work once you're selling on itch.io.

---

# Appendices: generated design files

The documents below are reproduced in full, in the order they were created. Later addenda override earlier documents where they conflict.


---

# Appendix A: Original HyperMesh Router (HMR) spec (user-provided)

*Source file: `hypermesh_router_prompt.txt (2)-90321e.md`*

## **HYPERMESH ROUTER (HMR) SYSTEM SPECIFICATION & PROMPT GUIDELINES**

## **1\. System Overview & Core Directives**

HyperMesh Router (HMR) is a multi-tier Model Context Protocol (MCP) server engineered to automate intelligent task routing, cost optimization, hardware safety, and automatic failover across cloud and local AI models.

### **Key Operational Goals:**

* Proactive Cost Saving: Automatically route simple, lightweight, or conversational prompts to free cloud tiers or local Ollama instances to preserve paid API credits.  
* Hardware & VRAM Guardrails: Route payloads exceeding 15,000 characters away from local hardware to prevent local GPU/VRAM OOM crashes.  
* Autonomous Failover: Catch 429 (Rate Limit) and 402 (Payment Required) errors to seamlessly cascade downstream without breaking agent execution.  
* Context Bridge Integrity: Distill and re-summarize state during mid-task failovers to prevent context truncation and attention drift.

## **2\. Multi-Tier Decision Hierarchy**

1. Manual/Opt-In Reasoning Tiers (Explicit User Request Only):  
   * `openrouter/fusion`: Heavy multi-model deliberation panel. STRICTLY restricted to high-level architecture, complex refactoring, and system security audits. NEVER use for raw code writing or interactive debugging.  
   * `openrouter/fusion-flash`: Speed-optimized flash model consensus panel for mid-tier code review and multi-model sanity checks.  
2. Proactive Cost Saver / Rubber-Duck Tier:  
   * When `force_rubber_duck=True`: Bypasses multi-model panels and routes to a low-latency model with a Socratic system prompt overlay.  
   * When prompt is lightweight/routine: Automatically routes to Free Cloud Ring or Local Ollama.  
3. Paid Primary Tier (Provider Floor Variant):  
   * Used for high-complexity code generation and complex multi-file edits when free tiers are insufficient.  
   * Appends `:floor` to paid model slugs (e.g., `anthropic/claude-3.5-sonnet:floor`) to enable price-based provider sorting and admit OpenRouter `flex` service tier endpoints.  
4. Fallback Cloud Ring (Active Free Endpoints):  
   * Curated sequence: `qwen/qwen-2.5-coder-32b-instruct:free`, `google/gemini-2.0-flash-exp:free`, `meta-llama/llama-3.3-70b-instruct:free`.  
5. Local Ollama Hardware (Safety Net):  
   * Local fallback model: `qwen2.5-coder:14b`.  
   * Local vision model: `qwen2.5-vl:7b`.

## **3\. MCP Tools Architecture & Signatures**

### **Tool 1: `hypermesh_route_prompt`**

Main routing endpoint for executing text and code prompts across the multi-tier hierarchy.

Parameters:

* `prompt` (str): The main user prompt or instruction.  
* `context_files` (list\[str\]): Relevant code or file contents.  
* `requires_vision` (bool): Flag for multimodal image/vision prompts.  
* `force_paid` (bool): Bypass cost saver to force Paid Primary.  
* `force_fusion` (bool): Force `openrouter/fusion` for architectural deliberation.  
* `force_fusion_flash` (bool): Force `openrouter/fusion-flash` for multi-model sanity check.  
* `force_rubber_duck` (bool): Force Socratic rubber-ducking mode on a fast model.  
* `force_local` (bool): Direct prompt to local Ollama.

### **Tool 2: `hypermesh_generate_image`**

Dedicated tool using OpenRouter's Image Generation API (`POST /api/v1/images`).

Parameters:

* `prompt` (str): Visual description for UI mockups, icons, or diagrams.  
* `aspect_ratio` (str): Default "1:1".  
* `force_model` (str): Default "google/nano-banana-2.1".

## **4\. OpenRouter API & Reasoning Safety**

* Mandatory App Attribution Headers:  
  `HTTP-Referer`: "https\://github.com/hydramesh/hydramesh-mcp"  
  `X-Title`: "HydraMesh MCP"  
* Reasoning Token Preservation:  
  Request payloads MUST include `"include_reasoning": True`. Response parsing must capture `message.get("reasoning")` or `message.get("thinking")` alongside standard `message.get("content")` to preserve reasoning traces (e.g., DeepSeek R1) in the Context Bridge.

## **5\. Configuration & External Rules (`hypermesh_rules.json`)**

All model slugs, complexity keyword lists, VRAM threshold limits, and provider settings must be externalized in `hypermesh_rules.json` to allow hot-swapping without code changes.

## **6\. Command Integration (`.md` Files)**

Provide custom command files in the .md directory (e.g., commands/hypermesh\_route.md) so IDE extensions (Cline, Roo Code, OpenCode) can invoke MCP tools using native slash commands.

## **7\. Hardware Cold-Start & Connection Keep-Warm Safeguards**

### **A. The Cold-Start / Model Loading Timeout Problem**

* **The Risk**: Ollama unloads models from VRAM after periods of inactivity to free system resources. When HMR targets local hardware (`qwen2.5-coder:14b`), loading model weights into VRAM can take 5–15 seconds on local GPUs (e.g., RTX 4080).  
* **The Impact**: Standard stdio MCP clients (Cline, OpenCode, Roo Code) enforce strict connection timeouts and can flag the MCP server as unresponsive or dead.

### **B. Mitigation Strategies**

6. **Background Keep-Alive / Keep-Warm Loop**:  
   * Maintain a background ping routine (or configure `keep_alive: "15m"` in Ollama API calls) to prevent model unloading and keep primary local weights locked in VRAM.  
7. **Non-Blocking Progress Heartbeat via `ctx.info(...)`:**  
   * While local drafting, VRAM loading, or cloud verification runs, stream intermediate status markers back through FastMCP context logging (`await ctx.info(...)`).  
   * Resets client timeout timers and maintains an active heartbeat so IDE extensions know the server is actively processing.

## **8\. Free-Tier Rate Limits & Exponential Backoff**

* **The Risk**: OpenRouter free tier models can encounter HTTP 429 (Rate Limit) or 503 (Provider Unavailable) errors during peak traffic hours.  
* **The Solution**: Implement an automated exponential backoff retry loop with randomized jitter and cascading fallback across the cloud free ring before surfacing errors to the user.

## **9\. Context Windows & `num_ctx` Optimization**

* **The Risk**: Default Ollama context window allocations (4096 or 8192 tokens) can silently truncate large files, leading to code hallucination.  
* **The Solution**: Explicitly enforce `num_ctx: 16384` (or `32768`) in Ollama API calls to leverage RTX 4080 VRAM fully. If payloads exceed local context budgets, auto-escalate to OpenRouter's 1M-token cloud free ring.

## **10\. Smart Verification Gate**

* **The Mechanism**: Before routing local drafts to heavy verification tiers, evaluate code using:  
8. **AST Syntax Check**: Python `ast.parse()` validation.  
9. **Completeness Check**: Scans for lazy placeholders (`# TODO`, `// rest of code`).  
   * If valid and complete, accept locally; otherwise, trigger cloud refinement.

## **11\. Optional Dynamic Speculative Drafting**

* **The Mechanism**: An optional/opt-in multi-pass mode (`force_speculative_draft`) where local Ollama rapidly generates structural skeletons (imports, signatures, docstrings) before sending to cloud models for complex logic. Kept as an opt-in toggle to prevent latency overhead on fast endpoints.

---

# Appendix B: Headwaters ultimate design (v3)

*Source file: `headwaters_ultimate_design.md`*

## Headwaters: Ultimate Design (v3)

*Product name (formerly the working name "Spillway"). It replaces "HyperMesh Router". See §11.1 for why the name had to change.*

> **One-line pitch:** Use your best model until it runs out, and never pay for work a free or local model can do just as well. This works in any IDE, on your own GPU.

---

### 0. Goals this design must meet

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

### 1. Comparison: HMR spec vs. my v2 proposal vs. this design

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

### 2. Architecture: one brain, two engines, one control center

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

### 3. Data model (SQLite)

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

### 4. Core modules

#### 4.1 Analyzer
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

#### 4.2 Catalog
Three layers, merged in this order:
1. **Live discovery**
   - Ollama: `/api/tags`, `/api/show` (capabilities, `model_info` architecture params for KV math, context), `/api/ps` (which model is warm)
   - OpenRouter: `/api/v1/models` (modalities, supported parameters, context, pricing, `:free`), `/api/v1/images/models` (image generation), `/api/v1/key` (credit and limits)
   - OpenCode: free models from the OpenCode config/provider listing, or a user list
   - Other free providers the user adds (OpenAI-compatible)
2. **Curated catalog** (`catalog.json`, signed, versioned). Contains per-task quality scores, `agentic_tools` reliability, known free-tier limits, deprecations and renames, Ollama tag aliases, and recommended local models per GPU profile. It is fetched from your GitHub releases at startup (with an ETag) and **works offline with the last cached copy**. This is the main way to cut support load: when models change, you ship data, not a new build.
3. **User overrides** in `rules.yaml`.

Unknown models get conservative default scores, labelled "unscored" in the dashboard.

#### 4.3 Hardware Guard
See §7. It answers: *Can model M run here at context C, how fast, and does loading it evict the warm model?*

#### 4.4 Wall Engine
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

#### 4.5 Policy Engine
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

#### 4.6 Verification Gate (verify, then escalate)
The gate is what makes aggressive downgrading safe.

| Output type | Checks (cheap → expensive) |
| --- | --- |
| Code / diffs | tree-sitter parse for the detected language (Python can also use `ast.parse`); placeholder/laziness scan (`TODO`, `... rest of code`, `// existing code`, truncated fences); `git apply --check` for diffs; optional project command from `.headwaters.yaml` (lint / type-check / a test subset) with a timeout |
| JSON | Parse + JSON Schema validation |
| Tool calls | Valid tool name, args match the schema |
| Prose | Length/completeness heuristics; optional self-check by a cheap model (off by default) |

**On failure:** escalate one rung, carrying the failure reason as context ("previous draft failed: SyntaxError line 42"). Limits: at most `max_escalations` (default 2), and the remaining paid budget. Record every pass or fail per (model, task) and feed it into the learned scores.

#### 4.7 Context Bridge (handoff packet)
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

#### 4.8 Executor
- **Cloud:** `litellm` SDK (client library) with OpenRouter attribution headers set to **your product's** URL and name.
- **Local:** Ollama native `/api/chat` with computed `num_ctx`, `keep_alive`, and `format` (JSON schema) when needed. The first-token timeout is longer when the model is cold.
- **Reasoning:** request it using the provider's current reasoning parameter (verify the OpenRouter field name). Capture `reasoning` / `thinking` separately from `content`.
- **Streaming failover (gateway):** fail over only before the first token. After that, send a clean terminal error event.

#### 4.9 Ledger and Learning
- Each outcome stores the actual cost and a **counterfactual cost**: what the user's paid primary would have charged for the same tokens.
- The dashboard shows *"Saved $X this week · Y% of requests served free/local · Z verification catches"*. **This is the screenshot that sells the product.**
- Learned score = EMA over (verification pass, user thumbs, quick re-ask = implicit failure).

---

### 5. Surfaces

#### 5.1 Engine A: Gateway
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

#### 5.2 Engine B: MCP server (FastMCP, stdio + optional streamable HTTP)

##### 5.2.1 Tools
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

##### 5.2.2 MCP protocol details
- **Files by path:** the server reads files from the workspace (MCP roots, or a configured root), so paid host tokens never carry file contents. Results come back as unified diffs, not whole files.
- **Progress:** send progress notifications during cold loads, drafts and escalations, plus log messages. Don't rely on logs alone to keep a host from timing out.
- **Async:** if the expected duration is above a threshold (cold load, Fusion, escalation), return a `job_id` right away. The host polls `job_status`.
- **Elicitation** (where the host supports it): confirm premium or paid spend above a threshold. Otherwise require the force flag.
- **Prompts primitive:** `/headwaters-route`, `/headwaters-duck`, `/headwaters-deliberate`, `/headwaters-status`, `/headwaters-handoff`. Also ship `.md` command files for hosts that read command folders.

##### 5.2.3 Dispatcher fallback (MCP-only mode)
- A rules pack (`AGENTS.md`, `.clinerules`, Kilo/Roo/Continue rules) tells the host model: *"For implementation work call `route`; apply returned diffs exactly; don't do the work yourself."*
- Dispatcher ladder: the free cloud model with the best `agentic_tools` score → a local dispatcher model (always available, never rate-limited).
- Hosts with a fallback-model setting switch automatically. Others need one manual switch, and `should_switch_host` warns before the wall.

#### 5.3 Control Center
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

#### 5.4 CLI
`headwaters start|stop|status|pick|models refresh|mode|pin|feedback|doctor|savings|handoff`

---

### 6. Tier map

| Tier | Members | Notes |
| --- | --- | --- |
| Premium (opt-in) | `openrouter/fusion` (Quality/Budget presets) | Costs the sum of panel calls plus a judge, and takes longer. Never automatic. Budget cap per call. |
| Paid primary | User's model(s), optional `:floor` price-sort variant | If you use `:floor`, also set provider quantization filters so you don't land on low-precision endpoints (verify the field) |
| Paid smart | `openrouter/auto` | Treated as one candidate; never reimplemented |
| Free cloud | OpenRouter `:free`, OpenCode free models, user-added free endpoints | **Ranked by the catalog**, not a fixed order |
| Local | Ollama models that fit this GPU | Wins ties with free cloud |
| Last resort | `openrouter/free` | Off by default; complexity ≤ 2 only |

---

### 7. Hardware Guard (RTX 4080 laptop 12 GB / RTX 5080 desktop 16 GB)

#### 7.1 Budget math
```
budget      = VRAM_total − reserve (1.5 GB default; more on laptops driving a display)
kv_bytes    = 2 · n_layers · n_kv_heads · head_dim · ctx · bytes_per_elem
              (bytes_per_elem: f16=2, q8_0≈1, q4_0≈0.5; params from /api/show model_info)
fits(M, ctx)= weights(M) + kv_bytes(ctx) + overhead(~0.5–1 GB) ≤ budget
num_ctx(M)  = largest ctx in {8k,16k,24k,32k,48k,64k} that fits and is ≥ 1.2 × est_tokens
```
If nothing fits, classify the model as `offload` (slow, large latency penalty) or `no_fit` (excluded).

#### 7.2 Why the HMR "15,000 characters" rule is replaced
Worked example for Qwen2.5-Coder-14B (48 layers, 8 KV heads, head_dim 128; Q4_K_M weights ≈ 9 GB):
- KV per token at f16 ≈ 2·48·8·128·2 B ≈ 0.19 MB. At q8_0 it is ≈ 0.1 MB.
- **12 GB laptop (budget ≈ 10.5 GB):** with q8 KV, about 12–14k tokens fit. That is roughly **45k characters**, so the 15k-char rule sends too much to the cloud. But HMR's fixed `num_ctx: 32768` would **not** fit, and would spill to CPU.
- **16 GB desktop (budget ≈ 14.5 GB):** 32k context with q8 KV fits (≈ 9 + 3.2 + overhead).

Only computed budgets get both cases right. Treat these numbers as estimates and calibrate them against `/api/ps` actual sizes on first run.

#### 7.3 Residency policy
- **12 GB:** `OLLAMA_MAX_LOADED_MODELS=1`. Strongly prefer the warm model (warm bonus + eviction penalty). The classifier stays on CPU. Vision requests that need a different model are allowed but carry an eviction cost.
- **16 GB:** one large model, or a mid-size model plus a small vision model if both fit.
- **Keep-warm:** `keep_alive` (default 30 m) on every call. An optional background ping keeps the "primary local" model loaded. Pause it when the GPU is busy with other work (an optional check of `nvidia-smi` utilization).

#### 7.4 Recommended local models
These ship in the curated catalog, not in code, because local model releases change monthly. Recommend by role: agentic coder, general/reasoning, vision, classifier. Pick for each GPU profile using the fit math above. Example seeds at the time of writing: Qwen2.5/3 Coder 14B-class, gpt-oss-20b, Devstral Small, Gemma 3 12B, Qwen2.5-VL 7B. Verify the exact Ollama tags, for example `qwen2.5vl:7b`.

---

### 8. Configuration: `rules.yaml` (hot reload, JSON Schema published)

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

### 9. IDE integration matrix

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

### 10. Commercial design for itch.io

#### 10.1 Editions
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

#### 10.2 Distribution and licensing on itch.io
- Page type: **Tool**. Upload builds with **butler** on channels `windows`, `linux`, `mac`. Buyers get updates through the itch app.
- **Licensing: keep it light.** On itch, paid files can only be downloaded after purchase, so ship Pro as its own download. Do **not** embed an itch API key in the client to verify purchases (it would leak). If you later want license checks, use a tiny server you control, or signed offline license files. Your audience dislikes DRM, so don't overinvest.
- **Packaging:** Windows first (most RTX laptop and desktop buyers). Two options:
  - (a) a `uv`-bootstrap installer that sets up a pinned environment
  - (b) a frozen build (Nuitka/PyInstaller)

  Option (a) is easier to maintain with LiteLLM's dependencies. Either way, the first-run wizard does the rest.
- **Don't bundle model weights.** The wizard pulls them through Ollama, under each model's own license.
- **Pricing tactics:** launch discount, a bundle with "catalog updates for 12 months" messaging (honor system), and a demo GIF of the savings counter. Itch audiences skew toward games, so also cross-post the page to dev communities. Gumroad or Lemon Squeezy can be added later without changing the product.

#### 10.3 Support-cost reducers
- Model churn goes into the **catalog**, not code releases.
- `doctor` + a redacted diagnostic bundle.
- A per-IDE setup page generated from the same templates the wizard uses.
- Pinned LiteLLM version, with an upgrade test suite before you bump it.

---

### 11. Legal, ToS and trust guardrails (must-haves for selling)

1. **Name:** "HyperMesh" is an established Altair engineering software brand, and the spec also mixes in "HydraMesh". Rename before selling. "Headwaters" is the chosen name (formerly the working name "Spillway"). Run a trademark search on it before launch.
2. **Bring your own keys only.** Never resell, pool or proxy other people's keys.
3. **No rate-limit evasion.** Do not rotate multiple accounts or keys to get around free-tier limits. It violates provider terms and could get the listing pulled. The router *respects* limits and routes around them.
4. **Subscriptions:** don't route consumer subscription credentials (for example Claude or Copilot plans) through the gateway. Support API keys only.
5. **Privacy:** no telemetry by default, all state local, secrets in the OS keychain (`keyring`), logs redacted. Make "prompts never leave your machine in local mode" a headline feature.
6. **OSS licenses:** include a third-party notices file (LiteLLM core MIT, but don't use code from its enterprise directory; MCP SDK; tree-sitter; etc.).
7. **Attribution headers:** use your product's URL and title. This is free marketing in OpenRouter's app rankings.
8. **Employment:** if you have an employer, check your outside-activity and IP agreements, and build it on personal time and equipment.

---

### 12. Roadmap

| Milestone | Scope | Ship as |
| --- | --- | --- |
| M1 Core | Catalog (live + seed), analyzer, hardware guard, wall engine, policy, executor, SQLite, CLI, tests | Internal |
| M2 MCP | `route`/`delegate`/status tools, files-by-path, progress + async jobs, basic verify, rules packs, prompts/commands | **Lite 0.1 on itch** |
| M3 Gateway | LiteLLM hooks, failover before first token, sessions, Anthropic endpoint, virtual models | Pro beta |
| M4 Quality | Full verification gate, verify-then-escalate, handoff packets, ledger | Pro 1.0 |
| M5 Control Center | Tray, dashboard, wizard, doctor bundle | Pro 1.0 |
| M6 Extras | Fusion tier, image tool, learning, rubber duck, speculative drafting (experimental) | Pro 1.x |

---

### 13. Risks and mitigations

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

### 14. External facts to verify before coding

- LiteLLM: current custom hook / router API (rewrite before the call, observe failures, retry before the first streamed token)
- OpenRouter: Fusion slug, presets and plugin fields; whether a `fusion-flash` slug exists; the reasoning request parameter; `:floor` behaviour and provider quantization filter; `/api/v1/key` fields; current free-tier limits
- Ollama: `/api/show` `model_info` keys for KV math; current vision model tags
- OpenCode: how free models are listed in config/providers; custom provider `baseURL` format
- MCP: progress notification and timeout behaviour per host; elicitation support per host; prompts-as-slash-commands support per host
- itch.io: current butler workflow, revenue-share settings, tax handling

---

# Appendix C: Agent implementation prompt

*Source file: `headwaters_agent_implementation_prompt.md`*

## Implementation Prompt: Headwaters (give this to an AI coding agent)

You are building **Headwaters**: a local-first LLM router sold as a desktop tool. It minimizes paid LLM usage while keeping quality, and survives rate-limit and out-of-credit walls, in any IDE. It has one Python core and two engines that share state: an **MCP server** and a **LiteLLM-based gateway**. There is also a Control Center (tray + dashboard + setup wizard) and a CLI. The design doc `headwaters_ultimate_design.md` is the source of truth. When this prompt and the doc disagree, ask.

### Ground rules
- Python 3.11+, managed with `uv`. Package layout:
  ```
  headwaters/
    core/        catalog.py analyzer.py hardware.py walls.py policy.py verify.py
                 bridge.py sessions.py ledger.py learning.py executor.py state.py rules.py
    mcp/         server.py tools/*.py prompts.py jobs.py
    gateway/     hooks.py router.py anthropic_compat.py
    control/     app.py (FastAPI dashboard :4001) tray.py wizard.py doctor.py
    cli/         main.py (Typer)
    integrations/ templates per IDE (cline, kilo, roo, continue, opencode, claude-code,
                 vscode, cursor, trae), commands/*.md, rules packs (AGENTS.md, .clinerules …)
    catalog/     seed_catalog.json, catalog.schema.json, rules.schema.json
  tests/
  ```
- Never hard-code model slugs, limits, error regexes or prices in Python. They belong in `catalog/seed_catalog.json` (curated, signed, versioned, auto-updatable from a URL with ETag, offline cache) and in `~/.headwaters/rules.yaml` (user, hot reload, validated by pydantic + published JSON Schema).
- State lives in SQLite at `~/.headwaters/state.db`, WAL mode, shared by both engines. Keep transactions short.
- Secrets go in the OS keychain (`keyring`). Env vars are a fallback. Redact keys and prompt bodies in logs by default. No telemetry.
- Cloud calls use the `litellm` SDK. Local calls use the Ollama **native** API (`/api/chat`, `/api/tags`, `/api/show`, `/api/ps`). Never use Ollama's OpenAI-compatible endpoint.
- Set OpenRouter attribution headers from config (`attribution.referer`, `attribution.title`).
- Before implementing any external integration, **verify the current API from official docs** and record your assumptions in `docs/ASSUMPTIONS.md`:
  - LiteLLM hooks
  - OpenRouter Fusion / `:floor` / reasoning param / `/api/v1/key` / `/api/v1/images`
  - MCP progress, elicitation and prompts
  - OpenCode providers
  - Ollama `model_info` keys

  Isolate each integration behind an interface.
- Tests: pytest. Mock all HTTP in unit tests. Target at least 85% coverage on `core/`. Provide an optional `-m live` suite against a real Ollama.
- Do not implement key rotation across accounts, pooled keys, or anything that evades provider rate limits. Do not route consumer subscription credentials.

### Core requirements

1. **Analyzer** → `Requirements{hard:{vision,tools,json,min_ctx}, soft:{task,complexity,risk,agentic,languages,duck_hint}}`.
   - Deterministic pass first: token estimate, image parts, tools, schema, languages, agentic detection.
   - Classifier pass only if the result is ambiguous: tiny Ollama model forced to CPU (`options.num_gpu=0`), structured JSON output, 2 s timeout, cached by sha256, falls back to heuristics.

2. **Catalog** merges three layers: live discovery (Ollama, OpenRouter `/api/v1/models` and `/api/v1/images/models`, OpenCode free models, user OpenAI-compatible free endpoints), the curated catalog, and user overrides.
   - Per model: tier, modalities, tools_ok, json_ok, ctx, prices, size, architecture params (layers, kv_heads, head_dim) and per-task quality + `agentic_tools` scores.
   - Unknown models get conservative defaults and an "unscored" flag.

3. **Hardware Guard**
   - Detect the GPU via `nvidia-smi` (fall back to manual config). Profiles: `laptop_12gb`, `desktop_16gb`, `custom`.
   - Budget = VRAM − reserve.
   - `kv_bytes = 2·layers·kv_heads·head_dim·ctx·bytes(kv_cache_type)`.
   - `fits = weights + kv + overhead ≤ budget`.
   - `num_ctx` = the largest bucket that fits and is ≥ 1.2 × est_tokens. Otherwise classify as `offload` (penalized) or `no_fit`.
   - Calibrate against `/api/ps` actual sizes.
   - Warm bonus and eviction penalty.
   - `keep_alive` on every call, plus an optional keep-warm ping that pauses when the GPU is busy.

4. **Wall Engine**: `classify(status, headers, body, provider) → {kind: rate_limit|exhausted|overloaded|context|other, retry_after, reset_at, scope: model|provider|key}`.
   - Patterns come from the catalog.
   - Rate limit: exponential backoff with full jitter; cascade if the wait exceeds the threshold.
   - Exhausted: no retry; mark the scope until a balance check or the reset.
   - Overloaded: 1–2 short retries, then cascade.
   - Context: re-route to a larger context.
   - Proactive: rpm/rpd counters, soft-exhaust at 90%, OpenRouter key/credit polling.
   - Fixture tests with real-shaped error bodies for OpenAI, Anthropic, Gemini, OpenRouter, Ollama and OpenCode.

5. **Policy Engine**
   - Modes: `cost_saver` (default), `quality_first`, `local_only`, `paid_only_until_wall`.
   - Overlays: `rubber_duck`, `premium`, `pin`.
   - Floor = `base[task] + k·complexity + 10·high_risk + 15·agentic`. Agentic also requires `agentic_tools ≥ floor`.
   - Score = quality − complexity_penalty + warm_bonus − latency − evict − cost_weight·cost.
   - Ladder:
     1. Overrides
     2. Hard filter
     3. Cheap rung: best of {local, free_cloud} ≥ floor; local wins ties
     4. Optimistic rung: within margin δ and verifiable → try it with the verification gate
     5. Paid primary (optional `:floor` variant) → `openrouter/auto`
     6. If paid is walled → best-effort cheap with mandatory verification and `below_floor=true` → `openrouter/free` only if enabled and complexity ≤ 2
     7. Structured error with reset ETAs and suggestions
   - Premium (Fusion) is never automatic: force flag or command only, and capped by `budget.premium_max_usd_per_call`.
   - Return `RouteDecision{model, tier, score, floor, below_floor, reasons[], fallbacks[]}`.
   - Golden-table tests covering at least 40 scenarios: vision, agentic, local_only, every wall combination, both GPU profiles.

6. **Verification Gate**
   - tree-sitter parse per language (`ast.parse` too for Python)
   - Placeholder/laziness scan (patterns from the catalog)
   - Truncated-fence detection
   - `git apply --check` for diffs
   - JSON Schema validation
   - Tool-call argument validation
   - Optional project checks from `.headwaters.yaml` (commands with timeouts, run in the repo with an allowlist)
   - On failure, escalate one rung carrying the failure reason, at most `max_escalations` times and within the remaining budget
   - Record pass or fail per (model, task)

7. **Context Bridge**
   - Handoff packet: goal, constraints, decisions, files (path + sha + role), open items, last error, last N turns verbatim, summary of older turns only, reasoning summary.
   - Never summarize code.
   - Extract deterministically first.
   - Never forward one model's raw reasoning tokens to a different model.

8. **Sessions**
   - Key = `x-session-id` header, or hash(system prompt + first user message). Sticky model per session.
   - Re-decide only at session start or when the context crosses a threshold.
   - Switch mid-session only on a wall or repeated verify failures.
   - Every switch writes a handoff packet.

9. **Executor**
   - litellm for cloud; Ollama native for local (computed `num_ctx`, `keep_alive`, `format` schema).
   - Longer first-token timeout when the model is cold.
   - Capture `reasoning` / `thinking` separately from content.
   - Streaming: fail over only before the first token; after that, send a clean terminal error.

10. **Ledger and Learning**
    - Each outcome records actual cost and counterfactual cost (the user's paid primary pricing).
    - Savings report by day/week/model.
    - EMA learned scores from verification results, thumbs, and implicit failure (re-asking the same thing within N seconds).

### Engine B: MCP server (build first)
- FastMCP, stdio transport (optional streamable HTTP).
- Tools:
  - `route`, `delegate`, `verify`, `rubber_duck`, `deliberate`, `generate_image`
  - `recommend_model` (include the exact command to switch the host, including OpenCode free models)
  - `should_switch_host`, `handoff`
  - `quota_status`, `savings_report`, `list_local_models`
  - `set_mode`, `pin_model`, `job_status`, `job_result`
- Inputs take **file paths**, resolved against MCP roots or a configured workspace root (reject paths outside it). Return **unified diffs** and commands, not full files.
- Send progress notifications during cold loads, drafts and escalations. Return a `job_id` (async) when the expected duration is over `mcp.async_threshold_s`.
- Use elicitation to confirm spend above `budget.confirm_above_usd` where the host supports it; otherwise require an explicit force flag.
- Expose MCP **prompts**: `headwaters-route`, `headwaters-duck`, `headwaters-deliberate`, `headwaters-status`, `headwaters-handoff`. Also generate `.md` command files and rules packs (AGENTS.md, .clinerules, Kilo/Roo/Continue rules) that describe dispatcher mode: "call route for implementation work; apply returned diffs exactly."
- Dispatcher fallback guidance: the free model with the best `agentic_tools` score → a local dispatcher model.

### Engine A: Gateway (Pro)
- LiteLLM Proxy run natively (pinned version; no Docker, Postgres or Redis), with Headwaters hooks. If the hooks can't rewrite the model before the call and retry before the first token, build a thin FastAPI gateway that uses the litellm SDK instead.
- Endpoints: `/v1/chat/completions`, `/v1/models`, `/v1/messages` (Anthropic).
- Virtual models: `headwaters/auto`, `headwaters/local`, `headwaters/quality`, `headwaters/verified` (buffer → verify → stream), `headwaters/duck`.
- Alias table for IDE-sent model names. Explicit models pass through with failover unless `x-headwaters-pin: true`.
- Response headers: `x-headwaters-model`, `x-headwaters-tier`, `x-headwaters-reason`.
- Integration tests with a mock provider that returns 429, 402 or 503 before the first token, and that fails mid-stream.

### Control Center and CLI
- FastAPI dashboard on :4001: status, walls with ETAs, live decisions, savings, model fit for this GPU, verify catches, thumbs up/down.
- Tray icon (pystray) with green / amber / red status.
- Wizard:
  1. Detect GPU, Ollama and its env
  2. Recommend and pull models from catalog GPU-profile recommendations
  3. Select IDEs and write or show their snippets from `integrations/` templates
  4. Keys into keyring
  5. Mode
  6. Self-test
- `doctor`: checks Ollama env (FLASH_ATTENTION, KV_CACHE_TYPE, MAX_LOADED_MODELS), ports, versions and keys, and exports a redacted diagnostic zip.
- Typer CLI: `start|stop|status|pick|models refresh|mode|pin|feedback|doctor|savings|handoff`.

### Editions and packaging
- An `edition` flag (`lite` | `pro`) in the build. Lite = MCP engine + core routing + basic verify (syntax/placeholders) + CLI + doctor + status page. Pro adds gateway, full verify/escalation, bridge/sessions, full Control Center, learning, Fusion, image tool.
- No online DRM and no embedded marketplace API keys.
- Windows-first installer: a `uv` bootstrap that creates a pinned environment and registers autostart (Scheduled Task; systemd user service on Linux; LaunchAgent on macOS). Provide butler push scripts for channels `windows`, `linux`, `mac`.
- Include `THIRD_PARTY_NOTICES.md`. Do not include any code from LiteLLM's enterprise directory.

### Phases (stop after each, run tests, report progress, assumptions and open questions)
1. Core: rules, state, catalog (seed + live), analyzer, hardware, walls, policy, executor, CLI `pick/status/models`.
2. MCP engine: `route/delegate/status/list_local_models/recommend_model`, files-by-path, progress, async jobs, basic verify, prompts, command files, rules packs. **This is the Lite 0.1 release candidate.**
3. Gateway: hooks, failover before first token, sessions, Anthropic endpoint, virtual models.
4. Quality: full verification gate, verify-then-escalate, handoff packets, ledger.
5. Control Center: dashboard, tray, wizard, doctor bundle, installers, butler scripts.
6. Extras: Fusion `deliberate`, `generate_image` (discover models via `/api/v1/images/models`), learning, rubber duck, experimental speculative drafting (off by default).

### Definition of done
- Cold install → wizard → working routing in Cline and OpenCode (gateway) and Claude Desktop (MCP) in under 10 minutes on a 12 GB RTX laptop.
- Simulated paid 402 mid-session → next request served by the best free/local model with a handoff packet, no user action, and the change visible on the dashboard.
- `local_only` works with no network.
- Golden policy tests and wall fixtures pass. Docs include per-IDE setup, troubleshooting (context truncation, tool-call failures, cold loads, model-name mismatch, port conflicts) and `ASSUMPTIONS.md`.

---

# Appendix D: v3.1 addendum: decisions, Windows-first, editions

*Source file: `headwaters_v3.1_addendum.md`*

## Headwaters v3.1 Addendum: Decisions, Windows-First, Editions Architecture

Applies to `headwaters_ultimate_design.md` and `headwaters_agent_implementation_prompt.md`. Where this file conflicts with either of them, this file wins.

### A. Decisions log
| # | Decision | Status |
| --- | --- | --- |
| D1 | Open-source Lite? | **Open.** Default is closed until Lite 0.1 has real users. Decide before the first public release. The architecture must keep both options possible (see B). |
| D2 | Primary platform | **Windows 10/11 x64 first.** Linux second, macOS best-effort (no NVIDIA; Apple Silicon unified memory handled later). |
| D3 | Pro pricing | **No discount for a staged Pro.** Use an early-access price that rises at 1.0. Early buyers get all 1.x updates. |
| D4 | Model catalog | Kept in a separate repo. Could be made public on its own (low-risk community contributions). |

### B. Editions architecture (replaces the "edition flag")
A build-time flag is not safe if Lite's code is ever public, because anyone could flip it. Split the code into packages instead:
```
repo: headwaters            (could become public)      repo: headwaters-pro (always private)
  headwaters/core/...                                      headwaters_pro/gateway/...
  headwaters/mcp/...                                       headwaters_pro/verify_full/...
  headwaters/verify_basic/...                              headwaters_pro/bridge/... sessions/...
  headwaters/plugins.py  ◀── entry points ──────────────   headwaters_pro/control_center/...
  headwaters/cli/...                                       headwaters_pro/learning/... fusion/ image/
repo: headwaters-catalog    (could become public)
```
- Core discovers Pro features through Python entry points (`headwaters.plugins`). The plugin interfaces are `SurfacePlugin`, `VerifierPlugin`, `BridgePlugin`, `DashboardPlugin` and `LedgerPlugin`. Version them, and include a contract test suite.
- Lite must work completely with no Pro installed. Pro installs on top as a wheel included in the Pro download.
- Licensing: no online DRM. Pro is simply the paid download.
- License options if D1 goes open: Apache-2.0 (includes a patent grant), MIT, or FSL-1.1-Apache-2.0 (source-available, becomes Apache after 2 years, and prevents competitors from reselling it). If you have an employer, check whether open-source publishing needs approval.

### C. Windows-first requirements
#### C1. GPU, VRAM and power
- Detect the GPU with NVML (`nvidia-ml-py`). Fall back to `nvidia-smi` (usually in `C:\Windows\System32`), then to manual config.
- **Reserve depends on whether the GPU drives a display:**
  - Desktop with the monitor on the RTX: reserve **2.0–2.5 GB**.
  - Laptop with Optimus/MUX where the integrated GPU drives the display: about **1.0 GB**.
  - Detect which case applies (NVML display-active status, or use the higher value if unsure). Users can override it.
- **NVIDIA Sysmem Fallback detection:** recent Windows drivers can silently spill VRAM into system RAM. That makes a model run very slowly rather than fail with out-of-memory.
  - Detect it by comparing measured tokens/s against the expected speed for that model size, together with the `/api/ps` `size_vram` vs `size` values.
  - When detected: mark the model `offload`, and have `doctor` explain the NVIDIA Control Panel setting "CUDA – Sysmem Fallback Policy".
- **Battery awareness (laptop):** use `psutil.sensors_battery()`. On battery, add a local latency penalty (the GPU is throttled) and prefer free cloud models. This is configurable: `local.on_battery: prefer_cloud | normal | local_only`.
- Optional "GPU busy" check: if another process (a game, a render) holds most of the VRAM, mark the local tier degraded.

#### C2. Ollama on Windows
- `doctor` reads user- and system-level environment variables (registry and `os.environ`) for `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_MAX_LOADED_MODELS` and `OLLAMA_HOST`.
  - Setting them writes user-level variables (`setx`-equivalent).
  - Tell the user to quit and restart the Ollama tray app, then verify the values through the actual behavior.
- Detect the Ollama install and version. Warn about versions older than the minimum supported one.
- WSL split setups (Ollama on Windows, IDE/agent inside WSL): document the host address and `OLLAMA_HOST=0.0.0.0` trade-offs. Keep Headwaters itself bound to 127.0.0.1 by default.

#### C3. Networking and ports
- Bind to `127.0.0.1` only (avoids a Windows Firewall prompt and stays private).
- Before binding, check the Hyper-V/WSL excluded port ranges (`netsh int ipv4 show excludedportrange protocol=tcp`). Fall back to the next free port and write it back into the IDE snippets.
- Default ports 4000 (gateway) and 4001 (dashboard) are configurable.

#### C4. Files, diffs and commands
- Paths: `platformdirs`. Config and state go in `%APPDATA%\Headwaters` and `%LOCALAPPDATA%\Headwaters`. Support long paths and paths with spaces.
- **Line endings:** normalize CRLF/LF before `git apply --check` and tree-sitter parsing. Keep the repo's line-ending style when producing diffs (respect `.gitattributes` and `core.autocrlf`).
- Project checks run through an allowlist with explicit shells (`pwsh` / `powershell` / `cmd`), timeouts, and no `shell=True` with untrusted strings.
- Workspace-root path validation must handle drive letters, UNC paths and case-insensitive comparison.

#### C5. MCP on Windows
- IDE config snippets use **absolute paths** to the Headwaters launcher (`headwaters-mcp.exe` shim or the full venv `python.exe -m headwaters.mcp`), never a bare `python`.
- **Nothing except MCP JSON-RPC may go to stdout.** All logs go to stderr or a file. Add a test that fails if anything else writes to stdout.
- Force UTF-8 stdio (`PYTHONUTF8=1`, explicit encoding) to avoid cp1252 failures.

#### C6. Claude Code / OpenCode on Windows
- The wizard sets `ANTHROPIC_BASE_URL` through Claude Code's settings `env` block where possible (scoped to that tool), rather than as a global variable.
- It writes or merges the OpenCode provider config in the correct Windows config location (verify the path) and shows a diff before writing.

#### C7. Packaging and distribution
- **Installer:** Inno Setup `.exe` that bundles the `uv` binary plus a locked `uv.lock`, and creates a private venv under `%LOCALAPPDATA%\Headwaters\runtime`. No admin rights are needed for a per-user install.
- **Avoid PyInstaller one-file builds** (frequent Defender false positives). If you freeze the app later, prefer Nuitka.
- **Code signing:** sign the installer and launcher shims, so SmartScreen doesn't warn buyers. Budget for a code-signing certificate or a cloud signing service, and verify eligibility for individual developers in your country.
- **Autostart:** Task Scheduler "At log on" task for the current user (no admin rights), with an on/off toggle in the wizard.
- **Tray:** `pystray` (Windows notification area) with green/amber/red status and toast notifications on tier switches (optional, rate-limited).
- **Secrets:** `keyring` with the Windows Credential Manager backend.
- **itch.io:**
  - Ship a `.itch.toml` manifest with launch actions ("Open Control Center", "Run Setup Wizard", "Doctor").
  - Push with `butler push <dir> <user>/<game>:windows-lite` / `:windows-pro`.
  - The installer and the itch app install should both work. Detect an itch-managed install and avoid self-update conflicts.
- **Uninstaller:** removes the scheduled task, the runtime and, optionally, state and keys (ask first).

### D. Pricing and release plan
| Release | Contents | Suggested price | Gate to ship |
| --- | --- | --- | --- |
| Lite 0.1 | Core + MCP + local/free routing + wall failover + basic verification + doctor | Free / pay what you want | 10-minute install on a 12 GB laptop; stable in Cline + OpenCode + Claude Desktop |
| Pro Early Access | + gateway (OpenAI + Anthropic endpoints), dashboard, **savings ledger**, tray, wizard | ~$15, labeled "Early Access, price rises at 1.0" | The savings counter is accurate and the 402-failover demo works without user action |
| Pro 1.0 | + full verification/escalation, handoff packets, sessions, learning | ~$25 | Golden tests + 2 weeks of early-access feedback with no P1 bugs |
| Pro 1.x | Fusion `deliberate`, image tool, speculative drafting | Same price; free updates | — |

Notes:
- Itch buyers keep access to new uploads on the same page. State plainly that early-access buyers get 1.0 and all 1.x updates.
- **Hold-back criterion:** if early-access feedback shows the gateway alone isn't worth paying for, pause sales and ship 1.0 instead of lowering the price.

### E. Patch to the agent implementation prompt
Replace the "Editions and packaging" section with:
```
EDITIONS: Two codebases. `headwaters` (core, MCP, basic verify, CLI, doctor, status page)
must be fully functional standalone and contain NO Pro code. `headwaters_pro` is a separate
private package registered via Python entry points (group "headwaters.plugins") implementing
versioned interfaces SurfacePlugin, VerifierPlugin, BridgePlugin, DashboardPlugin,
LedgerPlugin. Write plugin contract tests. No edition flags, no DRM, no embedded
marketplace keys.

PLATFORM: Windows 10/11 x64 is primary; every feature must be tested on Windows first.
Implement Addendum §C in full: NVML GPU detection with display-aware reserve, sysmem-
fallback detection, battery-aware policy, Ollama env checks via registry, excluded-port-
range check + 127.0.0.1 binding, platformdirs paths, CRLF normalization in verify,
allowlisted pwsh/cmd project checks, absolute-path MCP launchers, stdout-cleanliness
test, UTF-8 stdio, Credential Manager keyring, Task Scheduler autostart, pystray tray,
Inno Setup + bundled uv + uv.lock per-user installer, code-signing hook in the build
script, .itch.toml launch actions, butler push scripts (windows-lite, windows-pro), and
a clean uninstaller. Linux = second target (systemd user service); macOS best-effort.

DECISION CHECKPOINT: after Phase 2, pause and present an open-source readiness report
(secrets scan, license headers, third-party notices, any Pro leakage) so the owner can
decide D1 before Lite 0.1 is published.
```

---

# Appendix E: v3.2 addendum: Copilot playbook and engine roles

*Source file: `headwaters_v3.2_addendum_copilot_and_roles.md`*

## Headwaters v3.2 Addendum: GitHub Copilot Playbook and Engine Responsibilities

This addendum applies on top of the v3 design and the v3.1 addendum. Where they conflict, this file wins.

### A. Facts this design relies on (verified Oct 2026; re-check before release)
- Copilot uses token-metered **AI Credits** (since June 1, 2026). **There is no fallback model** when credits run out. Chat, agent mode and CLI stop. Inline completions and Next Edit Suggestions keep working on paid plans.
- No zero-credit model remains in Copilot. Every model is metered.
- VS Code Copilot Chat supports **BYOK**:
  - Built-in providers: OpenRouter, Ollama, and others.
  - Custom OpenAI-compatible endpoints.
  - Works in Chat and Agent mode.
  - No Copilot plan is needed.
  - Business and Enterprise orgs can disable BYOK by policy.
  - BYOK models appear in the Agent Host window only when `chat.agentHost.byokModels.enabled` is true (experimental).
- The VS Code `LanguageModelChatProvider` API lets an extension register models directly in the Copilot Chat picker.

### B. Hard rules
- Never proxy, reuse or reverse-engineer Copilot credentials or the Copilot API, and never call Copilot models from Headwaters. This violates GitHub's terms.
- Copilot models are outside Headwaters's control. Headwaters only takes over traffic the user sends to a Headwaters model.

### C. Engine responsibilities
| Responsibility | Gateway | MCP |
| --- | --- | --- |
| Be the host's chat/agent model (`headwaters/*` virtual models) | ✅ | – |
| Detect walls on the main model and fail over transparently (before first token) | ✅ | – |
| Per-request routing, sessions, handoff on switch | ✅ | ✅ (for its own calls) |
| Delegate subtasks while the host runs a paid or subscription model | – | ✅ |
| Verify and escalate | ✅ (`headwaters/verified`) | ✅ |
| Fusion, rubber duck, images, prompts/commands, recommend_model | – | ✅ |
| Hosts with no custom endpoint | – | ✅ |
| Keeps working after the host's subscription runs out | ✅ | Only if the host model is the gateway or local Ollama |

**Rule of thumb:** the gateway keeps you coding; MCP makes the coding cheaper and better.

### D. Copilot playbook (wizard and docs)
1. **Setup:** add Headwaters as a BYOK custom OpenAI-compatible endpoint (or install the Headwaters VS Code extension, see E).
   - Enable tool calling.
   - Set `chat.agentHost.byokModels.enabled`.
   - Register the MCP server in VS Code.
   - Detect a Business/Enterprise BYOK policy block and explain it.
2. **Saver mode (credits remaining):** the user's default chat model is `headwaters/auto`.
   - Headwaters routes to local and free models.
   - Tasks above the cheap tiers' quality go to the user's own paid keys, if configured. Otherwise Headwaters returns **handback advice** (see E2).
3. **Wall mode (credits exhausted):** the user switches the picker to `headwaters/auto` once. From then on all failover is automatic: free cloud, then local, then re-routing on every later wall.
4. **Hybrid:** the user stays on Copilot models and lets MCP `delegate` handle cheap subtasks. This saves credits but stops when credits run out, so recommend switching to step 3 at that point.
5. **Docs note:** after Copilot credits run out, inline completions still work through Copilot. Headwaters handles chat and agent mode only. Optionally offer Continue with a small local autocomplete model for Copilot Free users once completions are capped.

### E. New components
#### E1. Headwaters VS Code extension (Pro)
- Implements `LanguageModelChatProvider` and registers "Headwaters Auto / Local / Quality / Verified / Duck".
- Acts as a thin client to the gateway (no duplicated routing logic) and auto-discovers the gateway port.
- Declares per-model capabilities (tools, vision, context window) from the gateway's current decision space.
- Status bar: current tier and model, wall state, savings counter. Click opens the dashboard.
- Commands: set mode, pin model, open handoff, run doctor.
- Publish on the VS Code Marketplace and Open VSX. The marketplace listing doubles as a funnel to itch.io.
- Differentiators from existing BYOK-fallback extensions: quality-ranked routing, GPU fit, verification, the ledger, and one engine across every IDE.

#### E2. Handback advice
- Triggers when the best available model is below the quality floor AND no paid key tier exists AND the user has declared a subscription host (`hosts.subscription: copilot | cursor | claude_app | none` in rules.yaml).
- Action: answer with the best cheap model (verification mandatory), prefixed by a one-line advisory along the lines of: `⚠ Headwaters: this task likely needs a frontier model. Consider switching to Copilot Auto. Handoff: /headwaters-handoff`. Also store a handoff packet.
- Configurable: `advice: off | inline | header_only`.

#### E3. Ledger addition
- Optional "subscription credits avoided": estimates the Copilot credits a request would have used, using the user-entered model rate from GitHub's published pricing (kept in the catalog and dated). Label it clearly as an estimate.

### F. Agent prompt patch (append)
```
COPILOT SUPPORT: Never proxy or reuse Copilot credentials or call Copilot models. Support
VS Code Copilot Chat via BYOK (custom OpenAI-compatible endpoint → gateway) and via a
Pro VS Code extension implementing LanguageModelChatProvider as a thin client to the
gateway (capabilities per virtual model, status bar with tier/model/savings, commands
for mode/pin/handoff/doctor). Wizard: configure BYOK endpoint with tool calling, set
chat.agentHost.byokModels.enabled, register MCP, detect org BYOK policy blocks.
Implement handback advice (rules.yaml hosts.subscription, advice mode) and the optional
"subscription credits avoided" ledger estimate from dated catalog rates.
Integration test: simulate host-credits-exhausted → user selects headwaters/auto → verify
multi-wall failover (paid key 402 → free 429 → local) completes an agent-mode tool-call
loop.
```

---

# Appendix F: v3.3 addendum: scopes, privacy lock, competition

*Source file: `headwaters_v3.3_addendum_scopes_and_competition.md`*

## Headwaters v3.3 Addendum: Scopes, Privacy Lock, Competitive Positioning

This file applies on top of v3, v3.1 and v3.2. Where they conflict, this file wins.

### A. Routing model: scope × strategy × locks
The earlier `mode` mixed two different things. They are now separate:

| Axis | Question it answers | Values |
| --- | --- | --- |
| **Scope** | *Which tiers may serve this request?* | `auto`, `free`, `local`, `cloud` |
| **Strategy** | *How do we rank inside the scope?* | `cost_saver` (default), `quality_first`, `primary_first` (was `paid_only_until_wall`) |
| **Locks** | *What can never be loosened?* | Private lock, project privacy rule, secret detection |

`local_only` is no longer a mode. It is the `local` scope, optionally enforced by the Private lock.

### B. Virtual models (picker entries)

#### B1. Core (always shown)
| Model ID | Display name | Allowed tiers | Money | Data leaves machine | Edition |
| --- | --- | --- | --- | --- | --- |
| `headwaters/auto` | Headwaters Auto | paid keys, `openrouter/auto`, free cloud, local | Own keys only, budget-capped | Yes | Lite (MCP) / Pro (gateway) |
| `headwaters/free` | Headwaters Free ($0) | free cloud, local | Never | Yes (free cloud) | Lite / Pro |
| `headwaters/local` | Headwaters Local (Private) | Ollama only | Never | **Never**, fail-closed | Lite / Pro |
| `headwaters/cloud` | Headwaters Cloud (no GPU) | paid keys, `openrouter/auto`, free cloud | Own keys only, budget-capped | Yes | Pro |

#### B2. Optional (hidden by default, `picker.show_extras`)
- `headwaters/quality`: the `auto` scope with the `quality_first` strategy.
- `headwaters/duck`: the rubber-duck overlay on the cheapest fast model allowed by the current lock state.

#### B3. Removed as a model
- `headwaters/verified` becomes the setting `verify: always | agentic | off` (default `agentic`).
- Power-user suffixes are allowed: `headwaters/free+verify`, `headwaters/auto+quality`.

#### B4. Cloud-scope automation
When `local.on_battery: prefer_cloud` is set, or the GPU-busy check fires, the router (and the VS Code extension's status bar) *suggests* `headwaters/cloud`. It never auto-switches *out of* `local`.

### C. Precedence and switching
```
effective_scope = intersect(
    private_lock ? local : ANY,
    project_rule (.headwaters.yaml privacy: local|free|any),
    secret_detected ? local : ANY,        # configurable: local | refuse | off
    requested_scope (picker model / MCP param),
    default_scope (rules.yaml) if nothing requested
)
```
- A tighter scope always wins. Only the user can loosen a lock, and never mid-request. Project rules can only tighten.
- If the intersection is empty or can't be satisfied, return a structured error with a fix (e.g. "pull qwen2.5vl:7b", "disable Private lock in tray").
- **Switch surfaces:**
  - Picker model (gateway / VS Code extension)
  - Tray toggle "Private Mode" (purple lock icon)
  - VS Code status bar
  - CLI `headwaters scope <auto|free|local|cloud>` / `headwaters private on|off`
  - MCP `set_scope` / `set_private`
  - Prompts `/headwaters-local`, `/headwaters-free`, `/headwaters-auto`
  - Optional global hotkey
- MCP tools accept `scope` and **inherit** the effective scope from locks and project rules. In MCP-only setups, `set_scope` sets the default.

### D. Local scope: privacy guarantee spec
1. Enforcement lives at the **executor**, not only the policy layer:
   - Cloud clients are not constructed for a local-scoped request.
   - The executor asserts that the target is a loopback Ollama host (127.0.0.1/::1/localhost) and raises if not.
2. Every sub-step stays local: the classifier, verification self-checks, handoff summaries, escalations, `delegate`, `rubber_duck` and image description.
3. Escalation inside local means moving to a larger or better local model, then a larger context, then a structured error. It never moves to the cloud.
4. Prompts and outputs are not written to logs (decision metadata only). The ledger stores token counts only.
5. Catalog updates send no prompt data. `airplane: true` also disables catalog fetch and all other egress.
6. Tests:
   - Network-mock egress test: fail if any non-loopback socket is opened during a local-scoped request (including under failure, escalation and handoff).
   - Fuzz secret-detection test.
   - A lock-precedence golden table.
7. Marketing line: "Headwaters Local never sends a byte of your prompt off your machine, and the tests prove it."
8. **Secret detection** (all scopes): detects API keys, tokens and private keys, ignoring placeholder patterns. Default action `local` (route local or refuse with the reason). Per-request override only via explicit user action.

### E. rules.yaml changes
```yaml
default_scope: auto            # auto | free | local | cloud
strategy: cost_saver           # cost_saver | quality_first | primary_first
private_lock: false
airplane: false
secrets: { action: local }     # local | refuse | off
verify: agentic                # always | agentic | off
picker: { show_extras: false }
local:
  on_battery: prefer_cloud     # prefer_cloud | normal
```
Per-project `.headwaters.yaml`:
```yaml
privacy: local                 # local | free | any   (tighten only)
checks: ["ruff check {files}", "pytest -q -x tests/unit"]
```

### F. Competitive landscape (verify every row before publishing claims)
| Product | Surface | Smart per-prompt routing | Fallback on walls | Local-model selection | Verification | Copilot picker + agent mode | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **llm-router** (OSS) | Hooks + MCP + gateway + SDK; about 15 hosts | Regex heuristic (confident on about half of prompts) + chain | Yes, circuit breakers | Ollama in the chain | Grounding check | No (Copilot = capability extension, no cost-routing) | Mature (about 1.7k commits). Drafts are *advisory* by default. Local models get write/run access by default. Strong secret routing and honest savings reporting. **Main competitor.** |
| AnyModel (VS Code) | Copilot picker | No (fixed order) | Yes | Reads context, keep-alive | No | Yes | Tiny user base |
| Model Router `@router` (VS Code) | Chat participant | Yes (task + complexity) | Yes | Ollama as classifier | No | Not a picker model | Has savings dashboard |
| OAIProvider / Opilot / built-in Ollama BYOK | Copilot picker | No | No | Lists models only | No | Yes | Plumbing only |
| pi-model-router | pi agent | Partial | 402/429/503/529 → `ollama/*` | Wildcard | No | No | Single host |
| opencode-rate-limit-fallback | OpenCode plugin | No | One fallback model | No | No | No | Single host |
| LiteLLM Proxy | Gateway | Basic routing strategies | Yes | Any configured model | No | Via BYOK | Headwaters's transport layer, not a rival product |
| OpenRouter Auto / Free | Cloud router | Yes (cloud) / random free | Provider-level | No | No | Via BYOK | Headwaters uses it as a tier |
| claude-code-router, RouteLLM (OSS) | Proxy / research router | Rules / learned | Varies | Configurable | No | No | Verify current features |

#### F1. Positioning: where Headwaters wins
1. **"Headwaters Auto" inside Copilot's picker, working in agent mode**: smart, scoped routing where Copilot users already are.
2. **GPU-aware local intelligence**: VRAM and KV-cache fit, computed `num_ctx`, warm-model preference, sysmem-fallback detection, battery awareness.
3. **Verify-then-escalate with safe defaults**: answers as diffs, no direct writes by local models unless the user opts in.
4. **Guaranteed scopes**: `free` = $0, `local` = provably private (fail-closed, egress-tested), plus the Private lock.
5. **Windows-first product polish**: one-click installer, wizard, tray, doctor, signed builds.
6. **One engine for every IDE**: gateway + MCP + extension share state, walls and savings.

#### F2. Where competitors are ahead (be honest; plan answers)
- llm-router: breadth of hosts, its Claude-subscription hook approach, maturity, free and open source. **Answer:** different focus (Copilot/VS Code + local GPU + verification + privacy guarantee). Consider listing interoperability instead of fighting it. Check its license before reading code for ideas; never copy code.

#### F3. Store-page copy (draft)
**Tagline:** *One model in your picker that never runs out.*
**Sub:** When Copilot or your API credits hit the wall, Headwaters keeps you coding: best free cloud model, then your own GPU, picked per prompt, checked before you see it.
**Bullets:**
- 🔁 Automatic failover on rate limits and empty credits: free cloud, then local
- 🧠 Picks the right model for the job: code, vision, long context, agent tools
- 🖥️ Knows your GPU: only runs models that actually fit your 12/16 GB card
- ✅ Checks the code before you see it; escalates if a cheap model fumbles
- 🔒 Headwaters Local: 100% on-device, offline, nothing leaves your PC
- 💵 $0 mode, plus a live "saved this month" counter
- 🪟 One-click Windows install; works in VS Code Copilot, Cline, Kilo, Roo, Continue, OpenCode, Claude Code + any MCP host

**FAQ, "Why not a free router?":** "Free routers are great, and Headwaters plays nicely alongside them. Headwaters is built for one thing: keeping you in flow inside your IDE when the credits run out. It gives you a picker model for Copilot agent mode, local routing tuned to your exact GPU, verified output, a privacy lock you can trust, and a one-click Windows setup. Try Headwaters Lite free."

### G. Agent prompt patch (append)
```
ROUTING MODEL: Replace `mode` with scope × strategy × locks (Addendum v3.3 §A–E).
Virtual models: core headwaters/auto, headwaters/free, headwaters/local, headwaters/cloud;
optional headwaters/quality, headwaters/duck; `verify` becomes a setting with optional
model suffixes (+verify, +quality). Implement effective-scope intersection with
precedence private_lock > project rule (.headwaters.yaml, tighten-only) > secret
detection > requested > default. Local scope enforced at executor (loopback-only
assertion, no cloud clients constructed), all sub-steps local, escalation local-only,
fail-closed structured errors with fix hints, no prompt logging. Add `airplane` mode.
Switch surfaces: picker, tray Private Mode (purple lock), VS Code status bar, CLI
scope/private, MCP set_scope/set_private, prompts /headwaters-local|free|auto.
TESTS: egress test (no non-loopback sockets in local scope incl. failure/escalation/
handoff paths), lock-precedence golden table, secret-detection fuzz, battery/GPU-busy
suggestion of headwaters/cloud (never auto-leave local).
```

---

# Appendix G: v3.4 addendum: best-in-class MCP, hooks, HeadwatersBench

*Source file: `headwaters_v3.4_addendum_best_in_class_mcp.md`*

## Headwaters v3.4 Addendum: Best-in-Class MCP Engine, Hooks Engine, HeadwatersBench

This addendum applies on top of v3–v3.3. Where they conflict, this file wins.
**Decision D5:** Headwaters is one self-contained product (Gateway, MCP and Hooks engines on one core). It has no dependency on, and no bundling with, llm-router or any other router.

### A. Three engines, one core
| Engine | Intercepts | Best for | Edition |
| --- | --- | --- | --- |
| **Gateway** | The host's model calls (OpenAI/Anthropic endpoints, VS Code BYOK, VS Code extension) | Transparent failover, "never runs out" | Pro |
| **MCP** | Work the host model hands off | Copilot/Cursor/any MCP host; cutting host tokens; verification; extras | Lite (core) / Pro (advanced) |
| **Hooks** (new) | The prompt *before* the host model sees it (hosts with lifecycle hooks) | Answering simple prompts with zero host tokens, including subscription hosts | Pro |

All three share SQLite state (walls, sessions, ledger, locks). The effective-scope rules (v3.3 §C) apply equally to every engine.

### B. MCP engine: product requirements

#### B1. Front-door tool surface (8 tools; the rest appear through progressive disclosure)
| Tool | Purpose | Annotations |
| --- | --- | --- |
| `headwaters_do` | The main entry. `task`, `paths[]`, `images[]`, `scope?`, `want: answer\|diff\|plan`, `async?`. Returns a verified result. | not read-only; idempotent=false |
| `headwaters_ask` | Q&A / explain / summarize over paths (read-only) | readOnly |
| `headwaters_check` | Verify a diff or code | readOnly |
| `headwaters_think` | Rubber duck / deliberate (Fusion, opt-in) | readOnly |
| `headwaters_status` | Quotas, walls, scope, savings, GPU | readOnly |
| `headwaters_scope` | Set scope / Private lock / strategy | not read-only |
| `headwaters_handoff` | Produce or consume a handoff packet | readOnly |
| `headwaters_more` | Lists and enables advanced tools (image, recommend_model, jobs, pin, feedback…) | readOnly |

- Every tool declares an **`outputSchema`** and returns **structured content** plus a short text rendering.
- **Relay contract:** each result carries `relay: "verbatim" | "apply_diff" | "summarize_ok"`, and the bundled rules files teach host models to honor it.
- Results are **compact**: unified diffs, a token-budgeted summary, and pointers to resources for full detail (`headwaters://result/<id>`).

#### B2. Tool-description engineering and host eval
- Descriptions are short, imperative and example-led, tuned so weak or free dispatcher models call them correctly.
- **Host-model eval suite:** for each (host, dispatcher model), measure the tool-selection rate, argument validity and relay compliance on 100+ scripted tasks. Ship in CI, and publish results in HeadwatersBench.

#### B3. MCP spec feature map (verify each against the current spec version and per-host support)
| Feature | Use |
| --- | --- |
| Structured output / outputSchema | All tools |
| Tool annotations | readOnly / destructive / idempotent hints so hosts can auto-approve safe tools |
| Progress notifications | Cold loads, drafting, escalation |
| Async tasks (spec Tasks primitive if available; else job_id pattern) | Long runs, Fusion |
| Elicitation | Spend confirmation, scope loosening (user-only) |
| Resources (+ subscriptions) | `headwaters://status`, `headwaters://savings`, `headwaters://result/<id>`, `headwaters://handoff/<id>` |
| Prompts | `/headwaters-local`, `/headwaters-free`, `/headwaters-auto`, `/headwaters-duck`, `/headwaters-handoff` |
| Roots | Workspace boundary for path reads |
| Sampling | Optional: let the host model serve as a tier when the host allows it (never in local scope) |
| Transports | stdio (default) + streamable HTTP (shared daemon so multiple IDEs reuse one warm GPU model) |

**Shared daemon:** stdio MCP processes are thin shims to one Headwaters daemon, so three IDEs open at once don't each spawn a router or compete for VRAM.

### C. Analyzer v2: three-layer classifier
1. **Rules layer** (under 1 ms): hard requirements + strong signals.
2. **Embedding kNN layer** (about 10–30 ms, CPU): a local embedding model run through Ollama with `num_gpu=0`, matched against a labeled prompt bank (shipped in the catalog and growing from the user's own feedback). Returns task, complexity and confidence from neighbor agreement.
3. **Tiny LLM layer** (CPU, 2 s budget): runs only when kNN confidence is below the threshold.
- **Calibration:** confidence is calibrated on a held-out set. Target **≥90% of prompts classified with confidence**, with measured accuracy published in HeadwatersBench.
- **Learning:** verification outcomes and thumbs feed the user's local bank (privacy: stays local; opt-in export of anonymized labels only).

### D. Repo Context Engine (what makes local models good)
- Incremental local index: tree-sitter symbols (defs, refs, imports), file summaries, and code embeddings (CPU), stored in SQLite + a vector table (e.g. sqlite-vec).
- **Context packing to the chosen model's computed `num_ctx`:** target files → referenced symbols → callers/callees → tests → docs, highest-value first, with token accounting.
- Reads paths itself, so host tokens are not spent on file contents.
- File watcher plus git-aware invalidation. Respects `.gitignore` and `.headwatersignore` (secrets and env files are excluded by default).

### E. Safe execution
- **Default: diffs only.** Local or free models never write files directly.
- **Optional worktree mode** (`exec: worktree`):
  - Changes are applied in a throwaway `git worktree`.
  - Project checks run there under an allowlist, timeouts, and no network for the subprocess where the OS allows (Windows: Job Object limits + firewall rule for the runner, or documented best effort).
  - Only diffs that pass verification are returned. The host or user applies them.
- Destructive commands (force push, recursive delete outside the worktree, credential access) are hard-denied.

### F. Verification v2
Grounding (every symbol or file referenced must exist in the index or the diff) → tree-sitter parse → placeholder scan → `git apply --check` → project checks in the worktree → JSON/tool-schema checks. Each failure carries a machine-readable reason into the escalation prompt.

### G. Hooks engine (Pro)
- `headwaters install --host <x>` writes the host's hook config where the host supports lifecycle hooks. Verify current hook APIs per host (e.g. Claude Code, Cursor, Codex CLI, Gemini CLI, Copilot CLI, Windsurf).
- Pre-prompt hook:
  1. Classify the prompt.
  2. If it is simple, read-only and in scope, answer it with Headwaters and either short-circuit the host turn or inject the verified answer as context. Which one depends on what the host allows.
  3. Otherwise pass through, adding a compact context pack.
- Never block the host on hook failure (fail-open for availability). For privacy locks, fail closed **only** when the user enabled "strict local".

### H. Multi-host installer and doctor
- `headwaters install --host vscode|copilot|cline|kilo|roo|continue|opencode|claude-code|cursor|trae|windsurf|claude-desktop|all`. For each host it writes:
  - the MCP entry (absolute launcher path)
  - the gateway endpoint / BYOK config
  - the rules pack
  - hooks, if supported
  - slash command files
- It shows a diff and asks for consent before changing any host config, and keeps a backup for `headwaters uninstall --host`.
- `headwaters doctor --host <x>` runs a live round trip, a tool-call test and a scope test, then prints a fix list.

### I. HeadwatersBench (proof, not claims)
- A public, reproducible harness (it could be open-source even if Pro stays closed), with four suites:
  1. **Routing accuracy** vs. labeled prompts
  2. **Quality vs. paid baseline** on coding tasks (pass@1 with tests, verified-diff rate)
  3. **Savings** at equal quality (counterfactual $ and host tokens avoided)
  4. **Host tool-call reliability** per dispatcher model
- Runs on reference hardware profiles (12 GB laptop, 16 GB desktop) and publishes methodology, n and dates.
- Consider submitting to public router benchmarks (e.g. RouterArena) for external credibility.
- The store page links to HeadwatersBench results. No unverified percentage claims.

### J. Clean-room policy
- Don't read competitor source code during design or implementation. Public READMEs and feature lists are allowed as market input.
- Keep `docs/PROVENANCE.md` recording where each major idea came from (own design, public docs, specs).
- Use original names, tool names, file layouts and docs.
- Check the licenses of all dependencies. Only use permissive ones in Pro (MIT/Apache/BSD); no copyleft code in the closed package.

### K. Updated roadmap
| Milestone | Scope | Release |
| --- | --- | --- |
| M1 | Core: catalog, analyzer layers 1+3, hardware, walls, scopes/locks, executor, state | – |
| M2 | MCP: 8 front-door tools, structured output, relay contract, progress/async, shared daemon, basic verify, installer for 4 hosts (VS Code/Copilot, Cline, OpenCode, Claude Desktop) | **Lite 0.1** |
| M3 | Gateway + VS Code extension (picker models) + ledger/dashboard | **Pro Early Access** |
| M4 | Analyzer layer 2 (kNN) + Repo Context Engine + Verification v2 + worktree exec | Pro 1.0 |
| M5 | Hooks engine + installer for all hosts + HeadwatersBench public | Pro 1.x |
| M6 | Fusion/duck/image extras, sampling tier, learning export | Pro 1.x |

Win order: differentiate first (GPU-aware local, verify-then-escalate, Copilot picker, provable privacy), then reach parity in breadth.

### L. Agent prompt patch (append)
```
PRODUCT: Headwaters is fully self-contained; no dependency on other routers. Implement
three engines (gateway, MCP, hooks) over one core with shared state and scope rules.
MCP: 8 front-door tools (headwaters_do, _ask, _check, _think, _status, _scope, _handoff,
_more) with outputSchema + structured content, tool annotations, relay contract
(verbatim|apply_diff|summarize_ok), compact results + resources (headwaters://...),
progress, async (spec Tasks if available else job_id), elicitation, prompts, roots,
optional sampling (never in local scope), stdio shims → shared streamable-HTTP daemon.
Host-model tool-call eval suite in CI.
ANALYZER v2: rules → CPU embedding kNN over labeled bank → tiny CPU LLM; calibrated
confidence; target ≥90% confident coverage; local learning from verify/thumbs.
CONTEXT ENGINE: tree-sitter symbol index + CPU embeddings (sqlite-vec), git-aware
incremental updates, ignore files, context packing to computed num_ctx.
EXECUTION: diffs by default; optional git-worktree exec with allowlist, timeouts,
no-network best effort, hard-deny destructive ops.
VERIFY v2: grounding → tree-sitter → placeholders → git apply --check → project checks
in worktree → schema; machine-readable failure reasons feed escalation.
HOOKS: per-host installers where lifecycle hooks exist (verify APIs); pre-prompt
classify → answer/short-circuit or inject context; fail-open except strict-local.
INSTALLER: headwaters install/uninstall --host ... with consent diff + backup; doctor
--host live checks.
HEADWATERSBENCH: reproducible routing/quality/savings/tool-call suites on reference GPU
profiles; publish methodology.
CLEAN ROOM: do not read or copy competitor source code; maintain docs/PROVENANCE.md;
permissive dependencies only in closed packages.
```
