"""Where an install writes, resolved once per run (HDW-403).

Everything is explicit - project folder, home, state dir, launcher - so a test can point the
installer at a temporary tree and never touch a real host config or the user's profile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from headwaters.core import paths
from headwaters.core.mcp_launch import McpLaunch, primary, require_existing
from headwaters.core.rules import GatewayRules

__all__ = ["Context", "default_context", "gateway_port"]

DEFAULT_PORT = GatewayRules().port


def gateway_port() -> int:
    """The gateway port from ``rules.yaml``, or the shipped default when rules cannot be read."""
    try:
        from headwaters.core import rules_io

        return int(rules_io.load_rules().gateway.port)
    except Exception:  # a broken rules file is the doctor's problem, not the installer's
        return DEFAULT_PORT


def _home() -> Path:
    for name in ("HOME", "USERPROFILE"):
        raw = os.environ.get(name, "").strip()
        if raw:
            return Path(raw).expanduser()
    return Path.home()


@dataclass(frozen=True)
class Context:
    """One install run's fixed paths and launch command."""

    project: Path
    home: Path
    state: Path
    launch: McpLaunch
    port: int
    #: HDW-407: set only by the docs renderer, so a generated page never reads a real profile
    appdata_override: Path | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    @property
    def appdata(self) -> Path:
        """The per-user application-data root hosts keep their config in."""
        if self.appdata_override is not None:
            return self.appdata_override
        if os.name == "nt":
            raw = os.environ.get("APPDATA", "").strip()
            return Path(raw) if raw else self.home / "AppData" / "Roaming"
        raw = os.environ.get("XDG_CONFIG_HOME", "").strip()
        return Path(raw) if raw else self.home / ".config"


def default_context(
    project: Path | None = None,
    *,
    launch: McpLaunch | None = None,
    require_launcher: bool = True,
) -> Context:
    """The real run. ``require_launcher`` is off only for a read-only check such as the doctor's."""
    chosen = launch
    if chosen is None:
        chosen = require_existing() if require_launcher else primary()
    return Context(
        project=(project or Path.cwd()).resolve(),
        home=_home().resolve(),
        state=paths.state_dir(),
        launch=chosen,
        port=gateway_port(),
    )
