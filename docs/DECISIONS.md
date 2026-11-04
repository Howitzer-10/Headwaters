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
| D14 | Daemon packages: **`headwaters.gateway` owns the HTTP server** (the only package allowed to import `fastapi`, `starlette` and `uvicorn`), **`headwaters.mcp` owns the MCP SDK** and hands its streamable-HTTP app out as a plain ASGI callable, and a new **`headwaters.daemon` is the composition root** that wires core, gateway and mcp into one process. Nothing imports `headwaters.daemon`; the CLI starts it as a child process. The internal API and health endpoint live in `gateway/server.py`, not under `headwaters/mcp/`. | Decided (2026-10-31, HDW-201) |
| D15 | Beta tester promise: **everyone on the beta tester list gets Headwaters Pro free if a free/Pro split ships at 1.0.** HDW-407's packet offers free Pro or a discount for testers; the free option is chosen, the discount is not promised, and the list itself is a release-gate owner step. | Decided (2026-11-10, HDW-407) |
| D18 | Provider identity: **every surface (CLI, dashboard page, tray menu) reads one registry of plain, lowercase provider names, and a person is never shown an underlying environment variable name.** The registry is `KeyStore.providers()`; it currently holds `anthropic`, `openai`, `openrouter`, `nvidia`, `huggingface`, `mistral`, `pollinations`, `opencode`, `opencode-go` and keyless `ollama` (whatever `providers()` returns is the list; this branch's registry also carries a `kilo` row from the catalog work, FU-503-2), and a provider added there appears on every surface without a second list. `HF_TOKEN` and `NVIDIA_API_KEY` are internal mappings, accepted as input at most, and never printed. No surface keeps its own provider list or label, and `keys set` rejects an unknown name. Why: a person thinks in products, not environment variables, and two surfaces drifting on naming is a support burden. Implemented by HDW-514 (registry owner), HDW-503 (dashboard) and HDW-504 (tray). | Decided (owner, HDW-503 / 504 / 514 packets) |
| D20 | Behaviour over advertisement: **a capability is recorded from an observed tool call, not from what the provider advertises**; `models.overrides` in `rules.yaml` is applied **last**, after catalog and scoring; the gateway answer footer is **opt-in and off by default**. | Decided (2026-11-03, HDW-512) |

Numbering note: D16 has a detail section only. D17 is HDW-513's retry rule, D18 is the provider registry and D20 is HDW-512's behaviour-over-advertisement rule; D19 arrives with the paid-vocabulary doc. The two branches that claimed D17 for different decisions are merged, and this session gave the older one (HDW-512) the number its consolidation uses.

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

**Refinement (2026-11-01, FU-129-2; the owner accepted the triage recommendation):** for an agentic request the retry also keeps tool-call strength. A candidate qualifies only if its task score is above the failed model's **and** its `agentic_tools` score is not below it. Not-below, rather than higher, so a good escalation is not lost. With none left the answer is flagged `verify_failed` and says why. Ticket HDW-154.

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

### D14 detail: daemon package boundary

**Decision.** FU-124-1 / FU-TRI-3 found that `.importlinter` lets only `headwaters.gateway` import
`fastapi` / `starlette` and only `headwaters.mcp` import `mcp`, while the repo layout put the daemon
and its internal HTTP API under `headwaters/mcp/`. Rather than loosen a contract, the work is split
by what each package already owns:

- `headwaters/gateway/server.py`: the Starlette app (`/health`, `/v1/status`, `/v1/shutdown`), the
  loopback `Host` / `Origin` and bearer-token guard, and the `uvicorn` server. D7 already says the
  gateway and the daemon share one process, so the HTTP server belongs to the gateway package.
  `uvicorn` joined that contract because it is part of the web framework.
- `headwaters/mcp/endpoint.py`: builds the SDK `MCPServer` and returns its streamable-HTTP app as an
  ASGI callable plus its lifespan. It imports no web framework, so D8 ("all SDK imports inside
  `headwaters/mcp/`") holds.
- `headwaters/daemon/`: the composition root (lock, rules, ports, background tasks, wiring). It may
  import core, gateway and mcp, but not the SDK or the web framework directly (two
  `allow_indirect_imports` contracts), and nothing may import it (`daemon-is-a-root`).
- `headwaters/core/`: what the CLI and the daemon share and that needs no server: port selection,
  the state file and token, the instance lock, the keep-warm ping, the engine wiring, the daemon
  client and start/stop control.

**Reason.** Every existing contract keeps its meaning and gets a sharper edge instead of an
exception. The CLI never imports the web or MCP stacks (tested), so `headwaters status` stays fast.

**Cost.** One more top-level package, and `headwaters.daemon` appears in the core-is-a-leaf and the
two external-package contracts.

**Tickets.** HDW-201. HDW-202 onward add MCP tools inside `headwaters/mcp/` and routes inside
`headwaters/gateway/`.

**Refinement (2026-11-01, FU-201-9; the owner accepted the triage recommendation):** `headwaters.control` (`doctor`, the status page) and the CLI read daemon state only through `headwaters.core.daemon_client` and `core.daemon_info`, never by importing `headwaters.daemon`. The `daemon-is-a-root` contract already enforces it. HDW-402 adds a `sys.modules` test that importing the doctor loads neither the web framework nor the MCP SDK.

## D15 detail: the beta tester promise

HDW-407's packet leaves one thing to the owner: what a beta tester is promised, either free Pro or
a discount. The choice is **free Pro**, recorded as `promise: testers-pro` in `docs/BETA.md` and
repeated, in the same words, on the itch store page.

- What is promised: everyone on the beta tester list gets Headwaters Pro free if a free/Pro split
  ships at 1.0. Nothing is promised about the price of Pro for anyone else, and nothing is promised
  about a discount tier.
- What the promise depends on: the tester list has to exist. `tester-registration` in the release
  gate is that step, and it stays `BLOCKED` until a person records the system and a count.
- What the gate enforces: exactly one of `promise: testers-pro` or `promise: testers-discount` in
  `docs/BETA.md`, that block repeated in `docs/ITCH_STORE.md`, and no leftover
  "[Pro free / 50% off]" placeholder on the store page. Two tester promises, or none, fail the
  gate; the packet accepts one.
- What does not change: D6 (free beta, every feature included) and D3 (superseded). The free core
  list in `docs/BETA.md` is checked against `headwaters.core.features.CORE_FEATURES`, so the tester
  promise cannot silently pull a core feature behind the paywall it describes.

## D16 detail: two stages, dogfooding first and distribution later

**Decision.** The remaining work is split into two stages, recorded in `docs/PLAN.md` and repeated as
a stage banner in `docs/BETA.md`, `docs/RELEASE_CHECKLIST.md`, `docs/RELEASE_CHECKLIST_LINUX.md` and
`docs/RELEASE_EVIDENCE.md`, in the README and in the ticket backlog:

- **No public beta and no closed alpha.** No 0.1 of any kind is released, and the "Beta 0.x" names in
  the backlog are phase labels for ticket work, not published versions.
- **Stage 1 is what happens now:** the owner runs the build from source on their own machine and
  functionally tests it, and then ticket work continues through Phase 5 (501-509), Phase 6 (601-608)
  and Phase 7 (701-706). No packaging, no installer distribution, no code signing, no clean-machine
  runs, no itch.io pages or uploads, no store copy and no marketing during Stage 1.
- **Stage 2 is distribution and sale, and it starts only when Phase 7 is complete.** It owns the
  Windows installer and its signature, the clean Windows VM run, the Linux payload and its clean run,
  the `windows-beta` and `linux-beta` uploads, the store copy, the 11 owner evidence steps in
  `docs/release/evidence.json`, and a green release gate.
- **The release gate stays red and unmodified until then.** `headwaters release-gate` exits 1 with its
  owner steps blocked. That is the expected Stage 1 state: nobody treats it as a bug, works around it,
  or edits a document so a line turns green, and `headwaters/control/release_gate.py` and its tests
  are not touched.

**Reason.** The owner's question is whether the product is worth taking to market, and that question
is answered by using it, not by packaging it. Every Stage 2 step needs a machine or a person that is
not available until the build is worth releasing, and a red gate that is expected removes the
pressure to fake evidence.

**Cost.** The release documents read as unfinished until Stage 2, the README says plainly that nothing
is published, and the backlog keeps two kinds of work that used to look like one list.

**Tickets.** None. Stage 1 is the functional test pass in `docs/FUNCTIONAL_TESTS.md` followed by
Phases 5-7; Stage 2 is the 11 owner steps.

**Revisit** if the owner's evaluation concludes the product is not worth distributing. Nothing has
been published at that point, so the plan simply stops.

## D17: a request remembers its own failures, and tiers take turns

**Decision.** Two rules govern every retry inside one gateway request, and they are implemented in
`Router._recover`/`_redecide` (`headwaters/core/router.py`), `headwaters/core/router_failures.py` and
`build_ladder`/`interleave_tiers` (`headwaters/core/policy/ladder.py`):

1. **Per-request failure memory.** A model that fails in a request is not chosen again by that
   request. The record lives on `RouteRun.failures` and is discarded with the run: this is not a
   cooldown and it never writes a wall. Walls and backoff stay exactly as HDW-511 left them, so a
   model excluded here is free to serve the next request (a context failure must not punish a model
   that would work on a smaller payload). Retry-class walls (`rate_limit`, `overload`) keep their
   model for the retry and are recorded as failed only if that retry fails too.
2. **Tier fairness.** The ladder is emitted round-robin by tier: every tier in the ladder takes one
   attempt before any tier takes a second. The first appearance of a tier keeps the rung and score
   precedence the policy already decided, and the rotation starts just after the tier attempted last,
   so re-deciding after a tier failed cannot hand that tier the next attempt while an untried tier
   is still available. Deterministic: same pool plus same attempted tiers gives the same order. A
   tier that is out of scope, locked or empty never appears, so fairness cannot widen the scope.
3. **Fast-fail an unauthenticated cloud provider.** When the (cached) key store says a cloud provider
   has no key, every model of that provider is excluded for the request instead of consuming an
   attempt each. No network call is added to the hot path.
4. **The terminal error is honest and bounded.** `Every allowed attempt failed.` is gone. The message
   is a summary line, up to four attempt lines (`attempt N: model (tier, provider) - failure`), the
   missing-key fix command when a credential is the cause, and a line naming a healthy tier that was
   never tried. Credential-looking substrings are redacted; key material is never included.

**Reason.** Live evidence (HDW-513, decisions 9-12): with no cloud key at all, four attempts all
re-selected the same top free model, failed the same 401 four times and never reached the healthy
local tier that had answered 90 s earlier. A trivial prompt therefore spent 21 minutes and ended in
a message the user could not act on. Both defects are properties of the retry loop, not of the
models.

**Cost.** Fallback order between tiers changes (a second candidate of a tier now waits for the other
tiers' first), which the policy golden tests pin explicitly. Attempt lines and reasons are longer by
a few fixed strings.

**Tickets.** HDW-513. **Revisit** if a provider grows per-model authentication (then the provider-level
fast-fail must narrow to the models that share the credential).

## D20 detail: observed capabilities, user overrides, opt-in footer

**Decision.** Three rules came out of one defect: Cline showed a raw
`{"name": "skills", "arguments": {"skill": "test"}}` block instead of an answer. `qwen2.5-coder:14b`
advertises `completion`, `tools` and `insert` on this Ollama version, `POST /api/chat` with a tools
array answers `tool_calls: null` and the call as `content` text, and the router had chosen it because
`tools_ok` came from that advertisement.

1. **A capability is what a model did, not what it says.** `tools_ok` is written only by a bounded
   probe — `headwaters models verify-tools`, or `models refresh --verify-tools` — that offers one
   synthetic tool and requires a structured `tool_calls` entry naming that tool with empty content.
   The observation is stored in `capability_probes` and read at candidate-build time, so a later
   refresh that re-advertises the capability cannot undo it. The probe runs on an explicit command,
   never in a request path, because it loads a model; an unreachable model, a refusal by the private
   lock or airplane mode, and an answer cut off at the token cap record nothing, so no model becomes
   `tools_ok` without an observation and none is refused on no evidence.
2. **The user has the last word.** `models.overrides` (`tier`, `scores`, `tools_ok`, `json_ok`,
   `ctx`) is applied to the candidate list after the catalog and the scoring pass, as the
   `ModelOverride` docstring always promised, and the existing rules watcher makes an edit take
   effect without a daemon restart. An override the schema rejects fails the file with a clear
   error rather than being silently ignored.
3. **The footer is opt-in and it is body text.** `gateway.footer.enabled` defaults to `false`: the
   line naming the model, tier, tokens, cost and latency changes the assistant content, so no
   existing client or test sees extra text until it is asked for. When it is on it is appended to the
   content of a non-streaming answer, emitted as a final content delta for a streaming one, never
   added to a `tool_calls` finish or an error response, and never counted in `usage`.

**Reason.** The router's promise is that a request lands on a model that can serve it. An advertised
capability cannot keep that promise (a vendor's capability list is a statement about a version, not
about this build on this hardware), while an observation can. The overrides exist so that when the
observation is wrong the owner can correct it without waiting for a Headwaters release, and the
footer answers the owner's requirement — say what answered, and leave room for cost detail — without
breaking the clients that already parse the body.

**Cost.** A probe loads a model, so its first answer is slow and it must stay off the request path;
until someone runs it, `tools_ok` is still the advertisement. An override outlives the reason it was
added: removing one needs `headwaters models refresh` to restore what discovery finds. A body footer
becomes part of the conversation history a host may echo back as context, which is why the default is
off and why a header (`X-Headwaters-Model`) is the invisible alternative when that matters; a paid
answer's `cost` is priced from the catalog and reads `unknown` when the catalog has no price, so it is
a routing-cost hint, not a bill.

**Tickets.** HDW-512. The composition-honest error text stays HDW-511's.

