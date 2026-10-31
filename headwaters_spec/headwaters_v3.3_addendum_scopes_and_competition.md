# Headwaters v3.3 Addendum: Scopes, Privacy Lock, Competitive Positioning

This file applies on top of v3, v3.1 and v3.2. Where they conflict, this file wins.

## A. Routing model: scope × strategy × locks
The earlier `mode` mixed two different things. They are now separate:

| Axis | Question it answers | Values |
| --- | --- | --- |
| **Scope** | *Which tiers may serve this request?* | `auto`, `free`, `local`, `cloud` |
| **Strategy** | *How do we rank inside the scope?* | `cost_saver` (default), `quality_first`, `primary_first` (was `paid_only_until_wall`) |
| **Locks** | *What can never be loosened?* | Private lock, project privacy rule, secret detection |

`local_only` is no longer a mode. It is the `local` scope, optionally enforced by the Private lock.

## B. Virtual models (picker entries)

### B1. Core (always shown)
| Model ID | Display name | Allowed tiers | Money | Data leaves machine | Edition |
| --- | --- | --- | --- | --- | --- |
| `headwaters/auto` | Headwaters Auto | paid keys, `openrouter/auto`, free cloud, local | Own keys only, budget-capped | Yes | Lite (MCP) / Pro (gateway) |
| `headwaters/free` | Headwaters Free ($0) | free cloud, local | Never | Yes (free cloud) | Lite / Pro |
| `headwaters/local` | Headwaters Local (Private) | Ollama only | Never | **Never**, fail-closed | Lite / Pro |
| `headwaters/cloud` | Headwaters Cloud (no GPU) | paid keys, `openrouter/auto`, free cloud | Own keys only, budget-capped | Yes | Pro |

### B2. Optional (hidden by default, `picker.show_extras`)
- `headwaters/quality`: the `auto` scope with the `quality_first` strategy.
- `headwaters/duck`: the rubber-duck overlay on the cheapest fast model allowed by the current lock state.

### B3. Removed as a model
- `headwaters/verified` becomes the setting `verify: always | agentic | off` (default `agentic`).
- Power-user suffixes are allowed: `headwaters/free+verify`, `headwaters/auto+quality`.

