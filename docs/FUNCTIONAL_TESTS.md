# Stage 1 functional tests

These are the hand-run checks for Stage 1: you run the build from source on your own machine, work
through this list, and write down what breaks. There is no packaging here and no clean-machine run;
those are Stage 2. See [PLAN.md](PLAN.md) for the two stages and [DOGFOOD.md](DOGFOOD.md) for how to
install the build in the first place.

How to use the list:

- Run them in order once, on a fresh install. Then re-run the group you touched after any change.
- Each check says what to do, the exact command or UI action, and what you should see.
- A check marked **[needs a key]** cannot be completed without a working provider key, and one marked
  **[needs Ollama]** cannot be completed without a local model pulled. Those are marked instead of
  being written to look runnable.
- A check marked **[cannot be forced]** describes a real behavior that has no honest way to trigger it
  on demand; note it as unverified rather than pretending it passed.
- Record each result as `pass`, `fail` or `skipped (why)`.

Unless a check says otherwise, every command runs from the repository root, and `uv run` supplies the
environment.

## A. Install and hosts

### 1. The CLI answers from a source checkout

Do: ask for the command list.

```
uv run headwaters --help
```

Expected: the command list includes `version`, `pick`, `status`, `start`, `stop`, `daemon-status`,
`doctor`, `mcp-path`, `install`, `uninstall`, `release-gate`, `release-docs`, `scope`, `private`,
`features`, `models`, `catalog` and `keys`, and no traceback. If you get
`UnicodeEncodeError: 'charmap' codec can't encode character '\u2605'`, set `PYTHONIOENCODING` to `utf-8`
and retry; that is a console code page issue, not a Headwaters bug.

### 2. The doctor runs first and explains the machine

Do: run the doctor with no arguments.

```
uv run headwaters doctor
```

Expected: one row per area (Environment, Install, Ollama, Ollama environment, Private Mode, GPU,
Ports, Daemon, Daemon token, API keys, Catalog, Git line endings, MCP launcher, Host config files),
each with a status of `ok`, `warn`, `skip` or `fail`, and a `fix` line wherever something is wrong.
On a source checkout the Install row reads `running from a source checkout`. Warnings about the Ollama
environment variables, `core.autocrlf=true` and a stopped daemon are expected on a fresh machine.

### 3. A dry run changes nothing

Do: preview an install into a scratch folder.

```
New-Item -ItemType Directory -Force "$env:TEMP\hw-scratch" | Out-Null
cd "$env:TEMP\hw-scratch"
uv run --project <repo> headwaters install --host vscode --dry-run
```

Expected: a list of the files it would create or update, each with a diff, then the note
`dry run: nothing was written` and `Nothing changed.` The folder stays empty.

### 4. A real install writes exactly what the dry run showed

Do: install into the same scratch folder, then look at it.

```
uv run --project <repo> headwaters install --host vscode
Get-ChildItem -Recurse -Force
```

Expected: `.vscode\mcp.json`, `.vscode\settings.json`, `.github\copilot-instructions.md` and
`.github\prompts\headwaters-*.prompt.md` exist, and the user `chatLanguageModels.json` under
`%APPDATA%\Code\User` gained a `Headwaters` provider with `headwaters/auto`, `headwaters/free` and
`headwaters/local`. Every file it touched has a `<name>.headwaters-backup` next to it, and
`%LOCALAPPDATA%\Headwaters\install\manifest.json` lists them. It also prints the note about pasting
the token from `daemon.token`, and the note about reloading the VS Code window.

### 5. The host is wired up for real

Do: start the daemon and run the live host probe.

```
uv run headwaters start
uv run headwaters doctor --host vscode
```

Expected: the doctor gains three rows, `round_trip` (the front door tool is listed),
`tool_call` (`headwaters_status` answered) and `scope` (an effective scope was reported), all `ok`.

### 6. Uninstall puts everything back

Do: remove the install, then confirm the folder is clean again.

