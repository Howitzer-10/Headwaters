# Decisions

| ID | Decision | Status |
| --- | --- | --- |
| D1 | Open-source Lite? Open. Default is closed until real users exist; decide before the first public release. Architecture keeps both options possible. | Open |
| D2 | Primary platform: Windows 10/11 x64 first, Linux second, macOS best-effort. | Decided |
| D3 | Pro pricing: no discount for a staged Pro; early-access price rising at 1.0. | Superseded by D6 (no editions, free beta) |
| D4 | Model catalog kept in a separate repo; may be made public on its own. | Decided |
| D5 | Headwaters is one self-contained product (Gateway, MCP, Hooks on one core), with no dependency on or bundling with llm-router or any other router. | Decided |
| D6 | Launch as a free beta with every feature included; decide at 1.0 whether to split into Lite and Pro, using the evidence in v3.5 section E. | Decided |
| D7 | Gateway build: **thin FastAPI gateway using the `litellm` SDK (Router + `litellm.anthropic.messages`)**, not the LiteLLM proxy with hooks. Rationale below. | Decided (2026-10-30, HDW-002) |
| D8 | MCP server SDK: **`mcp` v2 (`mcp>=2.3,<3`)**, using `MCPServer`, with its legacy-client path for hosts still on pre-2026-07-28 revisions. v1 is not used. | Decided (2026-10-30, HDW-002) |
| D9 | MCP workspace and sampling: **roots are optional**, and the configured workspace path is the primary boundary for path reads. **Sampling is not built** (HDW-905 deferred). Elicitation goes through the SDK's multi-round-trip mechanism, with a plain-text confirmation fallback. | Decided (2026-10-30, HDW-002) |
| D10 | Escalation in core: **core gets one bounded "verified retry"**. When a mandatory verification fails, retry once on the next *stronger* candidate inside the effective scope. The full verify-then-escalate loop (failure reasons in the prompt, max_escalations, budget, optimistic rung, learned pass rates) stays ★ (HDW-606). | Decided (2026-10-31, HDW-119) |
| D11 | Sticky sessions in core: **core gets basic stickiness**. Inside a session, keep the model that last answered successfully while it stays usable and in scope; switch only on a wall, a scope or lock change, a failed verification, or the hard filter. Handoff packets, summaries and context-threshold re-tiering stay ★ (HDW-607). | Decided (2026-10-31, HDW-119) |
| D12 | `headwaters_think` in 0.1: **ship it in core as single-model reasoning** (best reasoning-capable model in scope, reasoning on, reasoning text never returned or stored). Fusion `deliberate` and duck are ★ 1.x modes of the same tool. | Decided (2026-10-31, HDW-119) |
| D13 | Ollama cloud models are not routable in 0.1: **Headwaters never routes to Ollama `:cloud` / `-cloud` models, in any tier.** Discovery drops them (never `local`, never `free_cloud` or `paid`), and the executor refuses them as defence in depth. Owner answer to HDW-119 question 1 ("never"). Where HDW-121 says "`tier=free_cloud`/`paid` per catalog, or drops them", only "drops them" applies. | Decided (2026-10-31, owner, HDW-119) |


## Deferred tickets (do not build)

Every ticket packet tells the session to treat this list as binding. Add a line here to defer a ticket; remove it to reinstate.

| Ticket | Deferred by | Reason |
| --- | --- | --- |
| HDW-905 MCP sampling tier | D9 (2026-10-30) | Sampling is deprecated in MCP spec 2026-07-28 and could route local-scope prompts to a host model. |

## D7 detail: gateway build decision

**Verified (see ASSUMPTIONS.md A-LLM-1..5):** the LiteLLM proxy can rewrite the model before the call (`async_pre_call_hook`), observe failures (`async_post_call_failure_hook`, failure callbacks), serve `/v1/messages`, and fall back to another deployment when a stream fails before its first chunk. So the proxy *could* do the job. The decision rests on fit, not capability.

