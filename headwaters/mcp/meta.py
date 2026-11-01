"""Request ``_meta`` keys the shim sends to the daemon (HDW-204, D9).

Kept apart from the tools so the shim imports nothing heavy for two strings.
"""

from __future__ import annotations

__all__ = ["META_CWD", "META_ROOTS"]

META_CWD = "headwaters/cwd"  # the shim's working directory: the project folder a host opened
META_ROOTS = "headwaters/roots"  # host roots (paths or file:// URIs); they may only narrow