```
uv run --project <repo> headwaters uninstall --host vscode
Get-ChildItem -Recurse -Force "$env:TEMP\hw-scratch"
```

Expected: the files the install added are gone or restored from their backups, and it names anything
it left alone because you had edited it. `uv run headwaters doctor` warns about the host config again.

## B. Gateway endpoints

The gateway is on the port `headwaters daemon-status` reports (4000 unless it was taken). Read the
token once per shell:

```
$token = (Get-Content "$env:LOCALAPPDATA\Headwaters\daemon.token" -Raw).Trim()
```

### 7. Health answers without a token

```
curl.exe -s -o - -w "`nHTTP %{http_code}`n" http://127.0.0.1:4000/health
```

Expected: `{"ok":true,"gateway":"openai"}` and HTTP 200. No token is needed for this one, which is
what lets a host check the gateway is up before doing anything else.

### 8. The model list needs the token

```
curl.exe -s -o - -w "`nHTTP %{http_code}`n" http://127.0.0.1:4000/v1/models
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -H "Authorization: Bearer $token" http://127.0.0.1:4000/v1/models
```

Expected: the first call is HTTP 401 with `{"ok":false,"error":{"code":"unauthorized"}}`. The second is
HTTP 200 with an OpenAI model list containing `headwaters/auto`, `headwaters/free`, `headwaters/local`
and `headwaters/cloud`.

### 9. The OpenAI face answers, or refuses in its own shape

Put the body in a file so PowerShell cannot mangle the quotes:

```
Set-Content -Path body.json -NoNewline -Value '{"model":"headwaters/auto","messages":[{"role":"user","content":"say hi"}]}'
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data "@body.json" http://127.0.0.1:4000/v1/chat/completions
```

Expected: [needs a key] a normal OpenAI `chat.completion` object, or [needs Ollama] an answer from a
local model inside the same shape. With no model able to serve the request you instead get HTTP 400
`{"error":{"message":"No model can serve this request in the current scope.","type":"invalid_request_error","code":"no_candidates","param":null}}`,
which is a refusal, not a crash.

### 10. Streaming works and ends properly

Do: the same request with `"stream": true`.

```
Set-Content -Path body.json -NoNewline -Value '{"model":"headwaters/auto","stream":true,"messages":[{"role":"user","content":"count to three"}]}'
curl.exe -s -N -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data "@body.json" http://127.0.0.1:4000/v1/chat/completions
```

Expected: [needs a key or Ollama] a stream of `data: {...}` frames, the last content frame carrying a
`finish_reason` and a `headwaters` block, then `data: [DONE]`. Without a usable model the stream fails
over before the first token and you get a single error frame instead.

### 11. The Anthropic face answers, or refuses in its own shape

```
Set-Content -Path body.json -NoNewline -Value '{"model":"headwaters/auto","max_tokens":16,"messages":[{"role":"user","content":"say hi"}]}'
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST -H "x-api-key: $token" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" --data "@body.json" http://127.0.0.1:4000/v1/messages
```

Expected: [needs a key or Ollama] an Anthropic `message` object with `content`, `role`, `model`,
`stop_reason` and `usage`. With no model able to serve it, HTTP 400 with
`{"type":"error","error":{"type":"invalid_request_error","message":"No model can serve this request in the current scope."}}`.

### 12. Token counting answers without a model

```
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST -H "x-api-key: $token" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" --data "@body.json" http://127.0.0.1:4000/v1/messages/count_tokens
```

Expected: HTTP 200 `{"input_tokens":N}` — one field, an integer. `N` is the daemon's own estimate
from the same body `body.json` sends: about 4 characters per token of prose, 3.5 inside code fences
and tool definitions, 800 per image. No model is chosen and no provider is called, so this answers
even when check #11 answers 400 or 529. A body the messages face refuses (invalid JSON, an unknown
content block) is HTTP 400 `{"type":"error","error":{"type":"invalid_request_error",...}}`; it is
never a 404.

