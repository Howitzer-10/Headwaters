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
