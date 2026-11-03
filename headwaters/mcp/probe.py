"""``python -m headwaters.mcp.probe``: the live checks for ``headwaters doctor --host`` (HDW-402).

It runs in a child process so the doctor itself imports neither the MCP SDK nor the web framework
(D14). The probe opens a real MCP client to the daemon (starting it when it is not running) as the
named host's client id, then reports three things:

* ``round_trip`` - the daemon answers ``tools/list`` with the front-door tools;
* ``tool_call`` - ``headwaters_status`` answers without an error;
* ``scope`` - ``headwaters_scope`` with no arguments reads the effective scope and changes nothing.

One JSON object is printed on stdout: ``{"ok": bool, "checks": [{name, ok, detail}]}``. Nothing
from a prompt or a model appears in it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

__all__ = ["main", "run"]

FRONT_DOOR = "headwaters_do"


def _payload(result: Any) -> dict[str, Any]:
    """The structured part of a tool result, whichever way the SDK exposes it."""
    data = getattr(result, "structuredContent", None)
    if isinstance(data, dict):
        return data
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


async def run(host: str) -> dict[str, Any]:
    """The three live checks, as JSON-ready rows. Never raises: a failure is a failed row."""
    from headwaters.mcp.link import DaemonUnavailable, ensure_daemon, open_daemon_client
    from headwaters.mcp.meta import META_CLIENT

    meta: Any = {META_CLIENT: f"doctor:{host}"}
    checks: list[dict[str, Any]] = []
    try:
        daemon = await ensure_daemon()
        async with open_daemon_client(daemon) as client:
            listing = await client.list_tools()
            names = [tool.name for tool in getattr(listing, "tools", None) or []]
            checks.append(
                {
                    "name": "round_trip",
                    "ok": FRONT_DOOR in names,
                    "detail": (
                        f"{len(names)} tools listed"
                        if FRONT_DOOR in names
                        else f"{FRONT_DOOR} is missing from {len(names)} tools"
                    ),
                }
            )
            status = await client.call_tool("headwaters_status", {}, meta=meta)
            body = _payload(status)
            ok = not getattr(status, "is_error", False) and bool(body)
            checks.append(
                {
                    "name": "tool_call",
                    "ok": ok,
                    "detail": (
                        f"headwaters_status answered ({body.get('version') or 'no version field'})"
                        if ok
                        else "headwaters_status returned an error"
                    ),
                }
            )
            scope = await client.call_tool("headwaters_scope", {}, meta=meta)
            scope_body = _payload(scope)
            raw_scope = scope_body.get("scope")
            info: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
            effective = info.get("effective")
            ok = not getattr(scope, "is_error", False) and isinstance(effective, str)
            checks.append(
                {
                    "name": "scope",
                    "ok": ok,
                    "detail": (
                        f"effective scope {effective}"
                        if ok
                        else "headwaters_scope did not report an effective scope"
                    ),
                }
            )
    except DaemonUnavailable as exc:
        return {"ok": False, "error": f"the daemon is not available: {exc}"}
    except Exception as exc:  # one clear row per check, never a traceback
        return {"ok": False, "error": f"the probe failed ({type(exc).__name__})"}
    return {"ok": all(row["ok"] for row in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="headwaters.mcp.probe")
    parser.add_argument("--host", required=True, help="The host id to test as.")
    args = parser.parse_args(argv)
    result = asyncio.run(run(args.host))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - the doctor runs it as a module
    sys.exit(main())
