"""Run a host adapter: plan, apply, verify, undo (HDW-403).

``apply`` is only called after the user has seen the diff and said yes. It backs up each file,
writes it, then re-plans: a clean re-plan (nothing left to change) plus an empty
``adapter.problems()`` is what "verified" means here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from headwaters.installer import state as state_store
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context
from headwaters.installer.plan import FileChange, render

__all__ = ["HostResult", "apply", "plan", "uninstall"]


@dataclass
class HostResult:
    """What one host needs, or what was done to it."""

    host: str
    title: str
    changes: list[FileChange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    applied: bool = False
    written: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    verified: bool = False
    problems: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[FileChange]:
        return [item for item in self.changes if item.changed]

    @property
    def ok(self) -> bool:
        return not self.errors

    def diff(self) -> str:
        return render(self.changes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "title": self.title,
            "ok": self.ok,
            "applied": self.applied,
            "verified": self.verified,
            "changes": [
                {"path": str(item.path), "kind": item.kind, "note": item.note}
                for item in self.changes
            ],
            "written": list(self.written),
            "restored": list(self.restored),
            "notes": list(self.notes),
            "problems": list(self.problems),
            "errors": list(self.errors),
        }


def plan(adapter: HostAdapter, ctx: Context) -> HostResult:
    """Read only: what an install would change, and what the user must still do."""
    return HostResult(
        host=adapter.id,
        title=adapter.title,
        changes=adapter.plan(ctx),
        notes=adapter.notes(ctx),
    )


def apply(adapter: HostAdapter, ctx: Context) -> HostResult:
    """Back up, write and verify every changed file."""
    result = plan(adapter, ctx)
    for item in result.pending:
        created = item.before is None
        backup = state_store.backup_file(item.path)
        state_store.write_file(item.path, item.after)
        state_store.remember(
            ctx.state, adapter.id, item.path, backup=backup, after=item.after, created=created
        )
        result.written.append(str(item.path))
    result.applied = True
    leftover = [item for item in adapter.plan(ctx) if item.changed]
    if leftover:
        result.errors.append(
            "the write did not take: " + ", ".join(str(item.path) for item in leftover)
        )
    result.problems = adapter.problems(ctx)
    result.verified = not leftover and not result.problems
    return result


def uninstall(adapter: HostAdapter, ctx: Context, *, force: bool = False) -> HostResult:
    """Restore every backup this host's install made, newest first, leaving edited files alone."""
    rows = state_store.load_manifest(ctx.state).get(adapter.id, [])
    result = HostResult(host=adapter.id, title=adapter.title, applied=True)
    if not rows:
        result.errors.append(
            f"no install recorded for {adapter.id} here; run `headwaters install --host "
            f"{adapter.id}` first, or edit the config by hand"
        )
        return result
    remaining: list[dict[str, Any]] = []
    for row in reversed(rows):
        problem = state_store.restore(row, force=force)
        if problem is None:
            result.restored.append(str(row.get("path", "")))
        else:
            result.errors.append(problem)
            remaining.append(row)
    data = state_store.load_manifest(ctx.state)
    if remaining:
        data[adapter.id] = list(reversed(remaining))
    else:
        data.pop(adapter.id, None)
    state_store.save_manifest(ctx.state, data)
    result.verified = not remaining
    return result
