# Headwaters for VS Code

Your [Headwaters](https://github.com/Howitzer-10/Headwaters) models, in the VS Code model picker,
plus a status bar that tells you what the daemon is actually doing.

Everything here talks to a Headwaters daemon running on the same machine. The extension does not
contain a model, does not ship a key, and does not phone anywhere else.

> **Status: not yet on the Marketplace or Open VSX.** The `.vsix` is built and installable
> (`Install from VSIX…`, or `code --install-extension headwaters-vscode-0.1.0.vsix`), but the
> listings do not exist yet. Nothing is published while the credentials are still an owner step.

## What you get

**Models.** Headwaters exposes one vendor (`Headwaters`) in the language model picker. The models
you see are the ones the daemon reports, not a hard-coded list, and each keeps the daemon's own
context window and capability floor. `Headwaters: Refresh Models` re-reads them.

**A status bar item**, on the right, that reads:

```
$(pulse) paid tier · warm gemma4:12b · 2 walls · savings $1.84 · $(lock) Private
```

- **Tier and warm model** come from the daemon's `GET /v1/status` (`tray.tier` and
  `tray.warm_model`).
- **Wall count** comes from the same `tray.walls` block, and is only the walls the daemon is
  currently reporting — none means nothing is wrong, not that nothing was checked.
- **Savings** comes from the `headwaters_status` MCP tool, which is the only surface that carries
  it. If the ledger feature is switched off or absent, the item shows `—`, which is a different
  fact from a real `$0.00`; the tooltip says which one you are looking at.
- **The purple lock** appears when `private_lock` is on. It is `charts.purple`, not a background
  colour, so it stays legible in light, dark and both high-contrast themes.

Click the item to open the dashboard.

When the daemon stops, or the token stops being accepted, the item switches to
`$(error) Headwaters — daemon not reachable` on the next tick. It never leaves the last reading on
screen pretending to be current. The item polls once a minute, one request at a time, and a failed
read waits for the next tick rather than retrying in a loop.

## Commands

| Command | What it does |
| --- | --- |
| `Headwaters: Refresh Status` | Reads the daemon now instead of waiting for the next tick. |
| `Headwaters: Set Scope…` | Writes `default_scope` through the daemon's own `POST /v1/scope` — the same route the tray uses. Pick `auto`, `free`, `local` or `cloud`. |
| `Headwaters: Toggle Private Mode` | Flips `private_lock`. The same purple lock the tray shows. |
| `Headwaters: Pin Model…` | Pins a model for **front-door MCP tool calls only**. |
| `Headwaters: Create Handoff Packet…` | Asks the daemon to write a handoff packet for the current goal, then opens the packet it wrote. |
| `Headwaters: Run Doctor` | Runs `headwaters doctor --json` and opens the report. Needs the CLI on `PATH`; if it is missing, the extension says so instead of guessing. |
| `Headwaters: Open Dashboard` | Opens the dashboard the daemon bound in `daemon.json`. If no port was bound, it says that. |
| `Headwaters: Refresh Models` | Re-reads the model list from the gateway. |

### What "Pin Model" does and does not do

The pin writes the daemon's `mcp.pinned_model` setting and affects **front-door MCP tool calls**.
It does **not** change which model answers requests on the gateway's OpenAI-compatible or
Anthropic-compatible routes: those pin per request, with `x-headwaters-pin` and an explicit model
name. The command says so in its confirmation, in its input prompt and in the tooltip, because a
pin badge that implies the other thing would be a lie in the UI.

## Requirements

- VS Code 1.104 or newer.
- A Headwaters daemon running locally. The extension finds it through the state directory written
  by the daemon (`$HEADWATERS_HOME`, or the platform default) and never guesses a port: an
  unbound port is reported as unbound.

## Privacy

The extension reads two local surfaces — `GET /v1/status` and the MCP endpoint at `/mcp` on the
loopback API port — with the bearer token the daemon itself wrote to its state directory. It sends
no telemetry. Handoff packets, the doctor report and the status reads all stay on this machine,
except for whatever your daemon is already configured to send upstream.

## Development

```sh
npm ci
npm run typecheck
npm test          # real extension host against a loopback mock gateway
npm run package   # -> headwaters-vscode-<version>.vsix
```

The tests start a mock daemon on `127.0.0.1` and drive the real commands through
`vscode.commands.executeCommand`, so a command that only looks wired up fails the suite.

## Links

- Source, issues and the rest of Headwaters: <https://github.com/Howitzer-10/Headwaters>
- Ticket history and decisions: `docs/reports/` and `docs/DECISIONS.md` in that repository.

## Licence

Proprietary; all rights reserved. See [LICENSE](LICENSE). No licence is granted yet.
