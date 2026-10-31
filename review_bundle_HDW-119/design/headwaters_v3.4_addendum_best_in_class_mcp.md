# Headwaters v3.4 Addendum: Best-in-Class MCP Engine, Hooks Engine, SpillBench

This addendum applies on top of v3–v3.3. Where they conflict, this file wins.
**Decision D5:** Headwaters is one self-contained product (Gateway, MCP and Hooks engines on one core). It has no dependency on, and no bundling with, llm-router or any other router.

## A. Three engines, one core
| Engine | Intercepts | Best for | Edition |
| --- | --- | --- | --- |
| **Gateway** | The host's model calls (OpenAI/Anthropic endpoints, VS Code BYOK, VS Code extension) | Transparent failover, "never runs out" | Pro |
| **MCP** | Work the host model hands off | Copilot/Cursor/any MCP host; cutting host tokens; verification; extras | Lite (core) / Pro (advanced) |
| **Hooks** (new) | The prompt *before* the host model sees it (hosts with lifecycle hooks) | Answering simple prompts with zero host tokens, including subscription hosts | Pro |

All three share SQLite state (walls, sessions, ledger, locks). The effective-scope rules (v3.3 §C) apply equally to every engine.

## B. MCP engine: product requirements

### B1. Front-door tool surface (8 tools; the rest appear through progressive disclosure)
| Tool | Purpose | Annotations |
| --- | --- | --- |
| `headwaters_do` | The main entry. `task`, `paths[]`, `images[]`, `scope?`, `want: answer\|diff\|plan`, `async?`. Returns a verified result. | not read-only; idempotent=false |
| `headwaters_ask` | Q&A / explain / summarize over paths (read-only) | readOnly |
| `headwaters_check` | Verify a diff or code | readOnly |
| `headwaters_think` | Rubber duck / deliberate (Fusion, opt-in) | readOnly |
| `headwaters_status` | Quotas, walls, scope, savings, GPU | readOnly |
| `headwaters_scope` | Set scope / Private lock / strategy | not read-only |
| `headwaters_handoff` | Produce or consume a handoff packet | readOnly |
| `headwaters_more` | Lists and enables advanced tools (image, recommend_model, jobs, pin, feedback…) | readOnly |

- Every tool declares an **`outputSchema`** and returns **structured content** plus a short text rendering.
- **Relay contract:** each result carries `relay: "verbatim" | "apply_diff" | "summarize_ok"`, and the bundled rules files teach host models to honor it.
- Results are **compact**: unified diffs, a token-budgeted summary, and pointers to resources for full detail (`headwaters://result/<id>`).

### B2. Tool-description engineering and host eval
- Descriptions are short, imperative and example-led, tuned so weak or free dispatcher models call them correctly.
- **Host-model eval suite:** for each (host, dispatcher model), measure the tool-selection rate, argument validity and relay compliance on 100+ scripted tasks. Ship in CI, and publish results in SpillBench.

### B3. MCP spec feature map (verify each against the current spec version and per-host support)
| Feature | Use |
| --- | --- |
| Structured output / outputSchema | All tools |
| Tool annotations | readOnly / destructive / idempotent hints so hosts can auto-approve safe tools |
| Progress notifications | Cold loads, drafting, escalation |
| Async tasks (spec Tasks primitive if available; else job_id pattern) | Long runs, Fusion |
| Elicitation | Spend confirmation, scope loosening (user-only) |
| Resources (+ subscriptions) | `headwaters://status`, `headwaters://savings`, `headwaters://result/<id>`, `headwaters://handoff/<id>` |
| Prompts | `/headwaters-local`, `/headwaters-free`, `/headwaters-auto`, `/headwaters-duck`, `/headwaters-handoff` |
| Roots | Workspace boundary for path reads |
| Sampling | Optional: let the host model serve as a tier when the host allows it (never in local scope) |
| Transports | stdio (default) + streamable HTTP (shared daemon so multiple IDEs reuse one warm GPU model) |

**Shared daemon:** stdio MCP processes are thin shims to one Headwaters daemon, so three IDEs open at once don't each spawn a router or compete for VRAM.

