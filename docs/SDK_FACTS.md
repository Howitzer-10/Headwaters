# SDK and tooling facts (read before probing anything)

Read this first. Do not re-read SDK source for a fact listed here. Add a row only if you verified
it, in the same commit as the ticket that found it. After a bump of `mcp`, re-verify the rows
that name it.

## mcp SDK

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| A `Client` declares elicitation only if it has an `elicitation_callback` | read `client/session.py` `_build_capabilities` | mcp 2.3 | 146 |
| `Client(mode=...)` defaults to `"auto"` (2026-07-28); `"legacy"` forces the handshake era | read `client/client.py` | mcp 2.3 | 146 |
| On 2026-07-28 a server cannot send a request mid-call (`can_send_request` is False); it returns `InputRequiredResult` and the client retries with `input_responses` + `request_state` | scratch probe | mcp 2.3 | 146 |
| `session.call_tool(..., allow_input_required=True)` returns the `InputRequiredResult` instead of raising | read `client/session.py` | mcp 2.3 | 146 |
| `HEADER_MISMATCH` is -32020; `MODERN_PROTOCOL_VERSIONS == ('2026-07-28',)` | ran it | mcp 2.3 | 146 |
| A host cancelling a call cancels the shim handler task; the SDK client then sends its own cancel to the daemon | `tests/test_shim_cancel.py` | mcp 2.3 | 146 |
| A bare low-level `Server` needs `on_list_tools`, or `Client.call_tool` fails while validating the output | scratch probe | mcp 2.3 | 146 |
| `ElicitResult.action` is `accept`, `decline` or `cancel` | read `mcp_types/_types.py` | mcp 2.3 | 146 |
| `ServerSession.elicit_form(message, requested_schema, related_request_id=)` asks a handshake-era host | read `server/session.py` | mcp 2.3 | 146 |
| `NoBackChannelError` is a subclass of `MCPError` | ran it | mcp 2.3 | 146 |
| `Client.call_tool(name, arguments, meta=...)` takes a per-call `RequestParamsMeta` (keyword-only), which is how a probe sends the client identity block the daemon reads | read `client/client.py` | mcp 2.3 | HDW-402 |

## Windows and repo tooling

| Fact | Verified by |
| --- | --- |
| `Set-Content -Encoding utf8` adds a BOM; write files with the editor tool or `[System.IO.File]` | hit it in HDW-146 |
| The editor tool will not overwrite an existing file: create the first chunk, then use `insert_line` | hit it in HDW-146 |
| To see a crash's stderr, run `cmd /c "uv run python x.py > out 2>&1"` and read `out` | hit it in HDW-146 |
| Regenerate the rules schema with `uv run python -m headwaters.core.rules_io --write` | ran it in HDW-146 |
| Tests that count tools (`test_more_disclosure.py`, `test_host_eval.py`) need updating when an advanced tool is added | hit it in HDW-146 |
| A foreground command over 30 s times out; start long pytest runs with `Start-Process` and poll the log | hit it in HDW-146 |

## litellm and sqlite3

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| After `_load_litellm()` every logger whose name contains `litellm` (the three in `LITELLM_LOGGERS` exist) has no `StreamHandler` on `sys.stdout`, and importing writes nothing to stdout | `tests/test_litellm_quiet.py::test_real_litellm_loggers_have_no_stdout_handler` (subprocess) | litellm 1.106.6 | HDW-152 |
| `PRAGMA journal_mode = WAL` on a non-WAL database raises `sqlite3.OperationalError: database is locked` at once while another connection holds `BEGIN EXCLUSIVE`, even with `timeout=0`; it succeeds after the holder commits | `tests/test_state_records.py::test_enable_wal_retries_until_the_lock_is_released` | Python sqlite3 (stdlib) | HDW-152 |

## openai SDK

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| `AsyncOpenAI(base_url="http://127.0.0.1:4000/v1", http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)))` drives an ASGI app in-process: `chat.completions.create(..., stream=True)` parses our SSE frames (role/content/finish/usage chunks and `data: [DONE]`), and a JSON error body surfaces as `BadRequestError` (400), `NotFoundError` (404) or `InternalServerError` (502) with the body `message` in `str(exc)` | `tests/test_gateway_openai.py` | openai 2.54.0 | HDW-301 |
| An SSE chunk carrying an unknown top-level key (ours: `headwaters`) still parses: the streaming `ChatCompletionChunk` keeps the known fields and tolerates the extra one, so additive per-chunk metadata does not break the client | `tests/test_gateway_openai.py` (the SDK stream tests run against the chunks HDW-302 adds to) | openai 2.54.0 | HDW-302 |

