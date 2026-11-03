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
