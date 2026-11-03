"""The hosts this installer knows (HDW-403).

The v3.4 list has thirteen hosts; this ticket ships the first four. ``--host all`` means every
adapter registered here, so a host that is not implemented is reported as not implemented rather
than silently skipped.
"""

from __future__ import annotations

from headwaters.installer.base import HostAdapter
from headwaters.installer.hosts.claude_desktop import ClaudeDesktop
from headwaters.installer.hosts.cline import Cline
from headwaters.installer.hosts.opencode import OpenCode
from headwaters.installer.hosts.vscode import VsCode

__all__ = ["ADAPTERS", "all_adapters", "get_host", "host_ids"]

ADAPTERS: tuple[HostAdapter, ...] = (VsCode(), Cline(), OpenCode(), ClaudeDesktop())


def all_adapters() -> tuple[HostAdapter, ...]:
    return ADAPTERS


def host_ids() -> tuple[str, ...]:
    return tuple(adapter.id for adapter in ADAPTERS)


def get_host(host_id: str) -> HostAdapter | None:
    for adapter in ADAPTERS:
        if adapter.id == host_id:
            return adapter
    return None