**Why the thin gateway wins**
1. **Fail-closed local scope.** Headwaters must guarantee that in `local` scope no cloud client is constructed. In the proxy, the cloud deployments are built from `config.yaml` at startup, so that guarantee depends on request-time hook logic. In our own gateway the scope resolver decides which clients exist at all, so the guarantee is structural and easy to test (HDW-306).
2. **Routing lives in our core.** Scope x strategy x locks, wall classification, quota cooldowns and the catalog are core modules. The proxy's Router would duplicate or fight them. With the SDK, the core picks the deployment and litellm only performs the call.
3. **Pre-first-token failover under our control.** We need our own rule: retry on the next candidate until the first streamed token, then never switch mid-stream. We implement that around `litellm.acompletion(stream=True)`, with no dependency on proxy-internal fallback behaviour (mid-stream fallback is a proxy feature we do not want).
4. **Footprint and attack surface.** The proxy brings its own server, auth, DB and admin stack that we would have to disable. We bind 127.0.0.1 only, have no multi-user auth, and keep state in our own SQLite.
5. **One process, three engines.** The gateway shares the core in-process with the MCP daemon. A separate proxy process would add a second hop and a second config source.

**Costs accepted**
- We write the OpenAI and Anthropic route handlers, SSE streaming and header handling ourselves (HDW-301 to HDW-304). `litellm.anthropic.messages.acreate` covers the Anthropic wire format and removes most of the translation work.
- LiteLLM releases are frequent and the stable line lags the RC line. Pin an exact version in `uv.lock`, isolate all litellm use behind `headwaters/core/executor` (enforced by an import-linter external-package contract, HDW-124), and run the mocked contract tests on every bump.

**Revisit if:** the Anthropic or OpenAI route surface grows beyond what we want to maintain, or if LiteLLM changes the SDK `anthropic.messages` API incompatibly.

## D8 detail: MCP SDK v2

- v2 is the stable line (2.3.0, 2026-10-02) and speaks spec 2026-07-28. v1 is maintenance-only.
- Use `from mcp.server import MCPServer` and the wire types from `mcp_types` (snake_case fields). Do not copy v1 `FastMCP` examples.
- Keep all SDK imports inside `headwaters/mcp/` so a later SDK change touches one package.
- Pin the range `mcp>=2.3,<3`; HDW-202 adds it to `pyproject.toml`.
- Hosts on older spec revisions are served through the SDK's legacy-client path. HDW-208 tests both modern and legacy clients.

## D9 detail: roots, sampling, elicitation

Spec 2026-07-28 deprecates roots and sampling and removes server-initiated requests (see ASSUMPTIONS.md A-MCP-5, A-MCP-8, A-MCP-9).

- **Workspace boundary (HDW-203):** the primary boundary is a workspace path configured in Headwaters (`.headwaters.yaml` / daemon config) or passed by the host shim. Files-by-path reads are refused outside it. If a host does send roots, they may only **narrow** the boundary, never widen it. Nothing depends on roots being present.
- **Sampling (HDW-905):** deferred, not built in the beta. The host model is not used as a routing tier. Revisit only if the spec reinstates a supported replacement. This also removes a path that could send local-scope prompts to a host model.
- **Elicitation (HDW-205, HDW-206):** spend confirmation and scope loosening use the SDK's multi-round-trip mechanism where the host supports it. Otherwise the tool returns a structured "confirmation required" result with the exact follow-up call, and nothing is spent or loosened until the user confirms. Scope loosening stays user-only.
- **Tasks:** keep the `job_id` pattern as the baseline (HDW-206). Return an MCP Tasks handle only when the client declares the Tasks extension.

### D10 detail: escalation in core

**Decision.** Core adds one bounded retry, only when verification was mandatory
(`verify_required`: below the floor, optimistic, best-effort, local below the floor) and failed.
The retry goes to the best remaining candidate whose task score is *higher* than the failed one's,
inside the same effective scope. It never leaves the scope, it counts against `max_attempts`, and
it doesn't apply once a stream has sent its first token. With no stronger candidate, the answer
comes back flagged `verify_failed`, as today. A registered ★ `EscalationPlugin` replaces the rule.

