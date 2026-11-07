# Headwaters Beta release notes

> **Status: not released.** These notes accompany the HDW-509 review gate. They do not announce a
> shipping build — the gate exits `not ready` (see [reports/HDW-509.md](reports/HDW-509.md)). They
> exist so that, when the owner records the Stage-2 evidence and fixes the two code blocks below,
> the beta has a ready-to-publish changelog.

## Version

- `pyproject.toml` declares **`0.0.0`**.
- [BETA.md](BETA.md) calls the event **"Beta 0.1"**.
- The HDW-509 ticket title says **"Beta 0.2"**.

These three disagree (see FU-509-4). Nothing here chooses among them; the owner must pick one label
before publishing, because the gate's `packaging-files` check will otherwise publish a build with a
stale version string.

## What the beta includes (free during beta)

The **core** set is enforced by `headwaters.core.features.CORE_FEATURES` and gate-checked by
`release-gate`'s `docs-core-features` step — this list is a restatement of what the gate already
checks, not a new claim:

- Gateway (OpenAI + Anthropic endpoints, bring-your-own keys)
- MCP server (8 front-door tools)
- Automatic failover across walls (rate limit, out of credit, overloaded)
- Free and local scopes, Private mode, project privacy rule, secret detection
- Hardware guard (VRAM/KV fit, computed `num_ctx`, warm model, battery)
- Basic verification (parse, placeholder scan, `git apply --check`)
- Installer for VS Code/Copilot, Cline, OpenCode, Claude Desktop; doctor; CLI; status page
- Catalog updates

**★ features are free during beta and may become Headwaters Pro at 1.0.** At this commit the
installed ★ features are: **ledger**, **paid_scopes**, **tray**, **usage_report**, and **wizard** —
five, not four. (This is the same fact that currently breaks four stale tests; see below and
FU-509-2.) The ★/core boundary is enforced by `importlinter`'s `core-is-a-leaf` contract, which
passed 7/0 at this commit.

**No tracking** (unchanged, gate-checked via `docs-privacy`): the `telemetry` rule is the literal
`false`, the request log drops `prompt`/`content`/`messages`, and the only outbound calls are the
ones a request itself makes to its provider — unless a cloud scope is enabled.

## What is not in the beta

These are the Stage-2 gaps, copied from the gate's blocked owner steps and
[BETA.md](BETA.md) / [RELEASE_EVIDENCE.md](RELEASE_EVIDENCE.md) — not new findings:

- No binary published: no Microsoft Store listing, no itch build, no signed installer.
- The Windows clean-VM run and the Linux clean-run have not been performed.
- No Authenticode certificate: the Windows payload is unsigned and will show SmartScreen.
- The `windows-beta` / `linux-beta` itch channels are empty (nothing uploaded with butler).
- The VS Code extension is not on either registry (see FU-507-1..7): no publish credentials, the
  `headwaters` publisher is unverified, there is no icon, and the listing links GitHub rather than
  itch.

## What the owner must do before publishing

Two things the gate **cannot see** but the audit surfaced (fix these first — CI is red today):

1. **Make `mypy` pass.** `uv run mypy` fails on every CI matrix job with **124 errors / 25 files**
   (19 in shipped `headwaters/` source, 105 in `tests/`). Representative source errors are in
   `core/catalog/schema.py:254`, `core/keys.py:515`, `core/catalog/discovery.py:219`,
   `control/status_page.py:480`, `daemon/service.py:251/254/590`, `features/ledger/report.py:165+`,
   `features/wizard/*`, `features/tray/doctor_window.py:275/279`. See the punch list in
   [reports/HDW-509.md](reports/HDW-509.md) for the full set.
2. **Fix the 7 stale tests** (FU-509-2): `test_cli_misc.py:40/47`, `test_cli_pick.py:53/75`,
   `test_cli_scope.py:27`, `test_hdw504_tray.py:1028`, `test_usage_report.py:94`. They assert the
   pre-ledger/wizard/paid-scopes world and will keep the suite red until refreshed.

Then, the owner must record the **11 owner steps** the gate requires (this is the existing gate list,
verbatim, not an added checklist): `install-12gb`, `install-16gb`, `host-stability`, `p1-week`,
`windows-clean-vm`, `linux-clean-run`, `code-signing`, `itch-windows`, `itch-linux`,
`open-source-review`, `tester-registration`. Of these, the five closed-alpha exit criteria
(`install-12gb`, `install-16gb`, `host-stability`, `p1-week`, `tester-registration`) are the minimum
for beta to open; the rest are release criteria that block a public upload.

## Reconciliation note

Facts in this file come from two places and are kept distinct on purpose: the gate machinery
(`headwaters/control/release_gate.py` — 13 machine checks + 11 owner steps, evidence in
`docs/release/evidence.json`) supplies the "what is free / what is not done / what the owner must
record" content; the audit (probes P1–P7 in the report) supplies the two code facts the gate cannot
see — the red `mypy` step and the 7 stale tests — plus the ★-feature count drift and the
Marketplace gaps. Nothing here duplicates a new checklist; `release-gate` remains the single source
of truth for readiness.
