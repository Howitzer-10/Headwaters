"""Shared rig for the front-door tool tests (HDW-204).

The tools run against the real ``build_engine`` wiring with a scripted local backend, a real
SQLite state and a real temp git repository, so a diff is checked with a real ``git apply``.
Nothing leaves the machine: no cloud factory is ever called unless a test asks for one.
"""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import Client, types
from mcp.server import MCPServer

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.engine import Engine, build_engine
from headwaters.core.executor.types import BackendRequest, Delta
from headwaters.core.rules import Rules
from headwaters.core.state import Database, open_database
from headwaters.mcp.endpoint import build_endpoint
from headwaters.mcp.meta import META_CWD
from headwaters.mcp.tools.context import ToolContext, ToolSettings
from tests.cli_support import add_model, gpu_state

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_TASK = "write a function that adds two numbers"

DIFF_ANSWER = (
    "Added the flag.\n\n```diff\n"
    "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def run():\n-    return 1\n+    return 2\n"
    "```\n"
)


class Scripted:
    """A local backend that plays one reply and records what it was asked."""

    def __init__(self, reply: str = "Plain answer.") -> None:
        self.reply = reply
        self.requests: list[BackendRequest] = []

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        yield Delta(content=self.reply)
        yield Delta(finish_reason="stop")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def make_repo(root: Path, files: dict[str, str]) -> Path:
    """A committed temp repo with ``files`` (LF line endings, no autocrlf)."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "core.autocrlf", "false")
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def seed(db: Database, *, local_score: float = 90.0, cloud: bool = False) -> None:
    add_model(
        db, "qwen:14b", "local", local_score, agentic=local_score, warm=True, size_vram_gb=8.0
    )
    if cloud:
        add_model(db, "vendor/free:free", "free_cloud", 98.0, agentic=98.0, ctx=131072)


class Rig:
    """The tools wired to a real engine. ``cloud_calls`` counts cloud clients ever constructed."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        reply: str = "Plain answer.",
        rules: Rules | None = None,
        local_score: float = 90.0,
        cloud: bool = False,
        settings: ToolSettings | None = None,
        configured: Sequence[str] = (),
    ) -> None:
        self.rules = rules or Rules()
        self.db = open_database(tmp_path / "state.db")
        seed(self.db, local_score=local_score, cloud=cloud)
        self.backend = Scripted(reply)
        self.cloud_calls = 0
        self.edits: list[tuple[str, bool | str]] = []

        def cloud_factory() -> Scripted:
            self.cloud_calls += 1
            return self.backend

        self.engine: Engine = build_engine(
            lambda: self.rules,
            load_catalog(REPO_ROOT / SEED_RELATIVE_PATH),
            "seed",
            self.db,
            hardware=lambda: gpu_state(16),
            local_factory=lambda host: self.backend,
            cloud_factory=cloud_factory,  # type: ignore[arg-type]
            enable_classifier=False,
        )
        self.context = ToolContext(
            engine=lambda: self.engine,
            rules=lambda: self.rules,
            settings=settings or ToolSettings(),
            configured_roots=lambda: list(configured),
            db=self.db,
            edit_rules=self._edit_rules,
        )
        self.server = MCPServer(name="t", version="0")
        build_endpoint("0", server=self.server, tools=self.context)

    def _edit_rules(self, key: str, value: bool | str) -> tuple[Any, Any]:
        """The rig's stand-in for ``rules_edit.set_setting``: edits the in-memory rules."""
        previous = getattr(self.rules, key)
        self.rules = self.rules.model_copy(update={key: value})
        self.edits.append((key, value))
        return previous, value

    @asynccontextmanager
    async def client(self, **kwargs: Any) -> AsyncIterator[Client]:
        async with Client(self.server, **kwargs) as client:
            yield client

    async def call(
        self,
        name: str,
        args: Mapping[str, Any],
        cwd: Path | None = None,
        *,
        elicit: Any = None,
    ) -> types.CallToolResult:
        """One call on a fresh client. ``elicit`` is an elicitation callback (None: no support)."""
        meta: Any = {META_CWD: str(cwd)} if cwd is not None else None
        async with self.client(elicitation_callback=elicit) as client:
            return await client.call_tool(name, dict(args), meta=meta)


def structured(result: types.CallToolResult) -> dict[str, Any]:
    assert result.structured_content is not None, result
    return dict(result.structured_content)


def text_of(result: types.CallToolResult) -> str:
    return "".join(c.text for c in result.content if isinstance(c, types.TextContent))
