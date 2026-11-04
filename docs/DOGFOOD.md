# Dogfooding: run the current build from source

This is the owner's Stage 1 page: run the current build on your own Windows machine, from the
repository, with no packaging involved. Nothing here builds an installer, signs anything or touches a
store page. For what the stages are, read [PLAN.md](PLAN.md); for what to check by hand, read
[FUNCTIONAL_TESTS.md](FUNCTIONAL_TESTS.md).

If a step below cannot be done from source today, this page says so instead of inventing it.

## What you need

- Windows 10 or 11, PowerShell.
- [uv](https://docs.astral.sh/uv/) on `PATH`. `uv` also fetches the Python interpreter, so you do not
  install Python separately (this project needs 3.11 or newer).
- Git, and a checkout of this repository.
- At least one provider key for the cloud tiers. OpenRouter is the cheapest way in, because its free
  models cost nothing: get a key from openrouter.ai and save it with `headwaters keys set`. A paid
  key is optional and only needed for the paid tier.
- Optional: [Ollama](https://ollama.com/) for the local tier. Without it, `local` scope has nothing to
  route to, and the doctor says so.

## 1. Install the dependencies

From the repository root:

```
uv sync
```

That creates `.venv` and installs the runtime plus the dev group. Every command below is then run
through `uv run`, which uses that environment, so you never activate anything by hand.

Check the CLI answers:

```
uv run headwaters --help
```

If a command dies with `UnicodeEncodeError: 'charmap' codec can't encode character '\u2605'`, your
console code page is not UTF-8. Set `PYTHONIOENCODING` to `utf-8` for that shell (or
`setx PYTHONIOENCODING utf-8` once) and run it again.

## 2. Run the doctor first

```
uv run headwaters doctor
```

The doctor is the first thing to run after any install, update or reset, and the first output to
attach to a bug report. It changes nothing: it reads the machine and prints what it found. Each row
has a status of `ok`, `warn`, `skip` or `fail`, and a `fix` line when there is something to do.

The rows are:

| Row | What it tells you |
|-----|-------------------|
| Environment | Headwaters version, Python version, platform, state directory |
| Install | how this copy was installed (`running from a source checkout` for `uv run`) |
| Ollama | whether Ollama answers, and its version |
| Ollama environment | `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_MAX_LOADED_MODELS` |
| Private Mode | the current lock, or `skip` when it is off |
| GPU | the card, its VRAM, the profile, the reserve and the budget the router is allowed to use |
| Ports | the api, gateway and dashboard ports, free or taken |
| Daemon | running or not |
| Daemon token | present or not (it is written on the first start) |
| API keys | saved keys, or `skip` with `no keys saved; local models only` |
| Catalog | which catalog is active and whether updates are configured |
| Git line endings | warns when `core.autocrlf` would rewrite patches |
| MCP launcher | the exact command a host will launch |
| Host config files | which hosts are wired up, and which are missing |

Useful variants:

```
uv run headwaters doctor --host vscode
uv run headwaters doctor --bundle
uv run headwaters doctor --json
```

`--host <id>` adds three live rows: `round_trip` (the daemon answers `tools/list` and the front door
is there), `tool_call` (`headwaters_status` answers) and `scope` (`headwaters_scope` reports an
effective scope). Host ids for this flag: `vscode`, `copilot`, `cline`, `kilo`, `roo`, `continue`,
`opencode`, `claude-code`, `cursor`, `trae`, `windsurf`, `claude-desktop`, `all`.

`--bundle` writes a support bundle (doctor output plus the log tail, with secrets and prompts
stripped) that you can attach to a ticket. `--json` is for scripting.

A `warn` is not a failure. `Ollama environment` warns until you set the three variables and restart
the Ollama app; `Git line endings` warns while `core.autocrlf=true`; `Daemon` warns until you start it.

## 3. Start and stop the daemon

Everything routes through one background daemon, so several editors share one router and one warm
model.

```
uv run headwaters start
uv run headwaters daemon-status
uv run headwaters status
uv run headwaters stop
```

`start` launches the daemon, waits for it to listen, and prints:

```
The daemon is running.
API and MCP: http://127.0.0.1:4002/mcp
```

The full port list is in `daemon-status` (or `start --json`), for example `api` 4002, `gateway` 4000
and `dashboard` 4001. If a port is taken, the daemon picks the next free one, so read the ports from
`daemon-status` rather than assuming them. `start --port <n>` asks for a preferred gateway port.

`daemon-status` reports pid, uptime, ports, scope, the Private lock, the catalog and the keep-warm
state. `status` is the human page: effective scope, locks, walls with reset times and the warm model.
`status --open` starts the daemon if needed and opens the status page in your browser.

`stop` asks the daemon to shut down; `stop --force` kills it if it will not.

## 4. Wire up a host

```
uv run headwaters install --host vscode
```

Run `--dry-run` first to see every path that would change before anything is written:

```
uv run headwaters install --host vscode --dry-run
```

Hosts this command knows: `vscode`, `cline`, `opencode`, `claude-desktop`, or `all` (the default) for
every host that is installed. `--project <path>` points at the project folder to configure instead of
the current directory; `--yes` skips the confirmation prompts.

What an install changes, using VS Code as the example (the other hosts write their own config and
rules files; `--dry-run` shows the exact paths on your machine):

- the project's `.vscode/mcp.json`, which registers the `headwaters` MCP server;
- the project's `.vscode/settings.json`, which enables bring-your-own model keys;
- your user `chatLanguageModels.json` under `%APPDATA%\Code\User`, which gains a `Headwaters`
  provider pointing at `http://127.0.0.1:4000/v1/chat/completions` with three models
  (`headwaters/auto`, `headwaters/free`, `headwaters/local`);
- the project's `.github/copilot-instructions.md`, between `<!-- headwaters:begin -->` and
  `<!-- headwaters:end -->` markers;
- the project's `.github/prompts/headwaters-*.prompt.md`, so `/headwaters-status` and friends work in
  Copilot Chat.

Two things to know:

- Every file is copied to `<name>.headwaters-backup` before it is written, and the list of what was
  written is kept in `<state>\install\manifest.json`. A re-install keeps the first backup, which is
  the true original.
- VS Code asks once for the gateway token. Paste the contents of `<state>\daemon.token` into the
  `Headwaters` prompt. The installer never writes the token into a config file.

Because the project files land in the folder you run the command from, **do not run `install` inside
this repository** unless you want its `.vscode` and `.github` files rewritten. Use a scratch project
folder for dogfooding.

Undo it with:

```
uv run headwaters uninstall --host vscode
```

`uninstall` restores every backup that install made, newest first. It leaves a file alone when you
have edited it since, and tells you which ones it skipped; `--force` overrides that. `--dry-run` shows
what would be restored.

Host-specific notes live in [hosts/index.md](hosts/index.md) and, for example,
[hosts/vscode.md](hosts/vscode.md).

## 5. Where state lives

| Path | What is in it |
|------|---------------|
| `%APPDATA%\Headwaters\rules.yaml` | your rules: scope, Private lock, ports, tiers, catalog url, telemetry |
| `%LOCALAPPDATA%\Headwaters\headwaters.db` | the shared SQLite database: known models, walls, keys index, handoffs |
| `%LOCALAPPDATA%\Headwaters\daemon.json` | pid and ports of the running daemon |
| `%LOCALAPPDATA%\Headwaters\daemon.token` | the per-user gateway token |
| `%LOCALAPPDATA%\Headwaters\daemon.lock` | the lock that keeps one daemon per user |
| `%LOCALAPPDATA%\Headwaters\install\manifest.json` | what each install wrote, and where its backup is |
| `%LOCALAPPDATA%\Headwaters\Cache` | the downloaded catalog |
| `%LOCALAPPDATA%\Headwaters\Logs` | `headwaters.log` and `mcp-shim.log` |

Secrets are not in any of these files: API keys go to the Windows Credential Manager through
`keyring`, under the service name `headwaters`.

Setting `HEADWATERS_HOME` to a folder redirects all four directories under it
(`config`, `state`, `cache`, `logs`), which is the quickest way to try something without touching your
real state.

## 6. Keys

```
uv run headwaters keys set openrouter
uv run headwaters keys list
uv run headwaters keys test
```

`keys set <alias>` prompts with a hidden input (the alias defaults to the provider name), and
`--provider/-p` names the provider explicitly, `--env` reads the key from an environment variable,
`--stdin` reads it from standard input. `keys list` shows each alias, its provider, where the value
comes from and a masked key. `keys test [alias]` asks the provider whether it accepts the key; it
sends no prompt content. `keys remove <alias>` deletes it.

Environment variables work too, as a fallback: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, plus any `api_key_env` you configure for your own OpenAI-compatible endpoint.

## 7. Update after a merge

```
git pull
uv sync
uv run headwaters stop
uv run headwaters start
uv run headwaters doctor
```

Restarting the daemon is what makes a new build take effect: hosts talk to the daemon, and the daemon
is the process that loaded the old code. If the port list changed, re-run
`uv run headwaters install --host <id> --dry-run` to see whether the host configs still point at the
right gateway port.

## 8. Reset the state when it gets wedged

```
uv run headwaters stop --force
uv run headwaters doctor
```

Only if the doctor still shows a broken port, a stale `daemon.json` or a corrupt database, reset. Do
the `uninstall` **first**, while the manifest still exists:

```
uv run headwaters uninstall --host vscode
```

Then stop the daemon and delete the state directory:

```
uv run headwaters stop --force
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Headwaters"
```

What that loses: the model list and its scores, the recorded walls and their reset times, handoffs,
the install manifest (so an `uninstall` can no longer restore host backups), the gateway token (a new
one is written on the next start, so hosts that cached the old token must be told the new one) and the
logs. It does not touch host configs, which are restored by `uninstall`, or your keys, which are in
the Credential Manager.

To also start from default rules, delete `%APPDATA%\Headwaters\rules.yaml` and run
`uv run headwaters doctor` again; it is recreated as a comment-only reference, so every default
applies until you choose a setting.

Do not delete `%APPDATA%\Headwaters\rules.yaml` casually: it is the file that holds your scope, the
Private lock and the ports.

## 9. Run the gateway and the MCP server by hand

There is no separate `headwaters gateway` or `headwaters mcp serve` command, and this page does not
pretend there is. The daemon is the server: it binds the OpenAI and Anthropic faces on the gateway
port, and the MCP endpoint on `<api>/mcp`. The per-host `headwaters-mcp` process is a stdio shim that
forwards to that same daemon; a host starts it, you do not drive it by hand.

So the manual request test is: start the daemon, read the token, and talk to the gateway.

```
uv run headwaters start
Get-Content "$env:LOCALAPPDATA\Headwaters\daemon.token"
```

Then, in a second shell, with the token in a variable:

```
$token = (Get-Content "$env:LOCALAPPDATA\Headwaters\daemon.token" -Raw).Trim()
curl.exe -s -o - -w "`nHTTP %{http_code}`n" http://127.0.0.1:4000/health
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -H "Authorization: Bearer $token" http://127.0.0.1:4000/v1/models
```

Expected: `/health` answers `{"ok":true,"gateway":"openai"}` with HTTP 200 and needs no token;
`/v1/models` answers an OpenAI model list with the four `headwaters/*` ids with the token, and HTTP
401 `{"ok":false,"error":{"code":"unauthorized"}}` without it.

For a chat request, put the body in a file so PowerShell cannot mangle the quotes:

```
Set-Content -Path body.json -NoNewline -Value '{"model":"headwaters/auto","messages":[{"role":"user","content":"say hi"}]}'
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST -H "Authorization: Bearer $token" -H "Content-Type: application/json" --data "@body.json" http://127.0.0.1:4000/v1/chat/completions
```

The Anthropic shape answers on `POST /v1/messages` with the `x-api-key` and `anthropic-version`
headers:

```
Set-Content -Path body.json -NoNewline -Value '{"model":"headwaters/auto","max_tokens":16,"messages":[{"role":"user","content":"say hi"}]}'
curl.exe -s -o - -w "`nHTTP %{http_code}`n" -X POST -H "x-api-key: $token" -H "anthropic-version: 2023-06-01" -H "Content-Type: application/json" --data "@body.json" http://127.0.0.1:4000/v1/messages
```

With no model able to serve the request yet (a fresh database, before `headwaters models refresh`),
both faces answer HTTP 400 with a `no_candidates` error, in their own shape: OpenAI
`{"error":{"message":"No model can serve this request in the current scope.","type":"invalid_request_error","code":"no_candidates","param":null}}`
and Anthropic `{"type":"error","error":{"type":"invalid_request_error","message":"No model can serve this request in the current scope."}}`.
That is the router refusing politely, not a crash.

Two more gateway guards are worth seeing once, because they are what keeps the port private:
`curl.exe` with a non-loopback `Host` header gets HTTP 421 `bad_host`, and a request from a
non-loopback `Origin` gets HTTP 403 `bad_origin`.

For MCP, the supported manual check is the doctor's live probe:

```
uv run headwaters doctor --host vscode
```

with the three extra rows described in section 2. `uv run headwaters mcp-path` prints the exact
command and arguments a host would use, which is what to paste into a host by hand if you are wiring
one up manually.

## 10. What to do when something breaks

Record it in [FUNCTIONAL_TESTS.md](FUNCTIONAL_TESTS.md)'s bug-report template, attach
`uv run headwaters doctor --bundle`, and keep the log excerpt short. Never paste an API key, a
`daemon.token` or a prompt you would not share. During Stage 1 these reports become tickets or
follow-ups, and they may reorder the remaining backlog; they do not start release work.
