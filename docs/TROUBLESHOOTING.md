# Troubleshooting

Six failure modes account for almost every bad first day. Start with `headwaters doctor`: it prints
one row per area (Ollama, GPU, ports, the daemon, the token, saved keys, the catalog, git, the MCP
launcher) with the exact command that fixes a red row. Add `--host <id>` for the three rows a host
of that kind actually sees, and `--bundle` for a redacted zip to attach to a bug report.

## Context truncation

The provider answers with a 400 "context length exceeded", the reply stops mid-sentence, or the
model says it never saw the file. This is the request being larger than the window that was
reserved for it, not a Headwaters bug. For a local model the window is computed by the hardware
guard: it walks `local.ctx_steps` (8192 up to 65536 by default) and takes the largest step whose
weights plus KV cache fit in VRAM after `local.hardware.reserve_display_gb`, so a 12 GB card lands
on a smaller step than a 24 GB one, and a long prompt can exceed the step that was chosen. `headwaters doctor`
shows the computed context per model. Fix it by sending fewer tokens, by raising the reserve only if
the GPU really has the room, or by adding a larger step to `local.ctx_steps`. Truncation that only
happens in one host is that host's own limit: check its model context setting, not ours.

## Tool-heavy requests

A prompt of a few words fails with a message about context even though the model is installed.
Every MCP server a host enables contributes its whole `tools` array to the request estimate, so
two servers can be tens of thousands of tokens before the user types anything: 109 Summer Engine
schemas measure about 44,000 tokens and our eight `headwaters_*` front-door tools about 2,400. The
error names that split, the largest namespaces and the fixes that work: a wider scope such as
`headwaters/auto`, removing a server, or `tools.prune_to_fit: true` in `rules.yaml`. That setting
is off by default; switched on, Headwaters drops the largest non-`headwaters` namespaces until a
local model fits and records what it dropped in the decision reasons, so `headwaters explain` (and
anything else that reads them) shows the drop and its size. It
never drops the `headwaters_*` tools, never runs on the free or cloud tiers, and never prunes a
request whose message or images alone already exceed the window.

## Tool-call failures

A host reports "tool not found", "invalid tool schema" or an MCP server that immediately exits, or
the model calls a tool and nothing happens. The MCP server is the daemon's (`headwaters mcp-path`
prints the launcher a host should call), so the usual cause is a host config that points at an old
path, a stale launcher, or a daemon that is not running. Run `headwaters doctor --host <id>`: it
tests the three things that host sees and names the file it read. Then re-run
`headwaters install --host <id>` to rewrite the entry from the current build, restart the host
so it re-reads its config, and check `headwaters status` for the daemon. If the tool call itself
is being rejected, `headwaters doctor --bundle` captures the redacted environment for a report.

## Cold loads

The first local answer takes 30 to 90 seconds and later ones are instant, or the first call times
out. The model has to be loaded into VRAM before it can stream: the executor allows
`executor.local_cold_first_token_s` (240 s) for that first token and
`executor.local_warm_first_token_s` (60 s) once it is resident, so a cold call that fails is usually
a load that is slower than 240 s, not a broken model. `local.keep_alive` (default `30m`) keeps the
model resident between calls and `local.keep_warm_model` keeps one model warm on purpose. Check
`headwaters models list` for what is loaded, and the GPU row in `headwaters doctor` for a card that
is busy with something else before blaming the timeout.

## Model-name mismatch

A request is answered by a model nobody asked for, or the provider returns "model not found". Any
name that starts with `headwaters/` (`headwaters/auto`, `headwaters/free`, `headwaters/local`,
`headwaters/cloud`, plus `+quality`, `+verify`, `+noverify`) is routed by the picker. A name in
`models.aliases` is resolved to what it stands for. Every other name is pinned as-is and answers
from that model, so a host that asks for a vendor name it does not own gets exactly that: no
routing and an error, or a pinned answer. `headwaters models list` shows what is routable right now
and `headwaters pick` shows the ranking behind the choice. Fix it by sending `headwaters/auto`,
by adding the host's own name to `models.aliases`, or by using a name the provider really serves.

## Port conflicts

The daemon will not start, or `headwaters status` cannot reach it, because another program already
holds a port. Headwaters uses 4000 for the OpenAI/Anthropic gateway and 4001 for the local status
page; the doctor's port rows say which of the two is taken and by what. The usual culprits are a
second copy of Headwaters (a stale daemon from another checkout), another local proxy, or a
container publishing 4000. Run `headwaters stop --force` to clear a stale daemon, then
`headwaters status --open` to confirm a clean one. If the port is genuinely wanted by something
else, change `gateway.port` / `dashboard.port` in `rules.yaml` and point the host at the new one.

## Sysmem fallback

Local generation drops to a few tokens per second after a model that used to be fast. Ollama is
spilling layers into system RAM because the weights plus KV cache do not fit in VRAM, and system
memory is an order of magnitude slower. The hardware guard measures this: a throughput ratio below
`local.hardware.sysmem_slow_ratio` (0.5 of expected) with a spill beyond
`local.hardware.sysmem_spill_tolerance` marks the model as spilling, and the picker then ranks it
last instead of first. The fix is a smaller model or quantisation, a smaller context step, or a
quieter GPU: close whatever else is holding VRAM, since a card above
`local.hardware.busy_threshold_pct` (50%) is treated as busy and the reserve for the display is
reclaimed only when the iGPU drives it. `headwaters pick` shows the models that fit before the ones
that do not.