### B4. Cloud-scope automation
When `local.on_battery: prefer_cloud` is set, or the GPU-busy check fires, the router (and the VS Code extension's status bar) *suggests* `headwaters/cloud`. It never auto-switches *out of* `local`.

## C. Precedence and switching
```
effective_scope = intersect(
    private_lock ? local : ANY,
    project_rule (.headwaters.yaml privacy: local|free|any),
    secret_detected ? local : ANY,        # configurable: local | refuse | off
    requested_scope (picker model / MCP param),
    default_scope (rules.yaml) if nothing requested
)
```
- A tighter scope always wins. Only the user can loosen a lock, and never mid-request. Project rules can only tighten.
- If the intersection is empty or can't be satisfied, return a structured error with a fix (e.g. "pull qwen2.5vl:7b", "disable Private lock in tray").
- **Switch surfaces:**
  - Picker model (gateway / VS Code extension)
  - Tray toggle "Private Mode" (purple lock icon)
  - VS Code status bar
  - CLI `headwaters scope <auto|free|local|cloud>` / `headwaters private on|off`
  - MCP `set_scope` / `set_private`
  - Prompts `/headwaters-local`, `/headwaters-free`, `/headwaters-auto`
  - Optional global hotkey
- MCP tools accept `scope` and **inherit** the effective scope from locks and project rules. In MCP-only setups, `set_scope` sets the default.

## D. Local scope: privacy guarantee spec
1. Enforcement lives at the **executor**, not only the policy layer:
   - Cloud clients are not constructed for a local-scoped request.
   - The executor asserts that the target is a loopback Ollama host (127.0.0.1/::1/localhost) and raises if not.
2. Every sub-step stays local: the classifier, verification self-checks, handoff summaries, escalations, `delegate`, `rubber_duck` and image description.
3. Escalation inside local means moving to a larger or better local model, then a larger context, then a structured error. It never moves to the cloud.
4. Prompts and outputs are not written to logs (decision metadata only). The ledger stores token counts only.
5. Catalog updates send no prompt data. `airplane: true` also disables catalog fetch and all other egress.
6. Tests:
   - Network-mock egress test: fail if any non-loopback socket is opened during a local-scoped request (including under failure, escalation and handoff).
   - Fuzz secret-detection test.
   - A lock-precedence golden table.
7. Marketing line: "Headwaters Local never sends a byte of your prompt off your machine, and the tests prove it."
8. **Secret detection** (all scopes): detects API keys, tokens and private keys, ignoring placeholder patterns. Default action `local` (route local or refuse with the reason). Per-request override only via explicit user action.

## E. rules.yaml changes
```yaml
default_scope: auto            # auto | free | local | cloud
strategy: cost_saver           # cost_saver | quality_first | primary_first
private_lock: false
airplane: false
secrets: { action: local }     # local | refuse | off
verify: agentic                # always | agentic | off
picker: { show_extras: false }
local:
  on_battery: prefer_cloud     # prefer_cloud | normal
```
Per-project `.headwaters.yaml`:
```yaml
privacy: local                 # local | free | any   (tighten only)
checks: ["ruff check {files}", "pytest -q -x tests/unit"]
```

## F. Competitive landscape (verify every row before publishing claims)
| Product | Surface | Smart per-prompt routing | Fallback on walls | Local-model selection | Verification | Copilot picker + agent mode | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **llm-router** (OSS) | Hooks + MCP + gateway + SDK; about 15 hosts | Regex heuristic (confident on about half of prompts) + chain | Yes, circuit breakers | Ollama in the chain | Grounding check | No (Copilot = capability extension, no cost-routing) | Mature (about 1.7k commits). Drafts are *advisory* by default. Local models get write/run access by default. Strong secret routing and honest savings reporting. **Main competitor.** |
| AnyModel (VS Code) | Copilot picker | No (fixed order) | Yes | Reads context, keep-alive | No | Yes | Tiny user base |
| Model Router `@router` (VS Code) | Chat participant | Yes (task + complexity) | Yes | Ollama as classifier | No | Not a picker model | Has savings dashboard |
| OAIProvider / Opilot / built-in Ollama BYOK | Copilot picker | No | No | Lists models only | No | Yes | Plumbing only |
| pi-model-router | pi agent | Partial | 402/429/503/529 → `ollama/*` | Wildcard | No | No | Single host |
| opencode-rate-limit-fallback | OpenCode plugin | No | One fallback model | No | No | No | Single host |
| LiteLLM Proxy | Gateway | Basic routing strategies | Yes | Any configured model | No | Via BYOK | Headwaters's transport layer, not a rival product |
| OpenRouter Auto / Free | Cloud router | Yes (cloud) / random free | Provider-level | No | No | Via BYOK | Headwaters uses it as a tier |
| claude-code-router, RouteLLM (OSS) | Proxy / research router | Rules / learned | Varies | Configurable | No | No | Verify current features |

### F1. Positioning: where Headwaters wins
1. **"Headwaters Auto" inside Copilot's picker, working in agent mode**: smart, scoped routing where Copilot users already are.
2. **GPU-aware local intelligence**: VRAM and KV-cache fit, computed `num_ctx`, warm-model preference, sysmem-fallback detection, battery awareness.
3. **Verify-then-escalate with safe defaults**: answers as diffs, no direct writes by local models unless the user opts in.
4. **Guaranteed scopes**: `free` = $0, `local` = provably private (fail-closed, egress-tested), plus the Private lock.
5. **Windows-first product polish**: one-click installer, wizard, tray, doctor, signed builds.
6. **One engine for every IDE**: gateway + MCP + extension share state, walls and savings.

### F2. Where competitors are ahead (be honest; plan answers)
- llm-router: breadth of hosts, its Claude-subscription hook approach, maturity, free and open source. **Answer:** different focus (Copilot/VS Code + local GPU + verification + privacy guarantee). Consider listing interoperability instead of fighting it. Check its license before reading code for ideas; never copy code.

### F3. Store-page copy (draft)
**Tagline:** *One model in your picker that never runs out.*
**Sub:** When Copilot or your API credits hit the wall, Headwaters keeps you coding: best free cloud model, then your own GPU, picked per prompt, checked before you see it.
**Bullets:**
- 🔁 Automatic failover on rate limits and empty credits: free cloud, then local
- 🧠 Picks the right model for the job: code, vision, long context, agent tools
- 🖥️ Knows your GPU: only runs models that actually fit your 12/16 GB card
- ✅ Checks the code before you see it; escalates if a cheap model fumbles
- 🔒 Headwaters Local: 100% on-device, offline, nothing leaves your PC
- 💵 $0 mode, plus a live "saved this month" counter
- 🪟 One-click Windows install; works in VS Code Copilot, Cline, Kilo, Roo, Continue, OpenCode, Claude Code + any MCP host

**FAQ, "Why not a free router?":** "Free routers are great, and Headwaters plays nicely alongside them. Headwaters is built for one thing: keeping you in flow inside your IDE when the credits run out. It gives you a picker model for Copilot agent mode, local routing tuned to your exact GPU, verified output, a privacy lock you can trust, and a one-click Windows setup. Try Headwaters Lite free."

## G. Agent prompt patch (append)
```
ROUTING MODEL: Replace `mode` with scope × strategy × locks (Addendum v3.3 §A–E).
Virtual models: core headwaters/auto, headwaters/free, headwaters/local, headwaters/cloud;
optional headwaters/quality, headwaters/duck; `verify` becomes a setting with optional
model suffixes (+verify, +quality). Implement effective-scope intersection with
precedence private_lock > project rule (.headwaters.yaml, tighten-only) > secret
detection > requested > default. Local scope enforced at executor (loopback-only
assertion, no cloud clients constructed), all sub-steps local, escalation local-only,
fail-closed structured errors with fix hints, no prompt logging. Add `airplane` mode.
Switch surfaces: picker, tray Private Mode (purple lock), VS Code status bar, CLI
scope/private, MCP set_scope/set_private, prompts /headwaters-local|free|auto.
TESTS: egress test (no non-loopback sockets in local scope incl. failure/escalation/
handoff paths), lock-precedence golden table, secret-detection fuzz, battery/GPU-busy
suggestion of headwaters/cloud (never auto-leave local).
```