### 13. The port stays private

```
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -H "Host: example.com:4000" http://127.0.0.1:4000/health
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -H "Origin: https://evil.example" -H "Authorization: Bearer $token" http://127.0.0.1:4000/v1/models
```

Expected: HTTP 421 `{"ok":false,"error":{"code":"bad_host"}}` for a non-loopback `Host`, and HTTP 403
`{"ok":false,"error":{"code":"bad_origin"}}` for a non-loopback `Origin`. Neither request reaches a
model.

## C. MCP front door

These run from a host that has the MCP server installed (VS Code, Cline, OpenCode or Claude Desktop),
by asking the agent to call the tool. The front door is exactly eight tools: `headwaters_do`,
`headwaters_ask`, `headwaters_check`, `headwaters_think`, `headwaters_status`, `headwaters_scope`,
`headwaters_handoff` and `headwaters_more`.

### 14. The default tool list is the front door only

Do: in the host, ask the agent what Headwaters tools it has.

Expected: exactly those eight names. The rest of the toolbox is hidden behind `headwaters_more` on
purpose, so a host is not flooded with choices.

### 15. `headwaters_status` explains the state in a few lines

Do: `/headwaters-status` in Copilot Chat, or ask the agent to call `headwaters_status`.

Expected: scope, any locks, any walls with their reset times, the warm local model and the savings,
in a few lines. No prompt content appears in the result.

### 16. `headwaters_more` reveals the advanced tools

Do: ask the agent to call `headwaters_more`.

Expected: the nine advanced tools appear: `headwaters_recommend_model`,
`headwaters_should_switch_host`, `headwaters_list_local_models`, `headwaters_quota_status`,
`headwaters_job_status`, `headwaters_job_result`, `headwaters_job_cancel`, `headwaters_pin_model` and
`headwaters_feedback`.

### 17. `headwaters_do` does the work through the router

Do: `/headwaters-do` or ask the agent to have Headwaters write a small function in a scratch file.

Expected: [needs a key or Ollama] a result with the model that was used, its tier, and a `relay`
instruction. With a diff requested, the host applies the diff exactly. Not runnable without a model.

### 18. `headwaters_handoff` saves a packet

Do: `/headwaters-handoff` with a one-line goal.

Expected: a `packet_id` comes back, and `headwaters status` (no prompt content) shows the handoff was
recorded.

## D. Failover across walls

### 19. `pick` shows the ladder without spending anything

Do: a dry run of the routing decision.

```
uv run headwaters pick "refactor this function" --json
```

Expected: the chosen model, its tier, score, the scope in force and the fallback list. It makes no
model call and stores nothing, so run it as often as you like. With an empty database it answers
`no_candidates` and tells you to run `headwaters models refresh`; that is the correct refusal.

### 20. A real failover after a wall

Do: drive a paid or free model until the provider rate-limits or exhausts it, then send another
request.

Expected: `uv run headwaters status` lists the wall with its reset time, and the next request lands on
a different model with the wall named in the decision. **[needs a key]** and **[cannot be forced]**:
there is no CLI switch that fakes a wall, so this only gets verified when a real limit happens.

## E. Scopes, Private mode and secret detection

Run these with the daemon stopped or running; the CLI reads and writes the rules either way.

### 21. Tightening the scope applies at once

```
uv run headwaters scope free
uv run headwaters status
```

Expected: `headwaters scope` reports the change with `changed: true`, and `status` shows the effective
scope `free` with tiers `free_cloud` and `local`. `uv run headwaters scope auto` puts it back.

### 22. Asking for cloud is not asking for paid in this build

```
uv run headwaters scope cloud
uv run headwaters status
```

Expected: the effective scope is `cloud` but the tiers are `free_cloud` only, with the reason
`paid tiers excluded: the paid-scopes feature is not enabled`. Paid routing is a feature that has to
be turned on; this build has no features installed (`uv run headwaters features` says
`features: none installed`). Restore with `uv run headwaters scope auto`.

