# Headwaters

Windows-first, local-first LLM router that keeps developers coding when paid credits or rate limits run out.

**Stage 1, owner evaluation: nothing is published yet.** There is no installer download, no store
build, no signed binary and no beta of any kind. The owner is running the build from source and
testing it; distribution and sale are a separate, deferred stage.
[docs/PLAN.md](docs/PLAN.md) explains both stages, [docs/BETA.md](docs/BETA.md) and
[docs/RELEASE_EVIDENCE.md](docs/RELEASE_EVIDENCE.md) record what a release will eventually require,
and `headwaters release-gate` lists the runs that are still missing and refuses to pass until they are
recorded. Install from source until then: [docs/DOGFOOD.md](docs/DOGFOOD.md) walks through it.

## Install

```
uv sync
uv run headwaters start
```

`headwaters start` runs the daemon: the OpenAI- and Anthropic-compatible gateway on
`127.0.0.1:4000`, and the read-only status page on `127.0.0.1:4001`. `headwaters stop` stops it,
`headwaters stop --force` clears a stale one, `headwaters status` prints what it is doing,
`headwaters status --open` opens the dashboard, and `headwaters status --json` is the same data for a
script. The packaged payloads are built by `scripts/build_windows.py` (Inno Setup, per-user) and
`scripts/build_linux.py` (payload script plus a systemd user unit).

## Point a host at Headwaters

```
uv run headwaters install --host all --dry-run   # the diff, no writes
uv run headwaters install --host vscode          # per file: yes/no, backup, read back
```

Host ids are `vscode` (GitHub Copilot BYOK), `cline`, `opencode` and `claude-desktop`, or `all`;
`headwaters uninstall --host <id>` reverses the same plan. The pages below are generated from the
installer itself, so they list the files and BYOK fields the current build really writes:

| Host | Page |
| --- | --- |
| VS Code Copilot BYOK | [docs/hosts/vscode.md](docs/hosts/vscode.md) |
| Cline | [docs/hosts/cline.md](docs/hosts/cline.md) |
| OpenCode | [docs/hosts/opencode.md](docs/hosts/opencode.md) |
| Claude Desktop | [docs/hosts/claude-desktop.md](docs/hosts/claude-desktop.md) |

## What is free

The gateway, automatic failover, the free and local scopes with Private mode, the hardware guard,
basic verification, the installer, the doctor and the catalog updates are core: free in every
build, at beta and after. Features marked ★ are free during beta and may join a paid tier at 1.0.

★ = free during beta; may become part of Headwaters Pro at 1.0

## Development

```
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest --cov
uv run headwaters --help
```

Pytest markers: `live` (real Ollama; deselected by default, run with `-m live`), `windows`, `slow`.

## Pointing a tool at the gateway

The daemon serves two faces on the same loopback port: the OpenAI-compatible one
(`/v1/models`, `/v1/chat/completions`) and the Anthropic Messages one (`POST /v1/messages`). Both
take the daemon's token, as `Authorization: Bearer <token>` or, for the Anthropic face, as
`x-api-key: <token>`. Route ids the picker serves start with `headwaters/` (`headwaters/auto`,
`headwaters/free`, `headwaters/local`, `headwaters/cloud`, with `+quality`, `+verify` and
`+noverify` suffixes). A name the alias table maps — `models.aliases` in `rules.yaml`, e.g.
`gpt-4o: headwaters/auto` — is resolved to what it stands for. Any other model name is pinned
as-is: it answers from that model and fails over only when the call fails, unless the request
also sends `x-headwaters-pin: true`, which asks for that model or an error.

Claude Code, with API keys only — a Claude subscription must not be routed through the gateway:

```
set ANTHROPIC_BASE_URL=http://127.0.0.1:4000
set ANTHROPIC_API_KEY=<daemon token>
set ANTHROPIC_MODEL=headwaters/auto
claude
```

`ANTHROPIC_MODEL` matters: Claude Code would otherwise ask for a Claude model name, which the
gateway pins instead of routing — map that name in `models.aliases` to have it routed. Token
counting (`POST /v1/messages/count_tokens`) is not served yet.

## Privacy and the daemon token

`headwaters doctor` checks the machine it runs on and prints one row per area (Ollama, GPU, ports,
the daemon, the token, saved keys, the catalog, git, the MCP launcher). `--host <host>` adds the
three rows a host of that kind actually sees, and `--bundle` writes a redacted zip to the state
directory for support: no prompt, no key and no path outside the Headwaters app directories.

Private Mode (airplane mode, the Private lock, or a local default scope) stops Headwaters from
calling the cloud, but Ollama can still run a cloud model on its own. Set `OLLAMA_NO_CLOUD=1`, or
`"disable_ollama_cloud": true` in `~/.ollama/server.json`, and restart Ollama to close that.

On Windows `daemon.token` relies on the ACL of your per-user profile folder: the file has no
explicit ACL of its own, so another process of the same user can read it. Treat the token like a
password, and do not share or copy the profile folder.

## Docs

- [docs/PLAN.md](docs/PLAN.md): the two stages, Stage 1 dogfooding now and Stage 2 distribution later.
- [docs/DOGFOOD.md](docs/DOGFOOD.md): run the current build from source on your own machine.
- [docs/FUNCTIONAL_TESTS.md](docs/FUNCTIONAL_TESTS.md): the Stage 1 checks to run by hand, and the bug-report template.
- [docs/BETA.md](docs/BETA.md): what beta will promise at Stage 2, the free list and the closed-alpha exit criteria.
- [docs/hosts/index.md](docs/hosts/index.md): every host, generated from the installer.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md): the six common failures and the fix for each.
- [docs/PRIVACY.md](docs/PRIVACY.md): what is stored, what is logged and what leaves the machine.
- [docs/RELEASE_EVIDENCE.md](docs/RELEASE_EVIDENCE.md): the gate, the evidence file and the exit codes, all Stage 2.
- [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md) and [docs/RELEASE_CHECKLIST_LINUX.md](docs/RELEASE_CHECKLIST_LINUX.md): the runs a person has to do at Stage 2.
- [docs/ITCH_STORE.md](docs/ITCH_STORE.md): the store copy, kept in the repo so it is reviewed.
- [docs/DECISIONS.md](docs/DECISIONS.md): binding decisions, D1 onwards.
