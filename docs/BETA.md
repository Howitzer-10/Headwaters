# Headwaters Beta

Beta 0.1 is a working build, not a shipped one. This page says what beta means, what is promised
while it lasts, what a person has to prove before the gate will call the build ready, and what is
still missing. Every claim here is checked by `headwaters release-gate`; if this page and the code
disagree, the gate fails.

> **This is a Stage 2 page.** It describes the path to distribution and sale. There is no public beta
> and no closed alpha running, and there is no 0.1 release of any kind. The closed-alpha criteria,
> the clean-machine runs and the uploads described below are prerequisites the owner works through
> during Stage 2, only after Phase 7 is complete. For what is actually happening now, read
> [PLAN.md](PLAN.md).

## What beta means

A beta is a Stage 2 event here, not something in progress. When it happens, you get the whole
product, free, for as long as beta lasts, with no account and no telemetry. You do not get a
stability promise we have not measured: the closed-alpha criteria below, the clean Windows VM run and
the clean Linux run have not been performed by a person yet, and the Windows build is not
code-signed. None of that work is underway today; Stage 1 is the owner running the build from source
and testing it, and [PLAN.md](PLAN.md) says when that changes. Read
[RELEASE_EVIDENCE.md](RELEASE_EVIDENCE.md) for the exact list of runs that still have to happen, and
run `headwaters release-gate` to see the same list machine checked.

## Promises

### Core stays free

<!-- promise: core-free -->
The gateway, the MCP server, automatic failover, the free and local scopes with Private mode and
secret detection, the hardware guard, basic verification, the installer and the catalog updates are
core. They are in every build, they work with no subscription, and nothing in that list moves behind
a paywall at 1.0 or later. A `★` feature may become part of Headwaters Pro at 1.0; a core feature
never does, and import-linter enforces the code boundary between the two.

### No tracking

<!-- promise: no-tracking -->
Headwaters has no telemetry of any kind: no analytics, no usage counters, no crash reporting, no
account. The `telemetry` rule is the literal `false`, and the request log drops `prompt`, `content`
and `messages`, so your code is never written to a file by us. The only network calls are the ones
your own request makes to the provider you chose, unless you turn on a cloud scope.

### Breaking changes during beta

<!-- promise: breaking-changes -->
The beta is for people who can tolerate a broken release now and then. Rules, CLI flags, generated
host config and the shape of the state directory may change between beta builds; when a change needs
your action it is called out in the release notes with the exact command to run. Saved keys and the
catalog survive upgrades, and `headwaters doctor` is the first thing to run after one.

### Testers

<!-- promise: testers-pro -->
Everyone on the beta tester list gets Headwaters Pro free if a free/Pro split ships at 1.0: the list
is the registry of testers kept for exactly this, and the promise is the reward for reporting bugs
during the beta. Joining is one message to the maintainer, leaving is one message back, and neither
touches the free list above.

## The free list

These are the core features as the code registers them (`headwaters.core.features.CORE_FEATURES`),
so the page cannot drift from the build:

- Gateway: OpenAI and Anthropic endpoints, bring your own keys
- MCP server with the 8 front-door tools
- Automatic failover across walls (rate limit, out of credit, overloaded)
- Free and local scopes, Private mode, project privacy rule, secret detection
- Hardware guard: VRAM/KV fit, computed num_ctx, warm model, battery
- Basic verification: parse, placeholder scan, git apply --check
- Installer for VS Code/Copilot, Cline, OpenCode, Claude Desktop; doctor; CLI; status page
- Catalog updates

★ = free during beta; may become part of Headwaters Pro at 1.0

## Closed-alpha exit criteria

Beta 0.1 opens when a person has recorded, in `docs/release/evidence.json`, all of the following.
Each row names the key the gate validates and the proof it insists on; nothing here is inferred
from a test or from CI.

| What | Evidence key | What the gate accepts |
| --- | --- | --- |
| Install in 10 minutes or less on a 12 GB machine | `install-12gb` | `minutes` <= 10, `gpu_gb` >= 12, `date`, `machine` |
| Install in 10 minutes or less on a 16 GB machine | `install-16gb` | `minutes` <= 10, `gpu_gb` >= 16, `date`, `machine` |
| Stable in all four hosts | `host-stability` | `hosts.vscode`, `hosts.cline`, `hosts.opencode`, `hosts.claude-desktop` notes, `date` |
| One week of closed alpha with no open P1 | `p1-week` | `days` >= 7, `p1_open` = 0, `from`, `to` |
| Tester list exists | `tester-registration` | `system`, `registered` >= 1 |

The Windows and Linux clean-machine runs, the code-signing run and the two itch uploads are
release criteria rather than alpha criteria, and the gate lists them separately.

## What is not done

This is the Stage 2 gap list. It is not being closed during Stage 1; it is written down so that
Stage 2 has a definition of done.

- No binary is published. There is no Microsoft Store listing, no itch build and no signed
  installer that anyone has downloaded.
- The Windows clean-VM run in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) has not been performed.
- The Linux clean-run in [RELEASE_CHECKLIST_LINUX.md](RELEASE_CHECKLIST_LINUX.md) has not been
  performed.
- No Authenticode certificate has been supplied, so the Windows payload is unsigned and will show
  a SmartScreen warning.
- Nothing has been uploaded with butler; the `windows-beta` and `linux-beta` channels are empty.

`headwaters release-gate` reports all of the above as blocked owner steps and exits non-zero. That
is the intended state until a person records the evidence, not a bug to work around.