### 23. Private mode locks the machine down

```
uv run headwaters private on
uv run headwaters status --json
uv run headwaters private off
```

Expected: `private on` sets `private_lock: true` and the effective scope becomes `local` with
`locks: ["private_lock"]`. While it is on, `uv run headwaters pick "hello"` refuses with
`local_unavailable` unless a local model is pulled. `private off` restores the previous scope.

### 24. A key-shaped string forces local

Do: send two prompts through `pick`, the second containing something that looks like a leaked key.

```
uv run headwaters pick "hello there" --json
uv run headwaters pick "here is my key sk-or-v1-<24 random characters EXAMPLE>" --json
```

Expected: the first answers in whatever scope is in force (`no_candidates` on an empty database). The
second is treated as carrying a secret: the scope is forced to local and it answers
`local_unavailable` with the fix `pull qwen3:14b`. The point is that the routing decision changed
because a secret was detected; the matched text is never echoed back to you.

Do not test this with a real key. Use a fake string shaped like one, and keep the word `EXAMPLE` in it
so it is obviously not a credential.

## F. Hardware guard

### 25. VRAM and KV fit, with the computed context

```
uv run headwaters models list --tier local --tokens 1000
uv run headwaters models list --tier local --tokens 32000
```

Expected: [needs models] one row per local model with its size, whether it fits the GPU budget, and
the `num_ctx` the router computed for that request size. A model too large for the budget shows
`no_fit (<reason>)`. With no GPU detected the fit column reads `unknown (no GPU detected)`; with no
models known it prints `No models yet. Run: headwaters models refresh`.

### 26. The warm model is visible

Do: send a local request, then read the status.

```
uv run headwaters pick "hello" --scope local
uv run headwaters status
```

Expected: [needs Ollama] with a local model pulled, the first command names the model, and `status`
lists it as a warm model. Without Ollama both refuse with `local_unavailable`, which is the correct
answer rather than a silent fallback to a cloud model.

## G. Basic verification

### 27. Verification catches a lazy answer and a broken diff

Do: in the host, ask `headwaters_check` to check two things: a diff containing a placeholder such as
`# ... rest of the code unchanged ...`, and a diff whose context lines do not match the file.

Expected: the placeholder is flagged by the laziness scan, and the second diff is rejected by
`git apply --check` with the failing hunk named. A clean diff passes. There is no CLI command for
this: `headwaters_check` is an MCP tool, and the same checks run inside `headwaters_do` results.

## H. Doctor, status page, catalog and keys

### 28. The doctor reflects the locks

```
uv run headwaters private on
uv run headwaters doctor
uv run headwaters private off
```

Expected: the Private Mode row changes from `skip` to the lock being on, and the other rows are
unaffected. The doctor never changes state itself.

### 29. The status page opens and shows the same state

```
uv run headwaters status --open
```

Expected: the browser opens `http://127.0.0.1:4001/` (HTTP 200) and the page shows the effective
scope, the locks, any walls and the warm model. With `dashboard.enabled: false` in `rules.yaml` the
command instead says the dashboard is disabled and tells you how to fix it.

### 30. The catalog says where it came from, and updates only when configured

```
uv run headwaters catalog status
uv run headwaters catalog update
```

Expected: `catalog status` prints `source: seed`, `version: 0.0.0-seed` and
`updates: catalog.url is not configured`. `catalog update` prints
`Catalog update skipped: catalog.url is not configured.` [needs a url] Once you set `catalog.url` in
`rules.yaml`, `catalog update` fetches, and `catalog status` shows the new version, the fetch time and
the etag.

### 31. Saved keys are stored once and never printed

```
uv run headwaters keys set openrouter
uv run headwaters keys list
uv run headwaters keys test
```

