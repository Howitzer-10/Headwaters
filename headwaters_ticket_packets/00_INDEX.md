# Headwaters ticket packets: index

Each file is a ready-to-paste session prompt: **Context Pack + one ticket + only the design sections that ticket references** (from its `Refs` field). Work in order and follow each ticket's `Depends` field.

- The full design (all documents) is about **21.4k tokens**. An average Sonnet packet is about **1.8k tokens** (largest 2.7k), including the Context Pack.
- Review-gate tickets (Opus) are the exception: attach the full design plus the phase's code diff.
- If Sonnet asks for a section that isn't included, paste just that section from `headwaters_full_session.md`.
- Re-generate the packets if you edit the design or backlog, so the excerpts stay in sync.
- **Decisions override packets.** Every packet carries a `DECISIONS OVERRIDE PACKET TEXT` block (just above `WORKFLOW FOR THIS TICKET`) that tells the session to read `docs/DECISIONS.md` and `docs/ASSUMPTIONS.md` first; D7 and later win over conflicting packet text, and tickets listed under "Deferred tickets" are not built. **If you regenerate the packets, put this block back into the Context Pack template**, or the protection is lost.
- Decisions made so far that change packet text: D7 (thin litellm-SDK gateway), D8 (`mcp` v2 / `MCPServer`), D9 (roots optional, sampling deferred, elicitation fallback), D10 (core verified retry), D11 (core session stickiness), D12 (`headwaters_think` in core), D13 (Ollama cloud models not routable). HDW-120 to HDW-141 are the Phase 1.5 review fixes from HDW-119; Part 3 of each packet holds its review findings. HDW-142 and later (HDW-144 to HDW-154 are the Phase 2.5 tickets) come from triage sessions (`docs/FOLLOWUPS.md`); their Part 3 names the follow-up.

