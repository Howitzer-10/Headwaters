"""One model call for ``headwaters_do`` and ``headwaters_ask`` (HDW-204).

Pipeline: workspace, project rule, packed files, a ``RouteRequest`` (surface ``mcp``), the router,
then ``outcome.build_outcome``. Local scope stays fail-closed because everything goes through
``Router``: a refusal is returned as an error, never retried in the cloud. Prompt text and file
contents are never logged or stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from headwaters.core.engine import Engine
from headwaters.core.executor.types import Message
from headwaters.core.router_types import ProgressCallback, RouteRequest
from headwaters.core.rules import Scope
from headwaters.core.state_misc import kv_get
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools import prompts
from headwaters.mcp.tools.attach import load_images, pack_paths
from headwaters.mcp.tools.context import ToolContext, ToolFailure
from headwaters.mcp.tools.outcome import Outcome, build_outcome
from headwaters.mcp.tools.schemas import FileNote
from headwaters.mcp.tools.status import PIN_KEY
from headwaters.mcp.tools.workspace import Workspace, note_for, open_workspace

__all__ = [
    "Prepared",
    "current_engine",
    "current_pin",
    "prepare_request",
    "run_model_tool",
    "run_prepared",
]


def current_pin(tc: ToolContext) -> str | None:
    """The model the user pinned through ``headwaters_pin_model``, if any."""
    if tc.db is None:
        return None
    value = kv_get(tc.db, PIN_KEY)
    return value if isinstance(value, str) and value else None


def current_engine(tc: ToolContext) -> Engine:
    engine = tc.engine()
    if engine is None:
        raise ToolFailure(
            "starting_up",
            "Headwaters is still loading its model list.",
            "Try again in a few seconds.",
        )
    return engine


def _user_message(task: str, packed: str, images: list[str]) -> Message:
    body = f"{task}\n\n{prompts.FILES_HEADER}\n{packed}" if packed else task
    if not images:
        return {"role": "user", "content": body}
    parts: list[dict[str, Any]] = [{"type": "text", "text": body}]
    parts.extend({"type": "image_url", "image_url": {"url": url}} for url in images)
    return {"role": "user", "content": parts}


@dataclass(frozen=True)
class Prepared:
    """A request ready to route: built once, so the estimate and the run use the same one."""

    tool: str
    want: str
    request: RouteRequest
    engine: Engine
    workspace: Workspace
    files: tuple[FileNote, ...]
    notes: tuple[str, ...]


async def prepare_request(
    tc: ToolContext,
    tool: str,
    *,
    task: str,
    want: str,
    paths: list[str],
    images: list[str],
    scope: Scope | None,
    meta: Mapping[str, Any] | None,
    verify_always: bool,
    task_hint: str | None = None,
    reasoning: bool | str | None = None,
) -> Prepared:
    """Workspace, project rule, packed files and images, then the ``RouteRequest``.

    Raises ``ToolFailure`` or a routing error; the caller maps it. ``task_hint`` and
    ``reasoning`` are the HDW-140 hooks ``headwaters_think`` uses. The user's pin
    (``headwaters_pin_model``) rides every call; the hard filter still applies to it.
    """
    engine = current_engine(tc)
    workspace = open_workspace(meta, tc.rules(), tc.configured_roots())
    notes: list[str] = []
    files: list[FileNote] = []
    packed = ""
    if paths:
        pack = await run_blocking(lambda: pack_paths(tc, engine.catalog, workspace, paths))
        files.extend(note_for(e) for e in pack.entries)
        packed = pack.render()
        if not pack.files:
            raise ToolFailure(
                "no_readable_files",
                "None of the named paths could be read.",
                "Check each path is inside the project folder and is a text file.",
                files=files,
            )
        if any(e.status in ("truncated", "budget") for e in pack.entries):
            notes.append("Some files were cut to fit the token budget; see files[].")
    data_urls: list[str] = []
    if images:
        data_urls, image_notes = await run_blocking(
            lambda: load_images(workspace, engine.catalog, images, tc.settings)
        )
        files.extend(image_notes)
        if not data_urls:
            raise ToolFailure(
                "no_readable_images",
                "None of the named images could be read.",
                "Check each image is a PNG, JPEG, GIF or WebP inside the project folder.",
                files=files,
            )
    request = RouteRequest(
        messages=[_user_message(task, packed, data_urls)],
        system=prompts.system_for(tool, want),
        surface="mcp",
        file_paths=tuple(paths),
        scope=scope,
        verify="always" if verify_always else None,
        project=workspace.project,
        pin=current_pin(tc),
        task_hint=task_hint,
        reasoning=reasoning,
    )
    return Prepared(tool, want, request, engine, workspace, tuple(files), tuple(notes))


async def run_prepared(
    tc: ToolContext, prepared: Prepared, on_progress: ProgressCallback | None = None
) -> Outcome:
    """Route a prepared request, reporting each step to ``on_progress`` when given."""
    request = replace(prepared.request, on_progress=on_progress)
    result = await prepared.engine.router.run(request)
    return await build_outcome(
        tc,
        prepared.engine,
        prepared.tool,
        result,
        prepared.workspace,
        list(prepared.files),
        list(prepared.notes),
        prepared.want,
    )


async def run_model_tool(
    tc: ToolContext,
    tool: str,
    *,
    task: str,
    want: str,
    paths: list[str],
    images: list[str],
    scope: Scope | None,
    meta: Mapping[str, Any] | None,
    verify_always: bool,
    task_hint: str | None = None,
    reasoning: bool | str | None = None,
    on_progress: ProgressCallback | None = None,
) -> Outcome:
    """Prepare and run one request (what ``headwaters_think`` and the tests call)."""
    prepared = await prepare_request(
        tc,
        tool,
        task=task,
        want=want,
        paths=paths,
        images=images,
        scope=scope,
        meta=meta,
        verify_always=verify_always,
        task_hint=task_hint,
        reasoning=reasoning,
    )
    return await run_prepared(tc, prepared, on_progress)