Expected: `keys set` prompts with a hidden input and prints only the masked value. `keys list` shows
each alias with its provider and where the value comes from (`not set` for an alias you have not
saved). `keys test` asks the provider whether the key is accepted, and sends no prompt content.
**[needs a key]** for a real result; with no key it reports that there is nothing to test. The secret
lives in the Windows Credential Manager, not in `rules.yaml` or the database.

### 32. Model discovery, then a real route

```
uv run headwaters models refresh
uv run headwaters models list --tier free_cloud
uv run headwaters pick "summarise this function"
```

Expected: `models refresh` re-discovers models from Ollama, OpenRouter, OpenCode and your own
endpoints, and prints how many it found, how many were unscored, how many were removed and which
sources answered. Progress lines ("checking ollama at ...") go to stderr as each source starts, so a
slow network never looks frozen and `--json` on stdout stays parseable; a source that does not answer
inside the 20 second discovery budget is a warning, and the run still succeeds with what it has.
`models list` then shows rows, and `pick` returns a model with its fallbacks. [needs a key or Ollama]:
with no source reachable it reports zero models and `pick` keeps answering `no_candidates`.

## I. A dead tier still reaches a healthy one

### 33. A dead free tier reaches a local model

```
uv run headwaters keys list
uv run headwaters pick "summarise this function"
```

Expected with no cloud key saved (`keys list` says `not set` for the provider) and Ollama running:
`pick` returns a free-cloud model first and a local model next, and the reasons include a tier
fairness line naming both tiers. Send one real prompt through the gateway (or Cline on
`headwaters/auto`): the first attempt fails, the decision record's attempt list shows a
`auth_missing_key` free-cloud attempt followed by a `local` attempt, and the answer arrives. Fail if
every attempt in the record is a free-cloud model.

### 34. The terminal error says what to fix

With no cloud key saved and the local tier unusable (stop Ollama), send one prompt through the
gateway. Expected: an error of a handful of lines, `All N allowed attempts failed.`, then one line per
attempt with its model, tier, provider, failure class and a short reason, then, when a credential is
the cause, the provider named and the fix command (`headwaters keys set openrouter`), and, when a
usable tier was never reached, a line naming it. No key material and no credential-shaped substring
appear anywhere in the text, and the old opaque `Every allowed attempt failed.` is gone.

## What is not covered here, on purpose

- The Windows installer, code signing, the clean Windows VM run, the Linux payload and the clean Linux
  run, the itch.io uploads and the store page: all Stage 2, per [PLAN.md](PLAN.md).
- `uv run headwaters release-gate`: expected to be red during Stage 1. Do not "fix" it.
- Long-run stability under real use: that is what dogfooding is for, and it has no checklist.

## Bug report template

Copy this into a ticket for anything that fails above.

```
Title: <one line: what broke, where>

Build
- commit: <git rev-parse --short HEAD>
- branch: <git branch --show-current>
- version: <uv run headwaters version>

Environment
- Windows version:
- GPU and VRAM (or "none"):
- Ollama version (or "not installed"):
- host: vscode | cline | opencode | claude-desktop | CLI only

What I did
1. <exact command, or the exact clicks and prompts>
2.
3.

What I expected
<the observable result you wanted>

What happened
<the observable result you got, including the HTTP status or the error code>

Doctor output
<uv run headwaters doctor, or the bundle from uv run headwaters doctor --bundle>

Log excerpt
<the relevant lines from %LOCALAPPDATA%\Headwaters\Logs\headwaters.log, trimmed to the failing
minutes, with every secret removed>

Frequency
<always / once / after N requests / only with the Private lock on>
```

Rules for a good report:

- **Never paste an API key, a `daemon.token` or a session token into a report.** Replace it with
  `REDACTED`. The doctor bundle does this for you; hand-written excerpts do not.
- Include the exact error code (`no_candidates`, `local_unavailable`, `unauthorized`, `bad_host`) and
  the HTTP status for a gateway problem; they say more than a screenshot.
- Do not include prompt content you would not share. A description of the prompt shape is enough.
- One failure per report. If two checks fail, file two.
