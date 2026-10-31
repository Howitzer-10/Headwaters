# Headwaters v3.2 Addendum: GitHub Copilot Playbook and Engine Responsibilities

This addendum applies on top of the v3 design and the v3.1 addendum. Where they conflict, this file wins.

## A. Facts this design relies on (verified Oct 2026; re-check before release)
- Copilot uses token-metered **AI Credits** (since June 1, 2026). **There is no fallback model** when credits run out. Chat, agent mode and CLI stop. Inline completions and Next Edit Suggestions keep working on paid plans.
- No zero-credit model remains in Copilot. Every model is metered.
- VS Code Copilot Chat supports **BYOK**:
  - Built-in providers: OpenRouter, Ollama, and others.
  - Custom OpenAI-compatible endpoints.
  - Works in Chat and Agent mode.
  - No Copilot plan is needed.
  - Business and Enterprise orgs can disable BYOK by policy.
  - BYOK models appear in the Agent Host window only when `chat.agentHost.byokModels.enabled` is true (experimental).
- The VS Code `LanguageModelChatProvider` API lets an extension register models directly in the Copilot Chat picker.

## B. Hard rules
- Never proxy, reuse or reverse-engineer Copilot credentials or the Copilot API, and never call Copilot models from Headwaters. This violates GitHub's terms.
- Copilot models are outside Headwaters's control. Headwaters only takes over traffic the user sends to a Headwaters model.

## C. Engine responsibilities
| Responsibility | Gateway | MCP |
| --- | --- | --- |
| Be the host's chat/agent model (`headwaters/*` virtual models) | ✅ | – |
| Detect walls on the main model and fail over transparently (before first token) | ✅ | – |
| Per-request routing, sessions, handoff on switch | ✅ | ✅ (for its own calls) |
| Delegate subtasks while the host runs a paid or subscription model | – | ✅ |
| Verify and escalate | ✅ (`headwaters/verified`) | ✅ |
| Fusion, rubber duck, images, prompts/commands, recommend_model | – | ✅ |
| Hosts with no custom endpoint | – | ✅ |
| Keeps working after the host's subscription runs out | ✅ | Only if the host model is the gateway or local Ollama |

**Rule of thumb:** the gateway keeps you coding; MCP makes the coding cheaper and better.

## D. Copilot playbook (wizard and docs)
1. **Setup:** add Headwaters as a BYOK custom OpenAI-compatible endpoint (or install the Headwaters VS Code extension, see E).
   - Enable tool calling.
   - Set `chat.agentHost.byokModels.enabled`.
   - Register the MCP server in VS Code.
   - Detect a Business/Enterprise BYOK policy block and explain it.
2. **Saver mode (credits remaining):** the user's default chat model is `headwaters/auto`.
   - Headwaters routes to local and free models.
   - Tasks above the cheap tiers' quality go to the user's own paid keys, if configured. Otherwise Headwaters returns **handback advice** (see E2).
3. **Wall mode (credits exhausted):** the user switches the picker to `headwaters/auto` once. From then on all failover is automatic: free cloud, then local, then re-routing on every later wall.
4. **Hybrid:** the user stays on Copilot models and lets MCP `delegate` handle cheap subtasks. This saves credits but stops when credits run out, so recommend switching to step 3 at that point.
5. **Docs note:** after Copilot credits run out, inline completions still work through Copilot. Headwaters handles chat and agent mode only. Optionally offer Continue with a small local autocomplete model for Copilot Free users once completions are capped.

## E. New components
### E1. Headwaters VS Code extension (Pro)
- Implements `LanguageModelChatProvider` and registers "Headwaters Auto / Local / Quality / Verified / Duck".
- Acts as a thin client to the gateway (no duplicated routing logic) and auto-discovers the gateway port.
- Declares per-model capabilities (tools, vision, context window) from the gateway's current decision space.
- Status bar: current tier and model, wall state, savings counter. Click opens the dashboard.
- Commands: set mode, pin model, open handoff, run doctor.
- Publish on the VS Code Marketplace and Open VSX. The marketplace listing doubles as a funnel to itch.io.
- Differentiators from existing BYOK-fallback extensions: quality-ranked routing, GPU fit, verification, the ledger, and one engine across every IDE.

### E2. Handback advice
- Triggers when the best available model is below the quality floor AND no paid key tier exists AND the user has declared a subscription host (`hosts.subscription: copilot | cursor | claude_app | none` in rules.yaml).
- Action: answer with the best cheap model (verification mandatory), prefixed by a one-line advisory along the lines of: `⚠ Headwaters: this task likely needs a frontier model. Consider switching to Copilot Auto. Handoff: /headwaters-handoff`. Also store a handoff packet.
- Configurable: `advice: off | inline | header_only`.

### E3. Ledger addition
- Optional "subscription credits avoided": estimates the Copilot credits a request would have used, using the user-entered model rate from GitHub's published pricing (kept in the catalog and dated). Label it clearly as an estimate.

## F. Agent prompt patch (append)
```
COPILOT SUPPORT: Never proxy or reuse Copilot credentials or call Copilot models. Support
VS Code Copilot Chat via BYOK (custom OpenAI-compatible endpoint → gateway) and via a
Pro VS Code extension implementing LanguageModelChatProvider as a thin client to the
gateway (capabilities per virtual model, status bar with tier/model/savings, commands
for mode/pin/handoff/doctor). Wizard: configure BYOK endpoint with tool calling, set
chat.agentHost.byokModels.enabled, register MCP, detect org BYOK policy blocks.
Implement handback advice (rules.yaml hosts.subscription, advice mode) and the optional
"subscription credits avoided" ledger estimate from dated catalog rates.
Integration test: simulate host-credits-exhausted → user selects headwaters/auto → verify
multi-wall failover (paid key 402 → free 429 → local) completes an agent-mode tool-call
loop.
```