| Ticket | Title | Model | Design sections included | ≈ k tokens |
| --- | --- | --- | --- | --- |
| HDW-001 | Repo scaffold, tooling, CI | Sonnet | v3.4 §J, v3.5 §F | 1.8 |
| HDW-002 | External API verification spike | Opus | v3 §14, v3.2 §A, v3.4 §B3 | 2.4 |
| HDW-003 | Paths, logging, stdio safety | Sonnet | v3.1 §C4, v3.1 §C5, v3.3 §D | 2.0 |
| HDW-004 | Feature registry and plugin interfaces | Sonnet | v3.5 §B, v3.5 §F | 2.3 |
| HDW-101 | Rules config (`rules.yaml`) and project config (`.headwaters.yaml`) | Sonnet | v3 §8, v3.1 §C1, v3.3 §E | 2.5 |
| HDW-102 | SQLite state layer | Sonnet | v3 §3 | 1.6 |
| HDW-103 | Catalog schema, seed catalog, loader | Sonnet | v3 §4.2, v3 §7.4, v3.4 §C | 2.1 |
| HDW-104 | Catalog updater (signed, ETag, offline) | Sonnet | v3 §4.2, v3.3 §D | 2.0 |
| HDW-105 | Live discovery and catalog merge | Sonnet | v3 §4.2 | 1.7 |
| HDW-106 | Analyzer layer 1 (deterministic requirements) | Sonnet | v3 §4.1 | 1.6 |
| HDW-107 | Analyzer layer 3 (tiny CPU classifier) and layer interface | Sonnet | v3.4 §C | 1.5 |
| HDW-108 | Hardware A: GPU, display, battery, GPU-busy detection | Sonnet | v3 §7, v3.1 §C1 | 2.3 |
| HDW-109 | Hardware B: KV math, fit, `num_ctx`, sysmem-fallback detection | Sonnet | v3 §7.1, v3 §7.2, v3 §7.3, v3.1 §C1 | 2.2 |
| HDW-110 | Wall classifier | Sonnet | v3 §4.4 | 1.7 |
| HDW-111 | Quota tracker and cooldowns | Sonnet | v3 §4.4 | 1.7 |
| HDW-112 | Scope resolver, locks, secret detection | Sonnet | v3.3 §A, v3.3 §B, v3.3 §C, v3.3 §D, v3.3 §E | 2.7 |
| HDW-113 | Policy engine A: filters, floors, scores | Sonnet | v3 §4.5 | 1.8 |
| HDW-114 | Policy engine B: decision ladder and golden tests | Sonnet + Opus | v3 §4.5, v3.3 §B4 | 1.9 |
| HDW-115 | Executor (cloud + local, streaming, reasoning capture) | Sonnet | v3 §4.8, v3.3 §D | 1.9 |
| HDW-116 | Basic verification (core) | Sonnet | v3 §4.6, v3.1 §C4 | 1.7 |
| HDW-117 | Router façade (shared core API) | Sonnet | v3 §2, v3.4 §A | 2.3 |
| HDW-118 | CLI basics | Sonnet | v3 §5.4, v3.3 §C, v3.5 §B | 2.2 |
| HDW-119 | Phase 1 review gate | Opus | Full design + phase diff (attach) | 1.2 |
| HDW-120 | Secret scan covers every message part | Sonnet | v3.3 §D8, F1 | 1.7 |
| HDW-121 | Ollama cloud-proxied models are never local | Opus | v3.3 §D1-D2, A-OLL-8, F2 | 1.8 |
| HDW-122 | Credit walls hit paid models only, and expire | Sonnet | v3 §4.4, F3, F22 | 1.8 |
| HDW-123 | Local scope serves the best local model below the floor | Sonnet | v3 §4.5 step 4, v3.3 §D3, D10, F4 | 1.8 |
| HDW-124 | Complete the import contract | Sonnet | D7, D8, v3.5 §F, F5 | 1.8 |
| HDW-125 | Make subprocess decoding independent of the code page | Sonnet | gap 11, F6 | 1.7 |
| HDW-126 | Production never registers `features/_example` | Sonnet | v3.5 §F, F7 | 1.6 |
| HDW-127 | litellm never writes to stdout and logs through redaction | Sonnet | v3.1 §C5, F8 | 1.6 |
| HDW-128 | Hints honour every active lock | Sonnet | v3.3 §B4, F9 | 1.6 |
| HDW-129 | Escalation and the core verified retry go to a stronger model | Sonnet | v3 §4.6, D10, F10 | 1.7 |
| HDW-130 | A mandatory gate that checked nothing is not a pass | Sonnet | v3 §4.5 step 4, F11 | 1.6 |
| HDW-131 | Live rules in long-running engines | Sonnet | v3.3 §C, F12, F22 | 1.8 |
| HDW-132 | `primary_first` means the primary first | Sonnet | v3.3 §A, F13 | 1.6 |
| HDW-133 | Score terms that do something: offload, complexity | Sonnet | v3 §7.1, F14, F19 | 1.6 |
| HDW-134 | Core session stickiness (D11) | Sonnet | v3 §4.5 session rules, D11, F15, F16 | 1.7 |
| HDW-135 | Remote images are refused, not dropped, for local models | Sonnet | v3.3 §D2, F17 | 1.5 |
| HDW-136 | Unknown context is not "too small" | Sonnet | F18 | 1.5 |
| HDW-137 | Local fix hints name a model | Sonnet | v3.3 §C, F20 | 1.5 |
| HDW-138 | Hard-coded tunables to rules and catalog; startup fail-safe | Sonnet | section 5 of this review, F26, F27 | 1.7 |
| HDW-139 | Seed catalog: classifier and laziness patterns | Sonnet | v3 §4.6, F21 | 1.5 |
| HDW-140 | Core hooks for `headwaters_think` (D12) | Sonnet | v3.4 §B1, D12 | 1.5 |
| HDW-141 | Pin Python 3.13 for development | Sonnet | owner question 2026-10-31, gap 12 | 1.4 |
| HDW-142 | Secret detection sees joined and encoded secrets | Sonnet | v3.3 §D8, FU-120-2 | 1.6 |
| HDW-143 | CI runs on the real default branch and is green on ubuntu | Sonnet | FU-122-6, triage | 1.5 |
| HDW-201 | Shared daemon, internal API, port handling | Sonnet | v3.1 §C3, v3.4 §B3 | 1.8 |
| HDW-202 | stdio MCP shim | Sonnet | v3.1 §C5, v3.4 §B3 | 1.7 |
| HDW-203 | Workspace roots and files-by-path | Sonnet | v3 §5.2.2, v3.1 §C4, v3.4 §D | 1.9 |
| HDW-204 | Front-door tools: `headwaters_do`, `headwaters_ask`, `headwaters_check` | Sonnet | v3.4 §B1, v3.4 §B2 | 1.9 |
| HDW-205 | Front-door tools: `_status`, `_scope`, `_handoff` (basic), `_think` (basic), `_more` | Sonnet | v3.3 §C, v3.4 §B1 | 2.0 |
| HDW-206 | Progress, async jobs, elicitation | Sonnet | v3 §5.2.2, v3.4 §B3 | 1.8 |
| HDW-207 | MCP prompts, command files, rules packs | Sonnet | v3 §5.2.3, v3.3 §C | 1.8 |
| HDW-208 | MCP integration tests and host-eval harness skeleton | Sonnet | v3.4 §B2 | 1.4 |
| HDW-144 | Local GPU-busy check counts Ollama's own memory as another app's | Sonnet | FU-201-2, FU-125-1, triage | 2.0 |
| HDW-145 | Check the daemon, shim and tools on real hosts and a real model | Opus | FU-202-1, FU-204-3, FU-206-3, triage, triage | 2.7 |
| HDW-146 | The shim forwards elicitation and cancellation; jobs can be cancelled | Sonnet | FU-206-1, FU-205-1, FU-202-2, FU-202-3, FU-206-4, FU-206-8, triage | 2.1 |
| HDW-147 | Workspace root rule (`workspace.roots`) | Sonnet | FU-203-2, FU-204-2, triage | 1.7 |
| HDW-148 | Rebuild the engine when rules change | Sonnet | FU-131-1, FU-131-2, triage | 1.8 |
| HDW-149 | Real seed catalog entries and the catalog gaps behind hints | Sonnet | FU-139-1, FU-139-2, FU-140-1, FU-136-1, FU-137-1, FU-204-4, triage | 2.2 |
| HDW-150 | OpenCode free models can be called | Sonnet | FU-201-3, A-OC-3, triage | 1.6 |
| HDW-151 | Move the remaining tunables to rules and catalog | Sonnet | FU-138-1, FU-138-2, FU-138-3, triage | 1.7 |
| HDW-152 | Test and cleanup sweep | Sonnet | FU-125-2, FU-126-1, FU-126-2, FU-127-1, FU-134-2, FU-136-2, FU-140-4, FU-141-2, FU-143-1, FU-143-2, FU-203-6, FU-205-7, FU-205-8, FU-208-3, triage | 2.6 |
| HDW-153 | Remote-model and feature guards | Sonnet | FU-121-4, FU-126-3, FU-135-2, Phase 1 recheck R4, triage | 1.8 |
| HDW-154 | Escalation keeps tool-call strength for agentic requests | Sonnet | FU-129-2, D10, triage | 1.9 |
| HDW-301 | OpenAI-compatible endpoints and virtual models | Sonnet | v3 §5.1, v3.3 §B | 1.9 |
| HDW-302 | Streaming failover, headers, session key | Sonnet | v3 §4.8, v3 §5.1 | 1.7 |
| HDW-303 | Anthropic `/v1/messages` endpoint | Sonnet | v3 §5.1, v3.2 §D | 1.9 |
| HDW-304 | Tools, agentic handling, aliases, pin, suffixes | Sonnet | v3 §5.1, v3.3 §B3 | 1.6 |
| HDW-305 | Gateway integration tests: credits gone → multi-wall chain | Sonnet | v3.2 §F | 1.5 |
| HDW-306 | Privacy egress test suite (local scope proof) | Sonnet + Opus | v3.3 §D | 1.7 |
| HDW-401 | Key management | Sonnet | v3 §11, v3.1 §C7 | 1.9 |
| HDW-402 | `doctor` | Sonnet | v3 §5.3, v3.1 §C1, v3.1 §C2, v3.1 §C3 | 2.2 |
| HDW-403 | Host installer framework + first 4 hosts | Sonnet | v3.1 §C5, v3.1 §C6, v3.2 §D, v3.4 §H | 2.1 |
| HDW-404 | Status page (core, read-only) | Sonnet | v3.5 §B | 1.9 |
| HDW-405 | Windows installer, autostart, itch packaging | Sonnet | v3 §10.2, v3.1 §C7 | 2.1 |
| HDW-406 | Linux service (second target) | Sonnet | v3.1 §D | 1.5 |
| HDW-407 | Beta 0.1 docs and release gate | Sonnet + Opus | Full design + phase diff (attach) | 1.3 |
| HDW-501 | Ledger and savings ★ | Sonnet | v3 §4.9, v3.2 §E3 | 1.5 |
| HDW-502 | Paid scopes, budgets, handback advice ★ | Sonnet | v3 §6, v3.2 §E2, v3.3 §B | 2.1 |
| HDW-503 | Dashboard ★ | Sonnet | v3 §5.3 | 1.4 |
| HDW-504 | Tray app ★ | Sonnet | v3.1 §C7, v3.3 §C | 1.9 |
| HDW-505 | Setup wizard ★ | Sonnet | v3 §5.3 | 1.5 |
| HDW-506 | VS Code extension: model provider ★ | Sonnet | v3.2 §E1 | 1.5 |
| HDW-507 | VS Code extension: status bar, commands, publishing ★ | Sonnet | v3.2 §E1 | 1.5 |
| HDW-508 | Opt-in usage report, feedback, survey ★ | Sonnet | v3.5 §D2, v3.5 §D4 | 1.7 |
| HDW-509 | Beta 0.2 review gate | Opus | Full design + phase diff (attach) | 1.2 |
| HDW-512 | Tool capability truth, live overrides, honest pull hint, gateway footer | Sonnet | HDW-512.md | 1.3 |
| HDW-601 | Repo context engine A: symbol index ★ | Sonnet | v3.4 §D | 1.4 |
| HDW-602 | Context engine B: embeddings and packing ★ | Sonnet | v3.4 §D | 1.5 |
| HDW-603 | Analyzer layer 2: embedding kNN ★ | Sonnet | v3.4 §C | 1.5 |
| HDW-604 | Verification v2 ★ | Sonnet | v3 §4.6, v3.4 §F | 1.6 |
| HDW-605 | Worktree execution ★ | Sonnet | v3.4 §E | 1.5 |
| HDW-606 | Verify-then-escalate loop ★ | Sonnet + Opus | v3 §4.5, v3 §4.6 | 2.0 |
| HDW-607 | Handoff packets and sticky sessions ★ | Sonnet | v3 §4.5, v3 §4.7 | 2.0 |
| HDW-608 | Beta 0.3 review gate | Opus | Full design + phase diff (attach) | 1.2 |
| HDW-701 | Hooks engine framework + Claude Code ★ | Sonnet | v3.4 §G | 1.5 |
| HDW-702 | Hooks for additional hosts ★ (repeat per host) | Sonnet | v3.4 §G | 1.5 |
| HDW-703 | Installers for remaining hosts ★ (repeat per host) | Sonnet | v3 §9, v3.4 §H | 1.7 |
| HDW-704 | SpillBench | Sonnet | v3.4 §I, v3.5 §E1 | 1.8 |
| HDW-705 | Host tool-call eval suite (full) | Sonnet | v3.4 §B2 | 1.4 |
| HDW-706 | Beta 0.4 review gate | Opus | Full design + phase diff (attach) | 1.2 |
| HDW-801 | RC hardening and open-source readiness | Opus | Full design + phase diff (attach) | 1.3 |
| HDW-802 | Decision evidence report | Sonnet + Opus | Full design + phase diff (attach) | 1.3 |
| HDW-803 | Apply the 1.0 decision (conditional) | Sonnet | v3.5 §D3, v3.5 §E2, v3.5 §F | 1.8 |
| HDW-901 | Fusion "deliberate" ★ | Sonnet | v3 §6, v3.4 §B1 | 1.8 |
| HDW-902 | Rubber duck overlay ★ | Sonnet | v3 §4.5, v3.3 §B2 | 1.7 |
| HDW-903 | Image generation tool ★ | Sonnet | v3 §5.2.1 | 1.6 |
| HDW-904 | Learning ★ | Sonnet | v3 §4.9, v3.4 §C | 1.6 |
| HDW-905 | MCP sampling tier ★ | Sonnet | v3.4 §B3 | 1.5 |
