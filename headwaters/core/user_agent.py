"""The one User-Agent every outbound provider request carries (HDW-515 amendment).

Cloudflare refuses generic SDK and HTTP-library user agents with HTTP 403 "error code: 1010" on
every OpenCode host, so a client that sends ``Python-urllib/3.x``, ``python-httpx/...`` or
``AsyncOpenAI/Python ...`` never reaches the provider at all (docs/SDK_FACTS.md, 2026-11-03).
OpenCode's own docs ask clients to "identify itself with its own user agent, such as
my-coding-agent/1.0". Both the discovery/listing client and the executor's cloud calls name
Headwaters with this one string, so there is a single thing to keep honest.
"""

from __future__ import annotations

from headwaters import __version__

USER_AGENT = f"headwaters/{__version__}"

__all__ = ["USER_AGENT"]
