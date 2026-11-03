# Headwaters

Windows-first, local-first LLM router that keeps developers coding when paid credits or rate limits run out.

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