## C. Analyzer v2: three-layer classifier
1. **Rules layer** (under 1 ms): hard requirements + strong signals.
2. **Embedding kNN layer** (about 10–30 ms, CPU): a local embedding model run through Ollama with `num_gpu=0`, matched against a labeled prompt bank (shipped in the catalog and growing from the user's own feedback). Returns task, complexity and confidence from neighbor agreement.
3. **Tiny LLM layer** (CPU, 2 s budget): runs only when kNN confidence is below the threshold.
- **Calibration:** confidence is calibrated on a held-out set. Target **≥90% of prompts classified with confidence**, with measured accuracy published in SpillBench.
- **Learning:** verification outcomes and thumbs feed the user's local bank (privacy: stays local; opt-in export of anonymized labels only).

## D. Repo Context Engine (what makes local models good)
- Incremental local index: tree-sitter symbols (defs, refs, imports), file summaries, and code embeddings (CPU), stored in SQLite + a vector table (e.g. sqlite-vec).
- **Context packing to the chosen model's computed `num_ctx`:** target files → referenced symbols → callers/callees → tests → docs, highest-value first, with token accounting.
- Reads paths itself, so host tokens are not spent on file contents.
- File watcher plus git-aware invalidation. Respects `.gitignore` and `.headwatersignore` (secrets and env files are excluded by default).

## E. Safe execution
- **Default: diffs only.** Local or free models never write files directly.
- **Optional worktree mode** (`exec: worktree`):
  - Changes are applied in a throwaway `git worktree`.
  - Project checks run there under an allowlist, timeouts, and no network for the subprocess where the OS allows (Windows: Job Object limits + firewall rule for the runner, or documented best effort).
  - Only diffs that pass verification are returned. The host or user applies them.
- Destructive commands (force push, recursive delete outside the worktree, credential access) are hard-denied.

## F. Verification v2
Grounding (every symbol or file referenced must exist in the index or the diff) → tree-sitter parse → placeholder scan → `git apply --check` → project checks in the worktree → JSON/tool-schema checks. Each failure carries a machine-readable reason into the escalation prompt.

## G. Hooks engine (Pro)
- `headwaters install --host <x>` writes the host's hook config where the host supports lifecycle hooks. Verify current hook APIs per host (e.g. Claude Code, Cursor, Codex CLI, Gemini CLI, Copilot CLI, Windsurf).
- Pre-prompt hook:
  1. Classify the prompt.
  2. If it is simple, read-only and in scope, answer it with Headwaters and either short-circuit the host turn or inject the verified answer as context. Which one depends on what the host allows.
  3. Otherwise pass through, adding a compact context pack.
- Never block the host on hook failure (fail-open for availability). For privacy locks, fail closed **only** when the user enabled "strict local".

## H. Multi-host installer and doctor
- `headwaters install --host vscode|copilot|cline|kilo|roo|continue|opencode|claude-code|cursor|trae|windsurf|claude-desktop|all`. For each host it writes:
  - the MCP entry (absolute launcher path)
  - the gateway endpoint / BYOK config
  - the rules pack
  - hooks, if supported
  - slash command files
- It shows a diff and asks for consent before changing any host config, and keeps a backup for `headwaters uninstall --host`.
- `headwaters doctor --host <x>` runs a live round trip, a tool-call test and a scope test, then prints a fix list.

## I. SpillBench (proof, not claims)
- A public, reproducible harness (it could be open-source even if Pro stays closed), with four suites:
  1. **Routing accuracy** vs. labeled prompts
  2. **Quality vs. paid baseline** on coding tasks (pass@1 with tests, verified-diff rate)
  3. **Savings** at equal quality (counterfactual $ and host tokens avoided)
  4. **Host tool-call reliability** per dispatcher model
- Runs on reference hardware profiles (12 GB laptop, 16 GB desktop) and publishes methodology, n and dates.
- Consider submitting to public router benchmarks (e.g. RouterArena) for external credibility.
- The store page links to SpillBench results. No unverified percentage claims.

## J. Clean-room policy
- Don't read competitor source code during design or implementation. Public READMEs and feature lists are allowed as market input.
- Keep `docs/PROVENANCE.md` recording where each major idea came from (own design, public docs, specs).
- Use original names, tool names, file layouts and docs.
- Check the licenses of all dependencies. Only use permissive ones in Pro (MIT/Apache/BSD); no copyleft code in the closed package.

## K. Updated roadmap
| Milestone | Scope | Release |
| --- | --- | --- |
| M1 | Core: catalog, analyzer layers 1+3, hardware, walls, scopes/locks, executor, state | – |
| M2 | MCP: 8 front-door tools, structured output, relay contract, progress/async, shared daemon, basic verify, installer for 4 hosts (VS Code/Copilot, Cline, OpenCode, Claude Desktop) | **Lite 0.1** |
| M3 | Gateway + VS Code extension (picker models) + ledger/dashboard | **Pro Early Access** |
| M4 | Analyzer layer 2 (kNN) + Repo Context Engine + Verification v2 + worktree exec | Pro 1.0 |
| M5 | Hooks engine + installer for all hosts + SpillBench public | Pro 1.x |
| M6 | Fusion/duck/image extras, sampling tier, learning export | Pro 1.x |

Win order: differentiate first (GPU-aware local, verify-then-escalate, Copilot picker, provable privacy), then reach parity in breadth.

## L. Agent prompt patch (append)
```
PRODUCT: Headwaters is fully self-contained; no dependency on other routers. Implement
three engines (gateway, MCP, hooks) over one core with shared state and scope rules.
MCP: 8 front-door tools (headwaters_do, _ask, _check, _think, _status, _scope, _handoff,
_more) with outputSchema + structured content, tool annotations, relay contract
(verbatim|apply_diff|summarize_ok), compact results + resources (headwaters://...),
progress, async (spec Tasks if available else job_id), elicitation, prompts, roots,
optional sampling (never in local scope), stdio shims → shared streamable-HTTP daemon.
Host-model tool-call eval suite in CI.
ANALYZER v2: rules → CPU embedding kNN over labeled bank → tiny CPU LLM; calibrated
confidence; target ≥90% confident coverage; local learning from verify/thumbs.
CONTEXT ENGINE: tree-sitter symbol index + CPU embeddings (sqlite-vec), git-aware
incremental updates, ignore files, context packing to computed num_ctx.
EXECUTION: diffs by default; optional git-worktree exec with allowlist, timeouts,
no-network best effort, hard-deny destructive ops.
VERIFY v2: grounding → tree-sitter → placeholders → git apply --check → project checks
in worktree → schema; machine-readable failure reasons feed escalation.
HOOKS: per-host installers where lifecycle hooks exist (verify APIs); pre-prompt
classify → answer/short-circuit or inject context; fail-open except strict-local.
INSTALLER: headwaters install/uninstall --host ... with consent diff + backup; doctor
--host live checks.
SPILLBENCH: reproducible routing/quality/savings/tool-call suites on reference GPU
profiles; publish methodology.
CLEAN ROOM: do not read or copy competitor source code; maintain docs/PROVENANCE.md;
permissive dependencies only in closed packages.
```