**Reason.** Once local scope serves below-floor answers (HDW-123, needed for G4 and v3.3 D3),
"basic verification, safety baseline" (v3.5 B, core) needs a consequence. A check that fails and
changes nothing is a report, not a gate. One retry on a stronger model is the smallest behaviour
that keeps both "local is usable" and "checked before you see it" true. v3.5 B allows moving ★
behaviour into core; this moves only the smallest slice and leaves the paid differentiators
(failure reasons fed back, multi-step and budget-aware paid escalation, learning) in ★.

**Cost.** One extra model call when a check fails (latency only in local and free scope). Some
router code. The promise can never go back to ★.

**Tickets.** HDW-123, HDW-129, HDW-130.

**Revisit** at the HDW-608 gate with SpillBench numbers. If the core retry already captures most
of the verification-v2 gain, re-scope ★ verification v2; do not weaken core.

**Owner confirmation (2026-10-31, HDW-119 question 2):** yes. `headwaters/local` answers with the best local model, flagged and verified, rather than refusing.

### D11 detail: sticky sessions in core

**Decision.** On the next turn, core puts the session's last *successful* model first while it is
usable: no wall, still in scope, passes the hard filter, and still meets the floor (or is the same
below-floor local choice). It moves on a wall, a scope or lock change, a failed verification, or
when the model drops out of the hard filter (for example, context outgrows it). Without a session
model the ladder runs as today. ★ HDW-607 keeps handoff packets, older-turn and reasoning
summaries, and context-threshold re-tiering.

**Reason.** The gateway is core (v3.5 A), and agent loops resend the whole conversation every
turn. Switching models between turns breaks tool-call conventions, wastes prompt-cache hits and
hands a frontier model's half-finished plan to a cheap model. Today's warm bonus is not
stickiness: 15 points don't hold a model across a ranking change, and it gives a VRAM bonus to
cloud models and erases eviction cost (F15). v3 4.5 "Session rules" already describes this
behaviour; v3.5 B marks only the handoff side as ★.

**Cost.** A session can stay on a model that a fresh decision would not pick. That is limited by
"usable and in scope", and soft-exhaustion counts as a reason to move. Session state becomes
routing state, so the session key matters (a hash of system prompt plus first user message is
good enough).

**Tickets.** HDW-134.

**Revisit** if HDW-305 shows sessions stuck on a degrading free tier, or when HDW-607 lands (★ must
extend the core rule, not replace it).

### D12 detail: `headwaters_think` in 0.1

**Decision.** Ship `headwaters_think` in Beta 0.1 as core. It is a read-only tool that sends the
question (and optional paths) to the best reasoning-capable model in the effective scope with
reasoning on, and returns the final answer plus metadata, never the reasoning text. Local scope
uses local thinking models; free scope uses free reasoning models. Fusion `deliberate` (★ HDW-901:
opt-in, budget-capped, never in local or free scope) and `duck` (★ HDW-902) are modes of the same
tool, registered by features.

**Reason.** v3.4 B1 fixes eight front-door tools; a front door with a missing tool teaches hosts
not to call it. Core already has what this needs: the `reasoning` task and floor,
`ExecOptions.reasoning` for both backends, and reasoning captured apart from content. v3.5 B marks
"Fusion `think`" and the rubber duck as ★, not the tool, so this matches the core/★ line.

**What core must expose before HDW-205:**
- `RouteRequest.task_hint`, which forces `soft.task` (here `reasoning`) without touching the hard
  filter, so the tool doesn't depend on keyword hits.
- `RouteRequest.reasoning`, mapped to `ExecOptions.reasoning` (today it is reachable only by
  passing a whole `options` object).
- A `reasoning_ok` capability on `ModelRow` (Ollama `capabilities: thinking`, OpenRouter
  `supported_parameters: reasoning`) and a matching hard-filter flag, so "reasoning-capable" is
  data, not a name list.
- A documented rule that `ExecResult.reasoning` is never put on the wire or stored, except for the
  short summary v3 4.7 allows.

**Cost.** A small core API addition and one capability column (migration 3). One more tool to
support in 0.1.

**Tickets.** HDW-140.

**Revisit** at the Beta 0.2 gate (HDW-509) if hosts mostly call `think` for work that
`headwaters_do` should do.
