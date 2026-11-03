"""What a host adapter must answer (HDW-403).

An adapter detects its host, computes the files a full install would leave behind, explains what
the user still has to do by hand, and reads its own config back so the doctor can check it
(FU-402-2). It never writes: ``headwaters.installer.engine`` does that, once the user agrees.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from headwaters.installer.context import Context
from headwaters.installer.plan import FileChange

__all__ = ["HostAdapter"]


class HostAdapter(ABC):
    """One host (its config files, its command folder and its rules location)."""

    id: str = ""
    title: str = ""
    #: where the host reads its config: "project" for files in the repo, "user" for the profile
    scope: str = "project"
    #: some hosts take no command files at all
    has_commands: bool = True

    def detect(self, ctx: Context) -> bool:
        """Whether this host looks present on the machine or in the project."""
        return True

    def config_paths(self, ctx: Context) -> list[Path]:
        """The files this host owns, without reading them (cheap enough for the doctor)."""
        return [item.path for item in self.plan(ctx)]

    def installed(self, ctx: Context) -> bool:
        return any(path.is_file() for path in self.config_paths(ctx))

    @abstractmethod
    def plan(self, ctx: Context) -> list[FileChange]:
        """The files a full install would leave behind, with their before and after text."""

    def notes(self, ctx: Context) -> list[str]:
        """What the user still has to do: a token, a setting, a restart."""
        return []

    def problems(self, ctx: Context) -> list[str]:
        """What is missing or stale in the host's own config. Empty means it is healthy."""
        return []
