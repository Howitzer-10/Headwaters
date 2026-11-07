"""The wizard's page: one step at a time, plain HTML, no scripts and no secrets.

Everything here is a function of a dict the plugin already built, plus the CSRF token. There is
no client-side scripting, no external stylesheet, no image, no redirect that carries state and no
value from a request reflected back into the page — so "no secret, key, token or environment
variable name reaches the page" is a property of the module, not of a reviewer's memory.

Two shapes recur and are worth naming. Every action is a ``<form method="post">`` to one path in
:data:`headwaters.core.wizard.WIZARD_PATHS` with the CSRF token as a hidden field, and the *state*
a form carries is only ever the fingerprint of what the page displayed — never the new value
itself. A page cannot ask the server to write something the person was not shown.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from headwaters.core.wizard import WIZARD_PATHS

__all__ = ["render_page"]

PAGE_TITLE = "Headwaters setup"

#: Per-step marks in the navigation rail.
_MARK: dict[str, str] = {"done": "✓", "skipped": "-"}

_CSS = (
    "body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:0 auto;padding:1.5rem;"
    "color:#1b1b1b;background:#fdfdfc}"
    "header{border-bottom:1px solid #ddd;padding-bottom:.75rem;margin-bottom:1rem}"
    "nav ol{list-style:none;padding:0;display:flex;flex-wrap:wrap;gap:.4rem;margin:.75rem 0 0}"
    "nav li a{display:block;padding:.25rem .5rem;border:1px solid #ddd;border-radius:.25rem;"
    "text-decoration:none;color:#333}"
    "nav li.current a{border-color:#333;font-weight:600}"
    "h1{font-size:1.35rem;margin:.2rem 0}h2{font-size:1.1rem;margin:1.5rem 0 .4rem}"
    "pre{background:#f4f4f2;padding:.6rem;overflow-x:auto;font-size:.85rem}"
    "table{border-collapse:collapse;width:100%;font-size:.9rem}"
    "th,td{text-align:left;border-bottom:1px solid #eee;padding:.3rem .4rem;vertical-align:top}"
    "code{font-size:.9rem}.ok{color:#1a7f37}.bad{color:#b42318}.muted{color:#666}"
    ".note{background:#f4f4f2;padding:.5rem .7rem;margin:.6rem 0;font-size:.9rem}"
    ".banner{padding:.5rem .7rem;margin:1rem 0;border-left:3px solid #333}"
    ".banner.error{border-color:#b42318}.banner.notice{border-color:#1a7f37}"
    "form{margin:.5rem 0}fieldset{border:1px solid #ddd;padding:.6rem}label{margin-right:.6rem}"
)


@dataclass(frozen=True)
class _Ctx:
    """What every form needs: the token, and the path table it must agree with."""

    csrf: str
    step: str

    def action(self, name: str) -> str:
        for path, action in WIZARD_PATHS.items():
            if action == name:
                return path
        raise KeyError(name)

    def fields(self) -> str:
        return hidden("csrf", self.csrf) + hidden("step", self.step)


def esc(value: Any) -> str:
    """Escape for text and attributes alike; ``None`` renders as nothing, not as "None"."""
    return "" if value is None else html.escape(str(value), quote=True)


def hidden(name: str, value: Any) -> str:
    return f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'


def _pre(text: Any) -> str:
    return f"<pre>{esc(text)}</pre>"


def _banner(notice: str, error: str) -> str:
    out = []
    if error:
        out.append(f'<p class="banner error" role="alert">{esc(error)}</p>')
    if notice:
        out.append(f'<p class="banner notice">{esc(notice)}</p>')
    return "".join(out)


def _form(
    ctx: _Ctx,
    action: str,
    label: str,
    fields: Mapping[str, Any] | Sequence[tuple[str, Any]] = (),
    *,
    extra: str = "",
    danger: bool = False,
) -> str:
    pairs = list(fields.items()) if isinstance(fields, Mapping) else list(fields)
    body = "".join(hidden(name, value) for name, value in pairs)
    klass = ' class="danger"' if danger else ""
    return (
        f'<form method="post" action="{esc(ctx.action(action))}"{klass}>'
        f"{ctx.fields()}{body}{extra}"
        f'<button type="submit">{esc(label)}</button></form>'
    )


def _rows(rows: Sequence[Sequence[Any]], header: Sequence[str] = ()) -> str:
    if not rows:
        return ""
    head = "<tr>" + "".join(f"<th>{esc(cell)}</th>" for cell in header) + "</tr>" if header else ""
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table>{head}{body}</table>"


def _state_cell(value: object) -> str:
    if value is True:
        return '<span class="ok">✓</span>'
    if value is False:
        return '<span class="bad">✗</span>'
    return '<span class="muted">—</span>'


def _detect(view: Mapping[str, Any], ctx: _Ctx) -> str:
    gpu = view.get("gpu")
    ollama = view.get("ollama") or {}
    if gpu:
        detected = f"{esc(gpu.get('name'))} — {esc(gpu.get('total_gb'))} GiB"
    elif view.get("no_gpu_detected"):
        detected = (
            '<span class="bad">No GPU was detected.</span> Nothing below pretends otherwise; '
            "the router will only suggest models that fit what this machine reports."
        )
    else:
        detected = '<span class="muted">The hardware probe returned nothing.</span>'
    hardware = [("GPU", detected)]
    if view.get("budget_gb") is not None:
        hardware.append(("VRAM budget", f"{esc(view.get('budget_gb'))} GiB"))
    if view.get("profile"):
        hardware.append(("Catalog profile", f"<code>{esc(view.get('profile'))}</code>"))
    if not view.get("catalog_present"):
        hardware.append(
            (
                "Catalog",
                '<span class="bad">the catalog did not load</span>, so no model can be recommended',
            )
        )
    out = ["<h2>This machine</h2>", _rows([[name, value] for name, value in hardware])]

    notes = view.get("hardware_notes") or []
    if notes:
        out += ["<h2>What the probe noticed</h2>", "<ul>"]
        out += [f"<li>{esc(note)}</li>" for note in notes]
        out.append("</ul>")

    if ollama.get("version"):
        head = f"Ollama answered: version {esc(ollama.get('version'))}"
    else:
        head = (
            '<span class="bad">Ollama did not answer.</span> '
            f"{esc(ollama.get('error') or '')} Models cannot be listed or pulled until it does."
        )
    count = ollama.get("installed_count")
    out += [
        "<h2>Ollama</h2>",
        f"<p>{head}</p>",
        f'<p class="muted">Native API at <code>{esc(ollama.get("host"))}</code>. '
        + (f"{esc(count)} model(s) installed.</p>" if count is not None else "</p>"),
    ]

    out.append("<h2>Ollama's environment</h2>")
    env = view.get("env") or {}
    changes = env.get("changes") or []
    if changes:
        out.append("<p>The doctor's three rules, as the difference they would make:</p>")
        out.append(_pre("\n".join(str(line) for line in changes)))
    elif env.get("notes"):
        out.append("<p>Nothing to change: Ollama already matches the doctor's rules.</p>")
    else:
        out.append("<p>Nothing to change.</p>")
    for note in env.get("notes") or []:
        out.append(f'<p class="note">{esc(note)}</p>')
    if changes and env.get("writable"):
        out.append(
            _form(
                ctx,
                "env_apply",
                "Apply these three values",
                [("fingerprint", env.get("fingerprint"))],
            )
        )
        out.append(
            '<p class="muted">Written to your own user environment '
            "(<code>HKCU\\Environment</code>), never to the machine's. Ollama has to be "
            "restarted to see them; the value that was there before is kept so the next button "
            "can put it back.</p>"
        )
    elif changes:
        out.append(
            '<p class="note">Headwaters cannot write Ollama&rsquo;s environment on this platform, '
            "so nothing was written. Set them yourself, then reload this step:</p>"
        )
        out.append(_pre("\n".join(str(line) for line in changes)))
    if view.get("env_undoable"):
        out.append(_form(ctx, "env_undo", "Put the previous values back", danger=True))
    return "".join(out)


def _models(view: Mapping[str, Any], ctx: _Ctx) -> str:
    out = ["<h2>What the catalog recommends</h2>"]
    if view.get("profile"):
        out.append(f'<p class="muted">Profile: <code>{esc(view.get("profile"))}</code></p>')
    else:
        out.append(f'<p class="note">{esc(view.get("empty_reason"))}</p>')
    if not view.get("installed_known"):
        out.append(
            '<p class="note">Ollama did not answer, so "installed" below is unknown rather than '
            "false.</p>"
        )
    rows = []
    for rec in view.get("recommendations") or []:
        model = f"<code>{esc(rec.get('model'))}</code>"
        if rec.get("refusal"):
            action = f'<span class="bad">{esc(rec.get("refusal"))}</span>'
        elif rec.get("installed"):
            action = '<span class="ok">installed</span>'
        elif rec.get("pullable"):
            action = _form(ctx, "pull", "Pull", [("model", rec.get("model"))])
        else:
            action = '<span class="muted">not pullable</span>'
        name_cell = model + (
            f'<br><span class="muted">{esc(rec.get("note"))}</span>' if rec.get("note") else ""
        )
        rows.append(
            [
                esc(rec.get("role")),
                name_cell,
                esc(rec.get("fits")),
                esc(rec.get("num_ctx")),
                "" if rec.get("size_gb") is None else f"{esc(rec.get('size_gb'))} GB",
                action,
            ]
        )
    if rows:
        out.append(_rows(rows, ("Role", "Model", "Fit", "Context", "Weights", "")))

    job = view.get("pull")
    out.append("<h2>The pull</h2>")
    if not job:
        out.append("<p>No pull has been started from here.</p>")
    else:
        percent = job.get("percent")
        line = (
            f"{esc(job.get('model'))}: <strong>{esc(job.get('state'))}</strong>, "
            f"{esc(job.get('status'))}"
        )
        if percent is None:
            line += (
                ' — <span class="muted">Ollama reported no totals for this pull, so there is no '
                "honest percentage to show.</span>"
            )
        else:
            line += f" — {esc(percent)}%"
        if job.get("error"):
            line += f' <span class="bad">{esc(job.get("error"))}</span>'
        out.append(f"<p>{line}</p>")
        if view.get("pull_waiting"):
            out.append(
                '<p class="muted">The pull runs in the daemon, not in this page: reload to see '
                "where it is. Reloading does not start it again.</p>"
            )
    if view.get("ollama_error"):
        out.append(f'<p class="note">{esc(view.get("ollama_error"))}</p>')
    return "".join(out)


def _ides(view: Mapping[str, Any], ctx: _Ctx) -> str:
    out = ["<h2>Editors</h2>"]
    hosts = view.get("hosts") or []
    if not hosts:
        out.append("<p>No editor host is known to the installer.</p>")
    for host in hosts:
        title = esc(host.get("title") or host.get("host"))
        status = esc(host.get("status"))
        out.append(f'<h3>{title} <span class="muted">({status})</span></h3>')
        if host.get("errors"):
            for error in host["errors"]:
                out.append(f'<p class="banner error">{esc(error)}</p>')
            continue
        if host.get("diff"):
            out.append(_pre(host.get("diff")))
        else:
            out.append('<p class="muted">This host needs no change.</p>')
        for note in host.get("notes") or []:
            out.append(f'<p class="note">{esc(note)}</p>')
        for problem in host.get("problems") or []:
            out.append(f'<p class="banner error">{esc(problem)}</p>')
        if host.get("status") == "changes":
            out.append(
                _form(
                    ctx,
                    "host_apply",
                    f"Write {esc(host.get('title'))}'s config",
                    [("host", host.get("host")), ("fingerprint", host.get("fingerprint"))],
                )
            )
    out.append(
        '<p class="muted">Applying one re-reads that editor&rsquo;s config first and refuses if it '
        "changed since this page was drawn, so an edit you made in the editor is never "
        "overwritten.</p>"
    )
    return "".join(out)


def _keys(view: Mapping[str, Any], ctx: _Ctx) -> str:
    out = ["<h2>Providers with a key</h2>"]
    if view.get("error"):
        out.append(f'<p class="banner error">{esc(view.get("error"))}</p>')
    providers = view.get("providers") or []
    if providers:
        out.append("<ul>")
        out += [f"<li>{esc(name)}</li>" for name in providers]
        out.append("</ul>")
    else:
        out.append("<p>No provider has a key yet, so only local models can answer.</p>")
    out.append(f'<p class="note">{esc(view.get("note"))}</p>')
    out.append(
        f'<p><a href="{esc(view.get("manage_url"))}">Add or remove a key in the dashboard</a></p>'
    )
    return "".join(out)


def _mode(view: Mapping[str, Any], ctx: _Ctx) -> str:
    def select(name: str, current: Any, options: Sequence[Any]) -> str:
        items = "".join(
            f'<option value="{esc(option)}"'
            f"{' selected' if str(option) == str(current) else ''}>{esc(option)}</option>"
            for option in options
        )
        return f'<label>{esc(name)} <select name="{esc(name)}">{items}</select></label>'

    extra = select("scope", view.get("scope"), view.get("scope_options") or ())
    extra += select("strategy", view.get("strategy"), view.get("strategy_options") or ())
    out = [
        "<h2>Defaults</h2>",
        f"<p>In force now: scope <code>{esc(view.get('scope'))}</code>, strategy "
        f"<code>{esc(view.get('strategy'))}</code>.</p>",
        _form(ctx, "mode_set", "Save these defaults", extra=extra),
    ]
    if view.get("private_lock"):
        out.append(
            '<p class="note">Private mode is on, so nothing leaves this machine whatever the '
            "scope says. The wizard does not turn that off.</p>"
        )
    if view.get("airplane"):
        out.append('<p class="note">Airplane mode is on: no cloud provider is used.</p>')
    return "".join(out)


def _autostart(view: Mapping[str, Any], ctx: _Ctx) -> str:
    out = ["<h2>The at-logon task</h2>"]
    state = view.get("state")
    if not view.get("supported"):
        out.append(f'<p class="note">{esc(view.get("note"))}</p>')
    else:
        out.append(f"<p>State now: <code>{esc(state)}</code>.</p>")
        if view.get("target"):
            out.append(f'<p class="muted">Task file: <code>{esc(view.get("target"))}</code></p>')
        for argv in view.get("argv") or []:
            out.append(f"<p><code>{esc(argv)}</code></p>")
        if view.get("xml"):
            out.append(
                f"<details><summary>The task definition</summary>{_pre(view.get('xml'))}</details>"
            )
        if state == "enabled":
            out.append(_form(ctx, "autostart_set", "Turn the task off", [("on", "0")], danger=True))
        else:
            out.append(_form(ctx, "autostart_set", "Create the task and turn it on", [("on", "1")]))
        if view.get("note"):
            out.append(f'<p class="muted">{esc(view.get("note"))}</p>')
        out.append(
            '<p class="muted">Turning it off keeps the task definition and disables it; '
            "removing it entirely is <code>headwaters autostart remove</code>.</p>"
        )
    return "".join(out)


def _report(view: Mapping[str, Any], ctx: _Ctx) -> str:
    out = ["<h2>The usage report</h2>"]
    if not view.get("discovered"):
        out.append(f'<p class="note">{esc(view.get("note"))}</p>')
        out.append(
            "<p>This step stays here, and can be skipped, so the setup is never presented as "
            "complete when a package is simply missing.</p>"
        )
        return "".join(out)
    preview = view.get("preview") or {}
    rows = [
        ("Switch", "on" if view.get("enabled") else "off (the shipped default)"),
        ("Endpoint", "set" if view.get("endpoint_set") else "not set, so nothing can be sent"),
        ("Payload version", esc(view.get("payload_version"))),
        ("Previewed version", esc(view.get("consented_version") or "not yet")),
        ("Fields", f"{esc(view.get('fields'))} counted, none free text"),
    ]
    out.append(_rows([[name, value] for name, value in rows], ("Setting", "Value")))
    out.append("<h3>The exact payload</h3>")
    out.append(_pre(preview.get("json")))
    shape = preview.get("shape") or []
    out.append(_rows([(esc(path), esc(rule)) for path, rule in shape], ("Field", "Allowed value")))
    if preview.get("gate"):
        out.append(f'<p class="note">Gate: {esc(preview.get("gate"))}</p>')
    if view.get("consent_current"):
        out.append(_form(ctx, "report_consent", "Turn the report off", [("on", "0")], danger=True))
        out.append(
            '<p class="muted">The preview record is kept when you turn it off, so '
            "<code>headwaters report send</code> can still send one report if you ask.</p>"
        )
    else:
        out.append(
            _form(
                ctx,
                "report_consent",
                "I have read this payload — turn the report on",
                [("on", "1")],
            )
        )
        out.append(
            '<p class="muted">This records consent to <em>this payload version</em>. If a later '
            "release changes the shape, scheduling stops until you look again.</p>"
        )
    if not view.get("endpoint_set"):
        out.append(
            '<p class="note">No endpoint is configured (<code>report.endpoint</code> is empty), so '
            "nothing can be sent even with the report on.</p>"
        )
    return "".join(out)


def _selftest(view: Mapping[str, Any], ctx: _Ctx) -> str:
    rows = [
        (esc(check.get("name")), _state_cell(check.get("ok")), esc(check.get("detail")))
        for check in view.get("checks") or []
    ]
    out = [
        "<h2>Checks</h2>",
        _rows(rows, ("Check", "", "What happened")),
        f"<p>{esc(view.get('passed'))} passed, {esc(view.get('failed'))} failed, "
        f"{esc(view.get('skipped'))} not applicable.</p>",
        _form(ctx, "selftest", "Run the checks again"),
        f'<p class="muted">{esc(view.get("note"))}</p>',
    ]
    return "".join(out)


_SECTIONS = {
    "detect": _detect,
    "models": _models,
    "ides": _ides,
    "keys": _keys,
    "mode": _mode,
    "autostart": _autostart,
    "report": _report,
    "selftest": _selftest,
}


def _nav(steps: Sequence[Mapping[str, Any]], current: Mapping[str, Any], base: str) -> str:
    items = []
    for step in steps:
        mark = _MARK.get(str(step.get("state")), "")
        label = f"{step.get('n')}. {esc(step.get('title'))}"
        if mark:
            label += f' <span class="ok">{mark}</span>'
        klass = ' class="current"' if step.get("id") == current.get("id") else ""
        items.append(f'<li{klass}><a href="{esc(base)}?step={esc(step.get("n"))}">{label}</a></li>')
    return f"<nav><ol>{''.join(items)}</ol></nav>"


def render_page(
    *,
    steps: Sequence[Mapping[str, Any]],
    current: Mapping[str, Any],
    csrf: str,
    notice: str = "",
    error: str = "",
    base: str = "/wizard",
) -> str:
    """One step of the wizard, with every other step one click away and marked if recorded."""
    step_id = str(current.get("id") or "")
    ctx = _Ctx(csrf=csrf, step=step_id)
    section = _SECTIONS.get(step_id)
    body = section(current.get("view") or {}, ctx) if section else "<p>Unknown step.</p>"
    recorded = current.get("state")
    footer = ""
    if recorded:
        footer = (
            f'<p class="muted">This step is recorded as <strong>{esc(recorded)}</strong>. '
            "Running it again changes nothing that was already applied.</p>"
            + _form(ctx, "mark", "Clear this step&#8217;s record", [("state", "clear")])
        )
    footer += _form(ctx, "mark", "Mark this step done", [("state", "done")])
    footer += _form(ctx, "mark", "Skip this step", [("state", "skipped")])
    position = 0
    for index, step in enumerate(steps):
        if step.get("id") == step_id:
            position = index
    links = []
    if position > 0:
        links.append(
            f'<a href="{esc(base)}?step={esc(steps[position - 1].get("n"))}">&larr; previous</a>'
        )
    if position + 1 < len(steps):
        links.append(
            f'<a href="{esc(base)}?step={esc(steps[position + 1].get("n"))}">next &rarr;</a>'
        )
    if current.get("error"):
        error = error or str(current.get("error"))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{esc(PAGE_TITLE)}</title><style>{_CSS}</style></head><body>"
        "<header>"
        f'<p class="muted">{esc(PAGE_TITLE)} · step {esc(current.get("n"))} of '
        f"{esc(len(steps))}</p>"
        f"<h1>{esc(current.get('title'))}</h1>"
        f"<p>{esc(current.get('summary'))}</p>"
        f"{_nav(steps, current, base)}"
        "</header>"
        f"{_banner(notice, error)}"
        f"<main>{body}</main>"
        '<footer style="margin-top:2rem;border-top:1px solid #ddd;padding-top:.75rem">'
        f'{footer}<p>{" ".join(links)} · <a href="/">dashboard</a></p>'
        '<p class="muted">Every step can be skipped and repeated. Skipping writes nothing, and '
        "repeating a step never applies a change twice.</p>"
        '<p class="muted">This page is served by the daemon, so it exists only while the daemon '
        "is running and the dashboard is on. On a machine where nothing is set up yet there is no "
        "page to open until the daemon is started: the wizard can finish a setup, not begin one."
        "</p>"
        "</footer></body></html>"
    )
