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

## Windows and repo tooling

| Fact | Verified by |
| --- | --- |
| `Set-Content -Encoding utf8` adds a BOM; write files with the editor tool or `[System.IO.File]` | hit it in HDW-146 |
| The editor tool will not overwrite an existing file: create the first chunk, then use `insert_line` | hit it in HDW-146 |
| To see a crash's stderr, run `cmd /c "uv run python x.py > out 2>&1"` and read `out` | hit it in HDW-146 |
| Regenerate the rules schema with `uv run python -m headwaters.core.rules_io --write` | ran it in HDW-146 |
| Tests that count tools (`test_more_disclosure.py`, `test_host_eval.py`) need updating when an advanced tool is added | hit it in HDW-146 |
| A foreground command over 30 s times out; start long pytest runs with `Start-Process` and poll the log | hit it in HDW-146 |
