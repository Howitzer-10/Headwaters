"""Read the host configs back and report what is wrong with them (FU-402-2).

The doctor used to check only that the MCP command resolves. A host config can be perfectly
resolvable and still wrong: the entry missing, the path stale after an upgrade, the BYOK setting
off. This walks every installed host's own file and says so; it changes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from headwaters.installer.context import Context
from headwaters.installer.hosts import all_adapters

__all__ = ["HostProblem", "problems"]


@dataclass(frozen=True)
class HostProblem:
    host: str
    title: str
    detail: str


def problems(ctx: Context) -> list[HostProblem]:
    """One row per host config that is installed but not healthy."""
    found: list[HostProblem] = []
    for adapter in all_adapters():
        try:
            if not adapter.installed(ctx):
                continue
            for detail in adapter.problems(ctx):
                found.append(HostProblem(adapter.id, adapter.title, detail))
        except Exception as exc:  # a config we cannot even read is a finding, not a crash
            found.append(HostProblem(adapter.id, adapter.title, f"unreadable config: {exc}"))
    return found
