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
- LiteLLM releases are frequent and the stable line lags the RC line. Pin an exact version in `uv.lock`, isolate all litellm use behind `headwaters/core/executor`, and run the mocked contract tests on every bump.

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

