"""``headwaters pick``: show which model would answer a prompt, why, and the fallbacks.

A dry run of the router (``Router.plan``): the prompt is analyzed on this machine, scope locks and
secret detection are applied, and the policy picks a model. No model is called, no cloud client
is created, nothing is written, and the prompt is never stored or printed back.
"""

import asyncio
from typing import Annotated

import typer

from headwaters.cli import runtime
from headwaters.cli.output import EXIT_NO_ROUTE, EXIT_SCOPE, emit_json, fail
from headwaters.cli.pick_report import plan_payload, print_plan
from headwaters.core.executor.types import Message, ToolSpec
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Scope
from headwaters.core.scope import ScopeError
from headwaters.core.state_models import list_models

_IMAGE_PART = {"type": "image_url", "image_url": {"url": "data:,"}}
_PROBE_TOOL: ToolSpec = {
    "type": "function",
    "function": {
        "name": "headwaters_probe",
        "description": "Stands in for the tools your host would send.",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _messages(prompt: str, image: bool) -> list[Message]:
    if not image:
        return [{"role": "user", "content": prompt}]
    return [{"role": "user", "content": [{"type": "text", "text": prompt}, _IMAGE_PART]}]


def register(app: typer.Typer) -> None:
    @app.command("pick")
    def pick(
        prompt: Annotated[str, typer.Argument(help="The prompt to route (analyzed locally).")],
        image: Annotated[
            bool, typer.Option("--image", help="Pretend an image is attached.")
        ] = False,
        tools: Annotated[
            bool, typer.Option("--tools", help="Pretend the host sends tools.")
        ] = False,
        tokens: Annotated[
            int | None,
            typer.Option("--tokens", min=1, help="Pretend the request has this many tokens."),
        ] = None,
        scope: Annotated[
            Scope | None, typer.Option("--scope", help="Scope to ask for (default: your rules).")
        ] = None,
        as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    ) -> None:
        """Show which model would answer a prompt, why, and what it falls back to.

        A dry run: no model is called and nothing is stored.
        """
        rt = runtime.load_runtime(as_json=as_json)
        hw = runtime.hardware_state(rt.rules)
        router = runtime.build_router(rt, hw, tokens)
        request = RouteRequest(
            messages=_messages(prompt, image),
            surface="cli",
            tools=[_PROBE_TOOL] if tools else None,
            scope=scope,
            project=rt.project,
        )
        try:
            plan = asyncio.run(router.plan(request))
        except ScopeError as exc:
            fail(exc.code, exc.message, fix=exc.fix_hint, exit_code=EXIT_SCOPE, as_json=as_json)
        except NoRouteError as exc:
            hints = [t.text for t in exc.suggestions]
            if exc.code == "no_candidates" and not list_models(rt.db):
                hints.insert(0, "No models are known yet. Run: headwaters models refresh")
            fail(
                exc.code,
                exc.message,
                fix=" ".join(hints) or None,
                exit_code=EXIT_NO_ROUTE,
                as_json=as_json,
                extra={
                    "etas_s": exc.etas,
                    "rejected": [{"model": x.model_id, "reason": x.reason} for x in exc.rejected],
                },
            )
        if as_json:
            emit_json(plan_payload(plan))
        else:
            print_plan(plan)