## anthropic SDK

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| `anthropic` 2.4 speaks `httpx2`; `AsyncAnthropic(http_client=...)` raises on an `httpx` client (`_reject_httpx_object`). An in-process test passes `http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=...))`, and `max_retries=0` keeps the 5xx checks from backing off | `tests/test_gateway_anthropic.py` | anthropic 2.4.0 | HDW-303 |
| The stream parser skips any SSE frame with no `event` field (`_streaming.Stream.__stream__`), so every frame must be `event: <name>`; an `event: error` frame raises the status error built from the frame's JSON (`{"type": "error", "error": {...}}`), which is how a mid-stream failure reaches the client | read `anthropic/_streaming.py`; `tests/test_gateway_anthropic.py` | anthropic 2.4.0 | HDW-303 |
| Required response fields: `Message` needs `id`, `content`, `model`, `role`, `type`, `usage`; `Usage` needs both `input_tokens` and `output_tokens` (in `message_start` and in the final `message.usage`); `MessageDeltaUsage.output_tokens` is required and `input_tokens` optional; `ToolUseBlock` needs `id`, `input`, `name` | scratch probe; `tests/test_gateway_anthropic.py` | anthropic 2.4.0 | HDW-303 |
| `client.messages.stream(...)` is an async context manager, and the *entered* value carries `text_stream` and `get_final_message()`; the assembled message parses an `input_json_delta` into the tool block's `input` dict. `with_raw_response.create(...)` is awaitable and its `.parse()` is a coroutine | `tests/test_gateway_anthropic.py` | anthropic 2.4.0 | HDW-303 |
| `DEPRECATED_MODELS` in `anthropic/_data/deprecated_models.py` warns on every call that names a deprecated model (`claude-sonnet-4-5` does), so a test should pin a current name | saw the `DeprecationWarning`; read the table | anthropic 2.4.0 | HDW-303 |
| `keyring.set_password`/`get_password`/`delete_password` are module-level and go to the current backend; `get_password` returns `None` for a missing entry and `delete_password` raises `keyring.errors.PasswordDeleteError` | scratch probe; `tests/keys_support.py` | keyring 25.7.0 | HDW-401 |
| `keyring.set_keyring` demands a real `KeyringBackend` subclass (a duck-typed fake is rejected), so a test hands its fake backend to `KeyStore(backend=...)` instead of installing it | scratch probe | keyring 25.7.0 | HDW-401 |
| `keyring` ships `py.typed` and the default backend on Windows is `WinVaultKeyring` (Windows Credential Manager), which is why a test must never leave `backend=` unset | read `keyring/backends/Windows.py`; scratch probe | keyring 25.7.0 | HDW-401 |
## Host tool config files (HDW-403)

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| VS Code reads MCP servers from `.vscode/mcp.json` as `{"servers": {name: {"type": "stdio", "command": ..., "args": [...], "env": {...}}}}`; a fixed launcher needs no `inputs` prompt | official VS Code MCP docs (code.visualstudio.com, customize > MCP servers) | docs as of 2026-11 | HDW-403 |
| VS Code BYOK custom endpoints live in `<userData>/User/chatLanguageModels.json` (`%APPDATA%\Code\User\` on Windows) as a JSON array of `{name, vendor, apiKey, models: [{id, name, url, maxInputTokens, maxOutputTokens, toolCalling, vision}]}`, `url` being the full `/chat/completions` URL; the model appears in the picker only when `chat.agentHost.byokModels.enabled` is true | official VS Code docs, language models > bring your own key | docs as of 2026-11 | HDW-403 |
| A GitHub Copilot enterprise policy can switch BYOK/custom endpoints off for an org, so an installer says so instead of promising the endpoint will be selectable | same VS Code BYOK page | docs as of 2026-11 | HDW-403 |
| VS Code prompt files are `.github/prompts/<name>.prompt.md` with front matter `mode`, `description`, `argument-hint`; parameters are `${input:name}`, and a `$ARGUMENTS` placeholder is not substituted | official VS Code docs, prompt files | docs as of 2026-11 | HDW-403 |
| VS Code custom instructions are `.github/copilot-instructions.md`; a marked block can be added without taking the file over | official VS Code docs, custom instructions | docs as of 2026-11 | HDW-403 |
| OpenCode reads `opencode.json`/`opencode.jsonc`: `provider.<id> = {npm: "@ai-sdk/openai-compatible", name, options: {baseURL, apiKey}, models: {...}}`, `mcp.<name> = {type: "local", command: [cmd, ...args], enabled}`, and `{env:VAR}` interpolates an environment variable inside a value | official OpenCode docs: config, providers, MCP servers | docs as of 2026-11 | HDW-403 |
| OpenCode commands are `.opencode/commands/<name>.md` with front matter `description`, and `$ARGUMENTS` is substituted in the text; its rules file is `AGENTS.md` | official OpenCode docs: commands, rules | docs as of 2026-11 | HDW-403 |
| Claude Desktop reads `%APPDATA%\Claude\claude_desktop_config.json` as `mcpServers.<name> = {command, args}`, and has no prompt-file or rules mechanism | official Claude Desktop MCP quickstart (modelcontextprotocol.io) | docs as of 2026-11 | HDW-403 |
| Cline reads MCP servers from `~/.cline/data/settings/cline_mcp_settings.json` (`CLINE_DATA_DIR` overrides the data dir, `CLINE_DIR` the install dir) as `mcpServers.<name> = {command, args, disabled, autoApprove, timeout}`; project rules go in `.clinerules/` or `AGENTS.md`, and workflows are `.cline/workflows/<name>.md`, invoked as `/<name>.md` | official Cline docs: MCP, rules, workflows | docs as of 2026-11 | HDW-403 |
| `headwaters-mcp` is a console script and may sit outside the interpreter's `Scripts`/`bin` folder (pipx, `uv tool`, a frozen build), so an installer writes a host config only after checking that the launcher's path exists | scratch probe of this venv; `tests/test_installer_core.py` | repo 0.1 (HDW-403) | HDW-403 |

## itch.io packaging (HDW-405)

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| An itch build is pushed with `butler push <dir> <user>/<game>:<channel>`, and the channel name is whatever the push names (`windows-beta` here), so the manifest and the push script must agree on it | official butler docs (itch.io/docs/butler) | docs as of 2026-11 | HDW-405 |
| A payload root containing `.itch.toml` is what gives the itch app its launch actions; the file is pushed as part of the build, so its presence is also how the app knows it manages this install | official itch app docs, `.itch.toml` manifest | docs as of 2026-11 | HDW-405 |
| `.itch.toml` holds `[[actions]]` entries with `name`, `path`, `args`, `platform` and `scope`; `path` is resolved relative to the manifest's own folder, which is why every action here names `runtime/uv.exe` rather than an absolute path | official itch app docs, `.itch.toml` manifest | docs as of 2026-11 | HDW-405 |
| The working directory an action is launched in is **not** documented, so the actions pass relative paths only and the CLI is given an explicit `--project runtime`; verifying it needs `butler validate` plus one real install (FU-405-4) | official itch app docs (no cwd statement) | docs as of 2026-11 | HDW-405 |
| Inno Setup 6 installs per user with `PrivilegesRequired=lowest` and `DefaultDirName={localappdata}\...`; `[UninstallRun]` can call `schtasks`, and `CurUninstallStepChanged(usUninstall)` can run an `Exec` before the files go, which is where the optional state/key removal runs | official Inno Setup 6 documentation (setup directives, UninstallRun, Code events) | Inno Setup 6 | HDW-405 |

## Linux packaging (HDW-406)

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| A per-user unit lives in `$XDG_CONFIG_HOME/systemd/user/`, i.e. `~/.config/systemd/user/` when the variable is unset, and is driven by `systemctl --user ...`; its manager runs as the user, so no unit and no step needs root | official systemd docs: systemd.unit, "Unit load path" | docs as of 2026-11 | HDW-406 |
| `RestartPreventExitStatus=` takes exit-status definitions (`2 3`) and holds a `Restart=on-failure` unit back from restarting on those codes, which is how a refused configuration or a held lock is kept from looping | official systemd.service docs, `RestartPreventExitStatus=` | docs as of 2026-11 | HDW-406 |
| `ExecStart=` takes exactly one command (`Type=oneshot` is the exception), so `headwaters start` — which detaches and exits — is the wrong entry point and `python -m headwaters.daemon` in the foreground is the right one | official systemd.service docs, `ExecStart=` | docs as of 2026-11 | HDW-406 |
| Unit-file quoting follows the XDG desktop-entry rules: a whole word may be wrapped in double quotes, quotes are removed, C-style escapes are supported and backslashes must be doubled (`\\`, `\"`), so a path with a space or a backslash has to be escaped, not just wrapped | official systemd docs: systemd.syntax, "Quoting" | docs as of 2026-11 | HDW-406 |
| `systemctl enable --now <unit>` enables the unit and starts it in one command, while `enable` alone only writes the `WantedBy=` symlink and leaves starting to a second call | official systemctl docs, `enable` | docs as of 2026-11 | HDW-406 |
| `loginctl enable-linger <user>` starts that user's manager at boot and keeps it after the last session closes; without linger a user unit stops at logout, and the manager only exists while a session does | official loginctl docs, `enable-linger` | docs as of 2026-11 | HDW-406 |

## Cloud provider APIs (HDW-514)

All rows read from each provider's own public docs or public endpoints on 2026-11-03. A row here is the only place a base URL, env var or free-tier claim in `catalog.providers` is allowed to come from.

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| NVIDIA NIM: base `https://integrate.api.nvidia.com/v1`, `POST /chat/completions`, bearer auth, key env `NVIDIA_API_KEY` | NVIDIA API reference and its examples (docs.api.nvidia.com) | docs as of 2026-11 | HDW-514 |
| NVIDIA `GET /v1/models` answers without a key and returns only `id`, `object`, `created`, `owned_by` (85 models), so it carries no capability or tier data and cannot check a key | anonymous GET, read the body | 2026-11-03 | HDW-514 |
| NVIDIA: every model on build.nvidia.com is free to prototype with, up to 40 requests per minute on most models, no per-token billing; the free endpoints are trial-only and logged | help-center FAQ `https://build.nvidia.com/api/runtime/content/help-center-faq.yaml`; Kilo docs for the trial terms | docs as of 2026-11 | HDW-514 |
| Hugging Face: base `https://router.huggingface.co/v1`, bearer `HF_TOKEN` with the "Make calls to Inference Providers" permission | Inference Providers docs and security-tokens docs | docs as of 2026-11 | HDW-514 |
| Hugging Face `GET /v1/models` answers without a key: per model `providers[]` with `status`, `context_length`, `pricing.input/output` (USD per million tokens), `is_free`, `supports_tools`, `supports_structured_output`, plus `architecture.input_modalities/output_modalities`; no model had `is_free: true` (127 models) | anonymous GET, read the body | 2026-11-03 | HDW-514 |
| Hugging Face `GET https://huggingface.co/api/whoami-v2` is in the Hub OpenAPI spec and answers 401 without a token, so it is a safe key check | Hub OpenAPI spec; anonymous GET | 2026-11-03 | HDW-514 |
| Hugging Face free accounts have no included Inference Providers credits | pricing page of the Inference Providers docs | docs as of 2026-11 | HDW-514 |
| Mistral: base `https://api.mistral.ai/v1`, bearer `MISTRAL_API_KEY`; `GET /v1/models` answers 401 without a key and lists models with `capabilities` (`completion_chat`, `function_calling`, `vision`) and no context field | Mistral API docs; anonymous GET | docs as of 2026-11 | HDW-514 |
| Mistral documents a free mode with usage and rate limits, but nothing in the API distinguishes a free key from a paid one | Mistral quickstart | docs as of 2026-11 | HDW-514 |
| Pollinations: OpenAI-compatible base `https://gen.pollinations.ai/v1`, bearer key `POLLINATIONS_API_KEY` (secret keys start `sk_`); spend is metered in Pollen | `https://gen.pollinations.ai/llms.txt`, OpenAPI spec | docs as of 2026-11 | HDW-514 |
| Pollinations `GET /v1/models` answers without a key (about 355 KB) with `id`, `input_modalities`, `output_modalities`, `paid_only`, `tools`, `reasoning`, `context_length`, `pricing` (in Pollen) | anonymous GET, read the body | 2026-11-03 | HDW-514 |
| Pollinations documents no keyless generation (absence check across `llms.txt`, the OpenAPI spec and the docs pages) | read all three | 2026-11-03 | HDW-514 |
| Kilo Gateway: base `https://ai-gateway.kilo.ai/api/v1`, bearer `KILO_API_KEY`; `GET /models` answers without a key (379 models, OpenRouter-shaped: `architecture`, `context_length`, `supported_parameters`, `pricing`, `isFree`) | `https://kilo.ai/docs/gateway/api-reference`; anonymous GET | 2026-11-03 | HDW-514 |
| Kilo: models marked free (`:free` suffix, `isFree: true`) work without a key at 200 requests per hour per IP; paid models need a key | Kilo gateway docs | docs as of 2026-11 | HDW-514 |
| OpenCode: no page shows that a key changes the 403 non-OpenCode clients get for free-tier models (absence check; the docs say free-tier chat models require OpenCode) | opencode.ai/docs/zen and the Console pages | docs as of 2026-11 | HDW-514 |
| litellm's `openai/<model>` route with an `api_base` and no `api_key` falls back to `OPENAI_API_KEY`, so a registered provider with no key must send a placeholder instead of nothing | litellm docs for OpenAI-compatible endpoints (not run live) | litellm as pinned | HDW-514 |

## OpenCode providers (HDW-515)

Read on 2026-11-03 from OpenCode's own Console and Go docs and from anonymous GETs of both listing
endpoints. Two products, two endpoints, one key variable.

| Fact | Verified by | Version | Ticket |
| --- | --- | --- | --- |
| OpenCode Console and OpenCode Go are separate OpenAI-compatible chat roots: Console `https://opencode.ai/inference/openai/v1`, Go `https://opencode.ai/inference/go/openai/v1`; both take a bearer token, and one key variable covers both | Console and Go docs pages (their path examples) | docs as of 2026-11 | HDW-515 |
| The model listing is **not** under the chat root: `GET https://opencode.ai/inference/v1/models` answers 200 with 80 models and `GET https://opencode.ai/inference/go/v1/models` answers 200 with 28 models, while `GET …/openai/v1/models` is 404 on both, so a listing URL is a catalog field and is never derived from the base URL | anonymous GET, read the status and body | 2026-11-03 | HDW-515 |
| Both listings answer 401 `{"error":{"type":"server_error","message":"Invalid credential"}}` to a bogus `Authorization: Bearer …`, so each listing doubles as a key check (and is still 200 anonymously, so it is not a free-tier signal) | anonymous GET with a fake key | 2026-11-03 | HDW-515 |
| Listing shape: `id`, `name`, `canonical_id`, `family`, `api.<family>.base_url`, `capabilities.tools.supported`, `capabilities.structured_output`, `capabilities.reasoning.supported`, `modalities.input/output`, `limit.context/output`, `cost.text.input/output.price` (USD per million tokens) | read the body | 2026-11-03 | HDW-515 |
| Free-tier ids end `-free` and carry cost 0/0; the Console listing has 9 (`beam-free`, `crouton-alpha-free`, `deepseek-v4.1-flash-free`, `flint-40b-free`, `glm-5.3-flash-free`, `mimo-v2.6-flash-free`, `nemotron-3.5-lightning-free`, `nemotron-3.5-super-vl-free`, `pulse-alpha-free`). The client also shows "Nemotron 3 Ultra Free", which the listing does not carry, so the listing — not the picker — is what code reads | read the body; client observation for the missing one | 2026-11-03 | HDW-515 |
| Console docs, verbatim: "Free-tier chat models are only available from within OpenCode; other clients receive HTTP 403, even with an API key." | Console inference and harnesses pages | docs as of 2026-11 | HDW-515 |
| OpenCode Go is a paid subscription (Go $10/mo, Go Plus $40/mo, Go Teams $20/member/mo); its listing includes `beam-free` and `iceman-contributor-free` at cost 0/0 | Go docs page; anonymous GET of the Go listing | docs as of 2026-11 / 2026-11-03 | HDW-515 |
| Whether a real Console key reaches the `-free` ids from outside the OpenCode app: **not verified: no key present** in the environment for this ticket. The docs say a key does not change the 403, so nothing here depends on it | absence check (docs) plus no key in the environment | 2026-11-03 | HDW-515 |
