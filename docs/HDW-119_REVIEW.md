# HDW-119 Phase 1 review gate: report

Reviewer: Opus, effort High, 2026-10-31. Line references cite the bundle snapshot
(`code/*.md`). Claims marked **[probe]** were re-checked against the live tree with throwaway
scripts in `%TEMP%`; nothing in the repo was changed.

## 0. Bundle inventory

Present (35 files): `00_README.md`, `01_PROMPT.md`, `02_BRIEF.md`, `03_KNOWN_GAPS.md`;
`code/{core_A_safety_critical, core_B_supporting, surfaces_and_config, tests_priority, tests_other}.md`;
`decisions/{ASSUMPTIONS, DECISIONS, PROVENANCE}.md`; `design/` (v3, v3.1 to v3.5, backlog);
`evidence/` (9 files); `tickets/{00_INDEX.md, HDW-119.md}`.
Missing: nothing the README lists. `headwaters_full_session.md` is intentionally absent (gap 3).

---

## 1. Verdict

**Pass with required fixes.**

The fail-closed core is structurally sound. In local scope the policy filters out cloud tiers, the
executor re-checks the tier and asserts loopback before any backend exists, litellm is never even
imported (**[probe]** P6), and every failure ends in a structured `ScopeError`, `NoRouteError` or
`RouteFailed`, never a cloud fallback. Seven defects still break privacy or routing promises that
Phase 2 builds on directly: the secret lock misses tool-call payloads, Ollama's cloud-proxied
models can pass as "local", one OpenRouter 402 shuts off every free OpenRouter model indefinitely,
`headwaters/local` refuses ordinary agent-mode requests, the import contract does not confine
litellm, the gate's own contract test fails on this machine, and the example ★ plugin runs in
production. None needs a redesign. Each is a small, testable ticket, so Phase 2 can start as
soon as the seven blockers below are merged.

**Blockers (merge before HDW-201):**

| # | Ticket | Finding |
| --- | --- | --- |
| 1 | HDW-120 Secret scan covers tool-call arguments and `tool_use` input | F1 |
| 2 | HDW-121 Ollama cloud-proxied models are never `local` | F2 |
| 3 | HDW-122 Credit walls hit paid models only, and expire | F3 |
| 4 | HDW-123 Local scope serves the best local model below the floor, with verification, instead of erroring | F4 |
| 5 | HDW-124 Complete the import contract (litellm, mcp SDK, cli, surfaces) | F5 |
| 6 | HDW-125 Make the import-contract test independent of the console code page | F6 |
| 7 | HDW-126 Production never registers `features/_example` | F7 |

The other tickets in section 8 are tied to the Phase 2 ticket they must precede (HDW-127 before
HDW-202, HDW-131 with HDW-201, and so on), or can wait.

---

## 2. What I read and what I could not check

**Read in full:** `01_PROMPT`, `00_README`, `02_BRIEF`, `03_KNOWN_GAPS`, `decisions/DECISIONS.md`,
`decisions/PROVENANCE.md`; design v3.3 and v3.5 in full; v3 (sections 0 to 8 and 12 to 14; 4.5
and 7 closely), v3.1, v3.2 and v3.4 in full; backlog sections 1 and 2, Phases 0 to 3, and
tickets 501, 502, 604 to 607 and 901 to 905; all 9 `evidence/` files;
`code/core_A_safety_critical.md` (lines 1 to 5302, all 40 files); `code/core_B_supporting.md`
(lines 1 to 4711, all 33 files); `code/surfaces_and_config.md` (lines 1 to 1686, all 19 files);
`code/tests_priority.md` (29 of 30 files in full; for `test_walls.py` the helpers and the full
list of test names and assertions on kind/scope); `tickets/HDW-119.md`.

**Skimmed or searched:** `decisions/ASSUMPTIONS.md` (searched per topic, with the Phase 1 rows
171 to 182 and the A-OLL/A-OR rows read); `code/tests_other.md` (searched for specific claims:
the catalog-updater socket mock, the `_example` discovery tests, analyzer and logging tests).
Not read: backlog Phases 4 to 8 beyond the tickets named above; `tickets/00_INDEX.md`.

**Re-run on the live tree (2026-10-31):**
- `pytest --cov`: **5 failed, 877 passed, 1 skipped**. The 5 failures are all
  `test_import_contract.py::test_deliberate_violation_fails[*]`: a `UnicodeDecodeError` (cp1252)
  in a pytest reader thread turns `result.stdout` into `None`. Gap 11 calls this a one-off flake;
  here it failed 5 of 5, so it depends on the console code page, not on chance (F6). Coverage
  97.32 % reproduces exactly.
- `lint-imports`: 1 kept, 0 broken (reproduces).
- Probes P1 to P9, quoted in the findings.

**Could not verify, and why:**
- Anything against real Ollama, NVIDIA or providers (gap 7): KV-fit accuracy, the sysmem
  heuristic, wall regexes against live bodies, and the `/api/tags` fields of Ollama cloud models
  (F2 relies on Ollama's docs and `-cloud` naming, not a live listing).
- The Linux CI run (gap 6).
- Whether litellm 1.106.6 prints anything at import or on the first call apart from the
  exception path I found (F8); I read one code path, not the whole package.
- Hot-reload behaviour of a long-running Router, because no daemon exists yet (F12 is a design risk).

---

## 3. Fail-closed analysis

Every path in core that can open a non-loopback connection or hand data to a process that might.
"Reachable in local scope" means: while the effective tiers are `{local}` (picker
`headwaters/local`, Private lock, project `privacy: local`, secret lock or airplane mode).

| # | `file:function` | Reachable in local scope | Guard that stops it |
| --- | --- | --- | --- |
| 1 | `executor/cloud.py:LiteLLMBackend.stream` (and the lazy `_load_litellm`) | **No** | (a) `policy/filters.py:_first_failure` drops non-local tiers (A 2594); (b) `executor/guard.py:enforce_scope` raises `LocalScopeViolation` before `_cloud_factory` runs (A 1316, 1457); (c) litellm is imported only inside the backend (P6: not in `sys.modules`) |
| 2 | `executor/ollama.py:OllamaBackend.stream` | Yes (intended) | `assert_loopback_url` in the constructor and in `enforce_scope` (A 1429-1450, 1463-1467); `httpx` with `trust_env=False`. **Gap:** loopback is the wrong test when Ollama itself forwards `*-cloud` models to ollama.com (F2): **none** |
| 3 | `executor/ollama_convert.py:_text_and_images` | Yes | Remote `image_url`s are dropped, never fetched (A 1567-1587). Correct for egress, but silent (F17) |
| 4 | `analyzer/ollama_client.py:OllamaHttpClient.chat` (layer 3 classifier) | Yes, every scope | `assert_loopback` in the constructor, `ProxyHandler({})` (A 81-99). Same gap as #2 if the catalog's classifier model is a cloud tag (F2) |
| 5 | `catalog/sources.py:ollama_models` (discovery) | Yes (startup, TTL) | `is_loopback_url` (A 594); no prompt data |
| 6 | `catalog/sources.py:openrouter_models`, `openai_compatible_models` | Only if the default state is not local | `discovery._network_block`: airplane, Private lock or `default_scope: local` (A 311-316). A per-request local scope does not stop a scheduled refresh, which is fine: no prompt data is sent. It does send `OPENROUTER_API_KEY` to a public endpoint (nit, F24) |
| 7 | `catalog/updater.py:default_fetch` | Only if not airplane (and `disable_when_local` is false by default) | `blocked_reason` (B 1472-1482); https only; signature verified. No prompt data. It uses system proxies and follows redirects; integrity holds because of the signature |
| 8 | `quotas.py:QuotaTracker.poll_key` | No caller yet | `airplane` / `local_lock` arguments supplied by the caller (B 2846). Phase 2 must pass them (F12) |
| 9 | `verify_diff.py:_git` (`git apply --check`), `hardware/gpu.py:run_nvidia_smi` | Yes | Local subprocesses with a fixed argv; no network |
| 10 | Plugins (`LedgerPlugin.record`, `VerifierPlugin.verify`, `features.discover_*`) | Yes | **None by design.** In-process ★ code runs with full rights; the ledger only gets metadata (A 3743-3760), but a verifier receives the answer text. That is a trust boundary to document, not a defect |
| 11 | **Secret-lock bypass:** secret only in tool-call arguments or `tool_use.input` | The request is *not* narrowed to local, so cloud tiers stay open | **None** (F1, P1) |
| 12 | **Lock toggled while an engine runs** (`headwaters private on`) | A running Router keeps its old `Rules` | **None** today; there is no daemon yet (F12) |

**Conclusion:** rows 1 to 10 hold. Rows 2, 4, 11 and 12 are the gaps; 2/4 and 11 block Phase 2, and
12 must land with HDW-201.

---

## 4. Findings

Where: A = `code/core_A_safety_critical.md`, B = `code/core_B_supporting.md`,
S = `code/surfaces_and_config.md`, T = `code/tests_priority.md`, with line numbers.

| ID | Severity | Kind | Where | Finding | Evidence | Suggested fix |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | blocker | defect | `router_prep.py:scan_texts` (A 3862-3866), `router_session.py:text_of` (A 3961-3975) | The secret lock scans only message `content` text. A key inside OpenAI `tool_calls[].function.arguments` or Anthropic `tool_use.input` is never seen, so the request is not narrowed to local and goes to cloud. Agent loops (the core 0.1 use case) carry file contents and shell output exactly there. | **[probe]** P1: user text detected; tool_result detected; `tool_calls.arguments` **not** detected; `tool_use.input` **not** detected | HDW-120: scan every string leaf of every message (arguments, `input`, `tool_result`, system blocks), reusing `_extract.collect`. |
| F2 | blocker | defect | `catalog/sources.py:ollama_models` (A 592-641); `executor/guard.py:enforce_scope` (A 1453-1467) | The local Ollama server forwards cloud models (`gemma4:cloud`, `*-cloud` tags) to ollama.com. Discovery stores them as tier `local` with `retains_data=False`, and the loopback assertion passes because the hop to the cloud happens inside Ollama. A `headwaters/local` or Private-lock request can therefore leave the machine. | Ollama docs (2026-10-31): "In the Ollama app or CLI, use `gemma4:cloud`"; FAQ: local-only mode needs `OLLAMA_NO_CLOUD=1` / `disable_ollama_cloud`. **[probe]** P7: `gpt-oss:120b-cloud` in `/api/tags` becomes `tier=local retains_data=False` | HDW-121: classify remote models (remote fields in `/api/tags`/`/api/show`, plus catalog name patterns) as not local; the executor refuses them; `doctor` checks `OLLAMA_NO_CLOUD`. |
| F3 | blocker | defect | seed `wall_patterns` (S 1493, 1503); `quotas.py:_subject`, `record_wall` (B 2686-2689, 2712-2715); router passes no key (A 3387) | One OpenRouter 402 on a paid model writes `exhausted` on subject `provider:openrouter` with `until=None`. That blocks **every** OpenRouter model, `:free` included, and nothing lifts it: `poll_key` has no caller, and `record_success` keeps exhausted walls by design. The generic 402 pattern says scope `key`, but it falls back to the provider subject because no key is ever passed. Free scope then permanently loses its main tier. | **[probe]** P9: `exhausted provider:openrouter until None`; `availability("openrouter/meta/llama:free")` returns `exhausted, eta None` | HDW-122: credit walls block paid subjects only; an exhausted wall with no reset expires after `walls.exhausted_recheck_s`; the router passes the key alias. |
| F4 | blocker | defect | `policy/ladder.py:build_ladder` (A 2736-2741); `policy/decide.py` (A 2436-2440); analyzer `_complexity` (B 701-704) | Local scope never serves a local model below the floor; it raises `local_below_floor`. Agentic requests are counted twice (+1 complexity in the analyzer, +15 on the floor), so a trivial agent turn gets floor 95 on a 0-100 scale. `headwaters/local` therefore refuses ordinary agent-mode work that `headwaters/free` serves from the *same* local model. This contradicts v3 section 4.5 step 4 (best-effort cheap, verification mandatory), v3.3 D3 (escalate locally, *then* error) and goal G4. | **[probe]** P2: tools request, local model code 88 / agentic 85: `scope=local` raises `local_below_floor`; `scope=free` serves the same model, rung `best_effort`, floor 95 | HDW-123 (D10): in local and battery-local_only states, append the below-floor local entries with `verify_required`; drop the analyzer's agentic +1. |
| F5 | blocker | gap | `.importlinter` (S 1319-1337) | The contract holds but is incomplete: (a) `litellm` is not confined to `core/executor/cloud.py` (D7). External packages are excluded, and the `importlib` import would be invisible to the linter anyway; (b) the `mcp` SDK is not confined to `headwaters/mcp` (D8); (c) core may import `headwaters.cli`. Today it holds only by convention. | Config text; every litellm hit in `mechanical_checks.md` is inside `executor/`; **[probe]** P6: litellm not loaded after importing core and the CLI | HDW-124: forbid `headwaters.cli`; add external contracts for `litellm` and `mcp` (`include_external_packages = True`); add a source test that fails on any `litellm` reference outside `cloud.py`/`cloud_map.py`. |
| F6 | blocker | defect | `tests/test_import_contract.py:_lint_imports` (T 1454-1459); `hardware/gpu.py:run_nvidia_smi` (B 1919-1921) | `subprocess.run(text=True)` without an `encoding` decodes child output as cp1252. On this machine all 5 deliberate-violation tests fail, so the one test proving the contract can fail is red. In `run_nvidia_smi` the same pattern raises an uncaught `UnicodeDecodeError` on a non-ASCII process name; `HardwareMonitor` swallows it, and detection silently drops to manual. | Re-run 2026-10-31: 5 failed (`TypeError: ... 'NoneType'`, with `UnicodeDecodeError ... cp1252` in reader threads). Gap 11 calls it a one-off; it is deterministic | HDW-125: `encoding="utf-8", errors="replace"` in both places; set `PYTHONIOENCODING=utf-8` for the child process. |
| F7 | blocker | defect | `features/_example` (S 1175-1217); `cli/runtime.py:_registry` (S 1080-1084); `router._verify` (A 3465) | Production discovery registers the dummy ★ feature. Its `ExampleVerifier` fails every answer with empty `content`, which is every tool-call-only turn, and `verify` defaults to `agentic`, so every agent tool step is reported `verify_failed` (and would cascade with an `EscalationPlugin`). `ExampleLedger` keeps every event in memory for the life of the process. | **[probe]** P8: `discover_features` returns `['headwaters.features._example']`; a tool-only answer is `failed ('empty answer',)` | HDW-126: move the example to `tests/fixtures`, or skip `_`-prefixed packages outside tests. |
| F8 | high | defect | `executor/cloud.py:_load_litellm` (A 921-926) | litellm prints to stdout (`print()` at `litellm_core_utils/exception_mapping_utils.py:2386-2394`, guarded by `suppress_debug_info`, default `False`). On the MCP stdio shim that corrupts JSON-RPC; under `guard_stdout(strict=False)` it is diverted to stderr unredacted. litellm's own `logging` loggers do not pass through `RedactionFilter`. | Installed `litellm 1.106.6` source; `_load_litellm` sets only `telemetry` | HDW-127 (before HDW-202): set `suppress_debug_info=True`, `set_verbose=False`; attach `RedactionFilter` to the `LiteLLM*` loggers; add a test with a patched module. |
| F9 | high | defect | `policy/ladder.py:suggestions_for`, `no_route` (A 2666, 2703); `scope.py:_narrow` (A 4814-4826) | The "use headwaters/cloud" hint is suppressed only when `scope.locks` is non-empty, but `locks` records a lock only when it *narrowed* the request. Private lock plus requested `local`, or Private lock plus `default_scope: local`, gives `locks == ()`, so the router suggests cloud while Private Mode is on. That breaks the v3.3 B4 / golden `private-lock-battery-no-cloud-hint` intent. The golden test only passes because it injects `locks` by hand. | **[probe]** P3: `locks: ()`, suggestion "pick headwaters/cloud" | HDW-128: add `ScopeDecision.active_locks` (every lock in force, narrowing or not); key the hints on it. |
| F10 | high | defect | `router.py:_escalate` and `_redecide` (A 3473-3495, 3424-3443) | Escalation re-runs `decide` without the failed model, so it moves to the *next-ranked* model. That is usually weaker (rank order), not "one rung up" (v3 4.6). The optimistic rung can fire again on the redecide, so a failed cheap model can be replaced by another below-floor cheap model. In local scope this is the only escalation path. | Code reading; `test_verify_failure_escalates_when_a_plugin_is_registered` escalates FREE 90 to LOCAL 84, a *lower* score | HDW-129: on escalation, require candidates whose task score is above the failed model's, and set `escalation=None` for the redecide. |
| F11 | high | defect | `router_verify.py:run_verifiers` (A 4259-4278); `verify_types.Collector.report` (B 4416-4426) | A **mandatory** gate (`verify_required`, i.e. below floor or optimistic) passes silently when nothing could be checked: `ran == 0` gives `passed=None`, not failed; a prose answer gives `passed=True` with zero checks; parse checks skipped for a missing grammar count as passed. Below-floor answers are therefore delivered unflagged as "verified". | **[probe]** P8: prose answer `ran=True passed=True problems=()` | HDW-130: `VerifyOutcome.checked` (count of checks that ran); when `verify_required` and `checked == 0`, report `verify_unchecked` and keep `below_floor`; never call it verified. |
| F12 | high | risk | `Router.__init__`, `Executor.__init__` (A 3160-3188, 1239-1258); `rules_watch.RulesManager` (B 3043-3098) | Router, Executor, QuotaTracker and HardwareMonitor take a `Rules` *snapshot*. `RulesManager` reloads, but nothing hands the new rules to them. In the Phase 2 daemon, `headwaters private on` (which edits `rules.yaml`, S 542-578) would not reach a running router until restart: the Private lock would be silently not in force. `enforce_scope` also does not re-check `rules.private_lock`. | Code reading; no `set_rules` anywhere (searched the live tree) | HDW-131 (with HDW-201): a rules provider (`Callable[[], Rules]`) read per request in `_select` and `enforce_scope`; add a test that toggles the lock between two requests on one Router. |
| F13 | high | gap | `policy/ladder.py:build_ladder` (A 2728-2729) | `primary_first` puts only primaries that *meet the floor* in front, while `primary_bonus` lifts their score. A primary below the floor is not first, which contradicts v3.3 A (`primary_first` was `paid_only_until_wall`: use the paid primary until it walls). | Golden `primary-first` passes only because paid1 is 95 | HDW-132: under `primary_first`, put every usable primary first (flag `below_floor`); add a golden row with a primary under the floor. |
| F14 | medium | defect | `scoring.py:score_candidate` (A 2855-2893); `filters.py` (A 2598-2602) | `offload` (weights spill to system RAM, slow) is admitted but carries **no** penalty. v3 7.1 says "offload (slow, large latency penalty)". A 15 GB model on a 10.5 GB budget scores like one that fits. | **[probe]** P4: fits total 90.0, offload total 90.0 | HDW-133: `scoring.offload_penalty` in rules, applied when `fit.status == "offload"`. |
| F15 | medium | defect | `router_prep.py:with_warm_preference` (A 3869-3885) | A session's previous model gets the full warm bonus even when it is a cloud model (warmth is a VRAM concept) or a cold local model. For a cold local model its eviction penalty is also *replaced* with 0, so session memory overrides real residency. | **[probe]** P5: cloud model +15.0 warm points; cold local evict 25.0 becomes 0.0 | HDW-134: separate `session_bonus` (rules, small, any tier) from residency; never clear `eviction_penalty`. |
| F16 | medium | defect | `router.py:_remember` (A 3595-3603) and `_finish` | The session is pinned to the answering model even when its answer **failed** verification, so the next turn prefers a model known to have just failed. | Code reading | HDW-134 (same ticket): do not pin on `verify_failed`. |
| F17 | medium | gap | `executor/ollama_convert.py:_text_and_images` (A 1567-1587) | Remote image URLs are dropped silently for local models; the model answers "about" an image it never saw, and the analyzer still marks `vision`. | Code reading | HDW-135: refuse with a structured `image_url_not_local` error (or fetch it only outside local scope, in the engine). |
| F18 | medium | defect | `filters.py:_first_failure` (A 2611-2613) | `ctx is None` gives `ctx_too_small`, so models of unknown context are excluded with a misleading reason and suggestion (`split_task`). Merge fills `ctx` from `unknown_model_defaults`, so this mostly hits OpenAI-compatible endpoints that report no context. | Code reading; `test_filter_context_needs_headroom` asserts the current behaviour | HDW-136: `ctx_unknown` reason; treat it as `unknown_model_defaults.ctx`. |
| F19 | medium | risk | `scoring.py` complexity penalty (A 2867) | `complexity_penalty` is the same for every candidate, so it never changes a ranking; only the floor responds to complexity. It is a dead term with a rules key. | Code reading; `test_score_complexity_penalty` checks the arithmetic only | Document it or make it model-dependent (scale by `1 - quality/100`). Fold into HDW-133. |
| F20 | medium | gap | `router.py:_select` (A 3321-3331) | The router never passes `local_fix_hint`, so `local_unavailable` always says "Start Ollama and pull a model", never the model the catalog recommends for this GPU profile (v3.3 C: "pull qwen2.5vl:7b"). | Code reading | HDW-137: build the hint from `catalog.gpu_profiles[hw.profile].recommended`. |
| F21 | medium | defect | seed `gpu_profiles.laptop_12gb.recommended.classifier` (S 1520) | The classifier recommended for 12 GB is the **vision** model (6 GB). With `num_gpu: 0` that is a CPU run of a 7B model per ambiguous request, far above the 2 s timeout, so layer 3 is effectively always a timeout. Placeholder data, but the shape will be copied. | Seed catalog | Catalog data ticket (HDW-139): add a tiny text model entry as classifier. |
| F22 | medium | gap | `quotas.py:poll_key`, `count_request` (B 2768-2862) | `poll_key` has no caller, and `count_request` runs only on success, so a free model that 429s is never counted. Soft-exhaustion therefore lags real usage. | Code reading | Part of HDW-122 (calls) and HDW-131 (daemon timer). |
| F23 | low | defect | `project_config.py:effective_scope` (A 3028-3036) | Dead code (no caller in the live tree) that duplicates scope logic with a separate ranking table; it maps `cloud` to rank 0 and would "tighten" `cloud` to `free` by *adding* local. | Searched the live tree | Delete it. |
| F24 | low | nit | `discovery.py` (A 342-343); `sources.py:openrouter_models` (A 650-653) | Sends `OPENROUTER_API_KEY` to the public, unauthenticated `/models` endpoint for no benefit. | Code reading | Drop the header. |
| F25 | low | nit | `cli/cmd_features.py:CORE_FEATURES` (S 121-136) | The core list is hard-coded text in a surface. Fine for 0.1, but it will drift from v3.5 B. | Code reading | Move it to `core/features.py` as `FeatureSpec(star=False)` entries. |
| F26 | low | risk | `hardware/fit.py:CTX_HEADROOM` (B 1650) and `rules.scoring.ctx_headroom` (A 4413) | Two sources for the same 1.2: change the rule and fit and the filter disagree. | Code reading | Pass the rules value into `FitConfig`. |
| F27 | low | risk | `rules_watch.RulesManager._load_initial` (B 3065-3072) | Invalid `rules.yaml` at startup means built-in defaults, so `private_lock: true` in a file with an unrelated typo starts with the lock **off**. | Code reading | HDW-138: on a startup parse error, keep any `private_lock`/`airplane`/`default_scope` value that can be read, else start locked (local) and say so. |

Fine as they are: the scope resolver (golden table, tighten-only property tests), the executor guard
(loopback cases, including `localhost.evil.com`, `0.0.0.0`, `[::2]`, https), the KV formula and the
worked example, wall-header parsing, the SQLite layer (WAL, `BEGIN IMMEDIATE`, multi-process test),
the catalog signature path (empty key rejects every download, as intended), and logging redaction.
No hard-coded model slugs or provider URLs in routing logic: every slug-shaped hit in
`mechanical_checks.md` is a false positive (git EOL markers).

---

## 5. Hard-coded values review

**Protocol / safety** = keep in code. **Tunable** = move: `rules.yaml` for user policy,
`seed_catalog.json` for facts about models, GPUs or providers.

| Constant (file) | Value | Class | Verdict |
| --- | --- | --- | --- |
| `fit.CTX_STEPS` | 8k to 64k | Tunable (design list) | Move to `rules.tiers.local.ctx_steps`; models above 64k (128k local) are excluded today |
| `fit.CTX_HEADROOM` | 1.2 | Tunable, **duplicated** | Use `rules.scoring.ctx_headroom` (F26) |
| `fit.DEFAULT_OVERHEAD_GB` | 1.0 | Tunable | `rules.tiers.local.overhead_gb` |
| `fit.DEFAULT_OFFLOAD_WEIGHT_FACTOR` | 2.0 | Tunable | `rules.tiers.local.offload_weight_factor` |
| `fit.KV_BYTES_PER_ELEM`, `GIB`, `GB_TO_GIB` | | Protocol | Keep |
| `state.RESERVE_DISPLAY_GB/UNKNOWN/NO_DISPLAY` | 2.0 / 2.5 / 1.0 | Tunable | `rules.tiers.local.reserve_defaults`; `reserve_gb` already overrides |
| `state._LAPTOP_RANGE_GB`, `_DESKTOP_RANGE_GB` | 10-13.5 / 14-17.5 | Fact | Catalog: derive from `gpu_profiles[*].vram_gb` (already there) |
| `state.DEFAULT_BUSY_THRESHOLD_PCT` | 50 | Tunable | `rules.tiers.local.gpu_busy_pct` |
| `state.DEFAULT_CACHE_TTL_S` | 5 | Implementation | Keep |
| `state._OLLAMA_PREFIX` | `"ollama"` | Protocol (process name) | Keep |
| `residency.ResidencyConfig` | 0.15 / 0.25 / 1 | Tunable | `rules.scoring.residency` |
| `residency.default_max_loaded` | 2 if desktop else 1 | Fact | Catalog `gpu_profiles[*].max_loaded_models` |
| `sysmem.SLOW_RATIO`, `SPILL_TOLERANCE` | 0.5 / 0.02 | Tunable | `rules.tiers.local.sysmem` |
| `sysmem.SYSMEM_HINT` | user text | UI string | Keep (move to `doctor` later) |
| `classifier._MAX_PROMPT_CHARS`, `_MIN_CONFIDENCE` | 2000 / 0.75 | Tunable | Catalog `analyzer_hints` (next to `confidence_threshold`) |
| `classifier.TASKS`, `RISKS`; `requirements._TASK_ORDER` | | Schema enums | Keep, but derive from one definition (three lists today, plus `rules.SCORE_TASKS`, `schema.ANALYZER_TASKS`) |
| `requirements._IMAGE_BONUS/_JSON_BONUS/_FENCE_BONUS`, `_SHORT_PROMPT_TOKENS`, `_KEYWORD_SCAN_*`, the confidence literals 0.4/0.45/0.7/0.85 | | Tunable heuristics | Catalog `analyzer_hints` |
| `_extract.MAX_DEPTH`, `ollama_client._MAX_RESPONSE_BYTES`, `cloud_map._MAX_ERROR_CHARS`, `ollama._MAX_ERROR_BYTES`, `walls._BODY_SCAN_CHARS`, `updater.MAX_BYTES`, `verify_schema.MAX_SCHEMA_ERRORS` | | Safety limits | Keep |
| `walls._EPOCH_MS_MIN`, `_EPOCH_S_MIN` | 1e11 / 1e9 | Protocol | Keep |
| `quotas._STALE_AFTER_S` | 3600 | Tunable | `rules.walls.streak_reset_s` |
| `quotas.OPENROUTER_KEY_URL`, `sources.OPENROUTER_BASE` | URLs | Provider endpoint (fact) | Catalog `providers.openrouter.base_url`; OK for 0.1 |
| `discovery.OPENROUTER_KEY_ENV` | env name | Integration | Keep until HDW-401 (keyring) |
| `cloud_map.OPENROUTER`, `DEFAULT_EFFORT` | `"openrouter"`, `"medium"` | Protocol / tunable | Keep `OPENROUTER`; `DEFAULT_EFFORT` to `rules` |
| `cloud_map._TIMEOUT_NAMES`, `_CONNECTION_NAMES` | litellm class names | Protocol (SDK) | Keep; covered by contract tests on bumps |
| `ExecutorConfig` timeouts | 30 / 60 / 240 / 60 / 5 | Tunable | `rules.executor`; `connect_s` is **unused** (`OllamaBackend` gets its own default) |
| `RouterConfig.max_attempts` | 4 | Tunable | `rules.walls.max_attempts` |
| `ollama.DEFAULT_CONNECT_S`, `gpu._SMI_TIMEOUT_S`, `updater.TIMEOUT_SECONDS`, `UrllibHttp(timeout=10)` | | Implementation timeouts | Keep |
| `BasicVerifier.GRAMMARS` | language to grammar | Protocol (parser map) | Keep |
| `logging._SECRET_PATTERNS` | regexes | **Safety** (log redaction) | Keep in code: redaction must not depend on a downloadable file. Document the overlap with catalog `secret_patterns` |
| `paths._WIN_LONG_PATH_THRESHOLD` (240), `state.BUSY_TIMEOUT_MS`, `router_session._MAX_ID_CHARS/_HASH_CHARS`, `plugins.CURRENT_API_VERSION`, `rules_watch.DEFAULT_DEBOUNCE_S` | | Protocol / implementation | Keep |
| `scope.SCOPE_TIERS`, `SUFFIX_*`, `_EXTRA_ALIASES` | | Product contract (v3.3 A/B) | Keep |
| `ladder._VERIFIABLE_TASKS` | `{"code"}` | Tunable policy | `rules.floors.verifiable_tasks` |
| `rules.py` defaults (floors, weights, walls) | | Already in rules | Fine |
| Seed `wall_patterns`, `secret_patterns`, `placeholder_patterns` | | Catalog | Correct place; the `placeholder_patterns` list has one entry (laziness markers such as `rest of code` / `// existing code` are missing: HDW-139) |

No hard-coded error regexes or placeholder patterns in routing or verification logic: confirmed.

---

## 6. Test-quality review (highest risk first)

1. **No egress test at the socket level for the router.** "Local scope never touches the cloud"
   is asserted by counting a fake `cloud_factory` (T 2801-2818). That proves the factory wasn't
   called, not that no socket opened (classifier, discovery, a litellm import side effect). The
   catalog updater has a socket spy (`tests_other` 1162-1170); reuse it for `Router.run` in local,
   Private-lock, secret and airplane states. Full proof is HDW-306; the core half belongs in HDW-120.
2. **The golden tables restate the implementation in two places.** `policy_golden_support.run`
   builds `ScopeDecision` by hand and sets `tiers = LOCAL_TIERS if locks` (T 409-411). So golden B
   never exercises `resolve_scope`, and `private-lock-battery-no-cloud-hint` passes only because the
   test supplies `locks`, which hides F9. Expectations such as `fallbacks` are often the
   implementation's order, not the design's. Route the golden runner through `resolve_scope`
   (rules plus requested), and add rows derived from v3 4.5 text: below-floor local in local
   scope, `primary_first` with a primary under the floor, offload versus fits, escalation going
   *up*.
3. **Secret-lock tests cover user text only.** The fuzz tests check the detector, not the request
   surface. There are no cases for tool-call arguments, `tool_use.input`, system blocks or
   multi-part content (F1).
4. **Fixtures are not realistic.** The CLI `seed_models` has a local model scoring 80 with
   agentic 80; real seeds are 0.6 to 0.7. Few tests cover realistic agentic requests (tools plus a
   big system prompt), which is why F4 went unnoticed. Add an agent-mode end-to-end test through
   the real `Analyzer`.
5. **The contract test is environment-dependent (F6)**, and nothing tests that litellm stays
   unloaded (P6 shows that it does today).
6. **Verification tests accept "nothing ran" as success.**
   `test_requested_verify_without_any_verifier_is_reported_as_not_run` pins the F11 behaviour as
   correct. Change it to assert a flagged result when `verify_required`.
7. **Escalation tests check direction-free outcomes** (F10): the escalation test goes from 90 to
   84 and asserts success.
8. **The router uses a fake `CandidateSource`** (`lambda _reqs: pool`), so `CandidateBuilder` plus
   `Router` together, with real `refresh_models` output, is untested apart from the CLI dry run.
   Add one test from discovery fixtures to `Router.run`.
9. **No hot-reload or long-lived router test** (F12).
10. **Minor:** `test_filter_context_needs_headroom` pins F18; `test_score_complexity_penalty` tests
    arithmetic for a term that can't change a ranking (F19); `test_golden_table_has_at_least_40_scenarios`
    counts rows, not coverage.

Strengths worth keeping: the frozen-clock quota tests, the wall fixtures per provider, the KV
worked example, and the `pick` dry-run "writes nothing" test.

---

## 7. Decisions D10, D11, D12 (ready to paste into `docs/DECISIONS.md`)

Rows for the decisions table:

```
| D10 | Escalation in core: **core gets one bounded "verified retry"**. When a mandatory verification fails, retry once on the next *stronger* candidate inside the effective scope. The full verify-then-escalate loop (failure reasons in the prompt, max_escalations, budget, optimistic rung, learned pass rates) stays ★ (HDW-606). | Decided (2026-10-31, HDW-119) |
| D11 | Sticky sessions in core: **core gets basic stickiness**. Inside a session, keep the model that last answered successfully while it stays usable and in scope; switch only on a wall, a scope or lock change, a failed verification, or the hard filter. Handoff packets, summaries and context-threshold re-tiering stay ★ (HDW-607). | Decided (2026-10-31, HDW-119) |
| D12 | `headwaters_think` in 0.1: **ship it in core as single-model reasoning** (best reasoning-capable model in scope, reasoning on, reasoning text never returned or stored). Fusion `deliberate` and duck are ★ 1.x modes of the same tool. | Decided (2026-10-31, HDW-119) |
```

### D10 detail: escalation in core

**Decision.** Core adds one bounded retry, only when verification was mandatory
(`verify_required`: below the floor, optimistic, best-effort, local below the floor) and failed.
The retry goes to the best remaining candidate whose task score is *higher* than the failed one's,
inside the same effective scope. It never leaves the scope, it counts against `max_attempts`, and
it doesn't apply once a stream has sent its first token. With no stronger candidate, the answer
comes back flagged `verify_failed`, as today. A registered ★ `EscalationPlugin` replaces the rule.

**Reason.** Once local scope serves below-floor answers (HDW-123, needed for G4 and v3.3 D3),
"basic verification, safety baseline" (v3.5 B, core) needs a consequence. A check that fails and
changes nothing is a report, not a gate. One retry on a stronger model is the smallest behaviour
that keeps both "local is usable" and "checked before you see it" true. v3.5 B allows moving ★
behaviour into core; this moves only the smallest slice and leaves the paid differentiators
(failure reasons fed back, multi-step and budget-aware paid escalation, learning) in ★.

**Cost.** One extra model call when a check fails (latency only in local and free scope). Some
router code. The promise can never go back to ★.

**Tickets.** HDW-123, HDW-129, HDW-130.

**Revisit** at the HDW-608 gate with SpillBench numbers. If the core retry already captures most
of the verification-v2 gain, re-scope ★ verification v2; do not weaken core.

### D11 detail: sticky sessions in core

**Decision.** On the next turn, core puts the session's last *successful* model first while it is
usable: no wall, still in scope, passes the hard filter, and still meets the floor (or is the same
below-floor local choice). It moves on a wall, a scope or lock change, a failed verification, or
when the model drops out of the hard filter (for example, context outgrows it). Without a session
model the ladder runs as today. ★ HDW-607 keeps handoff packets, older-turn and reasoning
summaries, and context-threshold re-tiering.

**Reason.** The gateway is core (v3.5 A), and agent loops resend the whole conversation every
turn. Switching models between turns breaks tool-call conventions, wastes prompt-cache hits and
hands a frontier model's half-finished plan to a cheap model. Today's warm bonus is not
stickiness: 15 points don't hold a model across a ranking change, and it gives a VRAM bonus to
cloud models and erases eviction cost (F15). v3 4.5 "Session rules" already describes this
behaviour; v3.5 B marks only the handoff side as ★.

**Cost.** A session can stay on a model that a fresh decision would not pick. That is limited by
"usable and in scope", and soft-exhaustion counts as a reason to move. Session state becomes
routing state, so the session key matters (a hash of system prompt plus first user message is
good enough).

**Tickets.** HDW-134.

**Revisit** if HDW-305 shows sessions stuck on a degrading free tier, or when HDW-607 lands (★ must
extend the core rule, not replace it).

### D12 detail: `headwaters_think` in 0.1

**Decision.** Ship `headwaters_think` in Beta 0.1 as core. It is a read-only tool that sends the
question (and optional paths) to the best reasoning-capable model in the effective scope with
reasoning on, and returns the final answer plus metadata, never the reasoning text. Local scope
uses local thinking models; free scope uses free reasoning models. Fusion `deliberate` (★ HDW-901:
opt-in, budget-capped, never in local or free scope) and `duck` (★ HDW-902) are modes of the same
tool, registered by features.

**Reason.** v3.4 B1 fixes eight front-door tools; a front door with a missing tool teaches hosts
not to call it. Core already has what this needs: the `reasoning` task and floor,
`ExecOptions.reasoning` for both backends, and reasoning captured apart from content. v3.5 B marks
"Fusion `think`" and the rubber duck as ★, not the tool, so this matches the core/★ line.

**What core must expose before HDW-205:**
- `RouteRequest.task_hint`, which forces `soft.task` (here `reasoning`) without touching the hard
  filter, so the tool doesn't depend on keyword hits.
- `RouteRequest.reasoning`, mapped to `ExecOptions.reasoning` (today it is reachable only by
  passing a whole `options` object).
- A `reasoning_ok` capability on `ModelRow` (Ollama `capabilities: thinking`, OpenRouter
  `supported_parameters: reasoning`) and a matching hard-filter flag, so "reasoning-capable" is
  data, not a name list.
- A documented rule that `ExecResult.reasoning` is never put on the wire or stored, except for the
  short summary v3 4.7 allows.

**Cost.** A small core API addition and one capability column (migration 3). One more tool to
support in 0.1.

**Tickets.** HDW-140.

**Revisit** at the Beta 0.2 gate (HDW-509) if hosts mostly call `think` for work that
`headwaters_do` should do.

**Check against v3.5 B.** D10 and D11 move a slice of ★ behaviour into core, which is allowed.
D12 keeps the ★ overlays ★. Nothing moves from core to ★.

---

## 8. Punch list as new tickets

Model choice follows the backlog's "How to use it" item 4: Sonnet for routine work and core logic,
Opus for verification spikes and privacy/security work. **Blocks Phase 2** = merge before HDW-201.
Tickets marked **Before HDW-xxx** must land before that Phase 2 ticket; **Can wait** = any time
before the Beta 0.1 gate (HDW-407).

### HDW-120 · Secret scan covers every message part · **Blocks Phase 2**
**Model** Sonnet · **Effort** Medium · **Depends** 112, 117 · **Class** Core · **Refs** v3.3 §D8, F1
**Build:**
- `router_prep.scan_texts` walks every string leaf of every message: `content` parts, OpenAI `tool_calls[].function.arguments`, Anthropic `tool_use.input`, nested `tool_result` content, and system blocks. Reuse `_extract.collect`, extended to keep the texts.
- A socket-spy fixture (shared with the catalog updater tests) asserts no non-loopback connect during `Router.run` in the secret-locked state.
**Accept when:** P1's four shapes are all detected; a secret only in a tool call routes local; the socket spy records no remote connect; no secret text appears in reasons or rows.

### HDW-121 · Ollama cloud-proxied models are never local · **Blocks Phase 2**
**Model** **Opus** · **Effort** High · **Depends** 105, 115 · **Class** Core · **Refs** v3.3 §D1-D2, A-OLL-8, F2
**Build:**
- Verify against current Ollama docs and a live probe how cloud models appear in `/api/tags` and `/api/show` (remote fields, `:cloud`/`-cloud` tags). Record it as A-OLL-9.
- Discovery marks them `retains_data=true`, `tier=free_cloud`/`paid` per catalog, or drops them; never `local`.
- `enforce_scope` and `OllamaBackend` refuse a local call to a model flagged remote (defence in depth); the layer-3 classifier refuses one too.
- Catalog: `ollama_remote_patterns` (data, not code).
- `doctor` hint (HDW-402): recommend `OLLAMA_NO_CLOUD=1` when Private Mode is used.
**Accept when:** a fixture `/api/tags` with a cloud model never yields a local candidate; Private-lock routing to it raises `LocalScopeViolation`; the assumption is recorded with a source and date.

### HDW-122 · Credit walls hit paid models only, and expire · **Blocks Phase 2**
**Model** Sonnet · **Effort** High · **Depends** 110, 111, 117 · **Class** Core · **Refs** v3 §4.4, F3, F22
**Build:**
- Credit exhaustion (`exhausted` from a 402 or credit body on a paid model) records a subject that only paid models of that provider/key check (e.g. `provider:openrouter:paid`). Free-tier exhaustion (`free-models-per-day`) records a free subject.
- An exhausted wall without a reset gets `until = now + walls.exhausted_recheck_s` (rules, default e.g. 900).
- The router passes the key alias when the executor knows it; `count_request` also runs on 429.
**Accept when:** after a paid OpenRouter 402, a `:free` OpenRouter model is still `ok`; the wall lifts after the recheck time; P9 is a regression test; the quota tests still pass.

### HDW-123 · Local scope serves the best local model below the floor · **Blocks Phase 2**
**Model** Sonnet · **Effort** Medium · **Depends** 114 · **Class** Core · **Refs** v3 §4.5 step 4, v3.3 §D3, D10, F4
**Build:**
- In `build_ladder`, local-only and battery `local_only` states append the below-floor *local* entries (`best_effort`, `verify_required`) after the local entries that meet the floor; `local_below_floor` is raised only when no local model passes the hard filter. The error keeps its suggestions, now attached as `RouteDecision.suggestions` (`pull_model`, `split_task`).
- Remove the analyzer's `+int(agentic)` complexity step (the floor already carries `agentic_bonus`); update the HDW-106 fixtures.
- Golden rows: `agentic-local-scope` (served, best_effort, verify), `scope-local-too-hard` (served below floor), no cloud fallback in any row.
**Accept when:** P2 gives a local decision in local scope with `below_floor=True, verify_required=True`; no golden row leaves the local tier in local scope; CLI `pick --scope local` shows the warning.

### HDW-124 · Complete the import contract · **Blocks Phase 2**
**Model** Sonnet · **Effort** Medium · **Depends** 001 · **Class** Core · **Refs** D7, D8, v3.5 §F, F5
**Build:**
- Add `headwaters.cli` to the core contract's forbidden modules.
- New contracts with `include_external_packages = True`: only `headwaters.core.executor.cloud` may import `litellm`; only `headwaters.mcp` may import `mcp`; only `headwaters.gateway` may import `fastapi`/`starlette` (in place before Phase 2 code exists).
- A source test that fails on any `litellm` reference outside `executor/cloud.py` and `cloud_map.py` (it catches `importlib` strings the linter can't see).
- A test that importing `headwaters.core.router` and `headwaters.cli.main` leaves `litellm` out of `sys.modules`.
**Accept when:** `lint-imports` shows every contract kept; deliberate violations for each new contract fail; the litellm source test passes.

### HDW-125 · Make subprocess decoding independent of the code page · **Blocks Phase 2**
**Model** Sonnet · **Effort** Low · **Depends** 001, 108 · **Class** Core · **Refs** gap 11, F6
**Build:** `encoding="utf-8", errors="replace"` (plus `PYTHONIOENCODING=utf-8` in the child env) in `tests/test_import_contract.py`, `hardware/gpu.py:run_nvidia_smi`, and any other `text=True` call (search the repo).
**Accept when:** the full suite passes 3 times in a row in a cp1252 console on Windows; an `nvidia-smi` fixture with a non-ASCII process name parses.

### HDW-126 · Production never registers `features/_example` · **Blocks Phase 2**
**Model** Sonnet · **Effort** Low · **Depends** 004 · **Class** Core · **Refs** v3.5 §F, F7
**Build:** move `_example` to `tests/fixtures/features/_example` (or make `discover_features` skip `_`-prefixed packages unless `include_private=True`); the contract tests discover it explicitly.
**Accept when:** `discover_features(FeatureRegistry())` in production returns no `_example`; the contract harness still runs against the example; P8 is a regression test.

### HDW-127 · litellm never writes to stdout and logs through redaction · **Before HDW-202**
**Model** Sonnet · **Effort** Low · **Depends** 003, 115 · **Class** Core · **Refs** v3.1 §C5, F8
**Build:** in `_load_litellm` set `suppress_debug_info = True` and `set_verbose = False`, and attach `RedactionFilter` (plus a level cap) to the `LiteLLM`, `LiteLLM Router` and `LiteLLM Proxy` loggers; record the attribute names in ASSUMPTIONS (A-LLM-6).
**Accept when:** under `guard_stdout(strict=True)`, a cloud call that raises a mapped litellm exception (fake module) writes nothing to stdout; a key in a litellm log record is redacted.

### HDW-128 · Hints honour every active lock · **Before HDW-205**
**Model** Sonnet · **Effort** Low · **Depends** 112, 114 · **Class** Core · **Refs** v3.3 §B4, F9
**Build:** `ScopeDecision.active_locks` lists every lock in force (Private lock, project rule, secret, airplane), narrowing or not; `suggestions_for` and `no_route` key the cloud hint on it; the golden runner builds scope through `resolve_scope`.
**Accept when:** P3 yields no `use_cloud` hint; a golden row covers Private lock plus `default_scope: local` plus battery.

### HDW-129 · Escalation and the core verified retry go to a stronger model · **Before HDW-204**
**Model** Sonnet · **Effort** Medium · **Depends** 117, 123 · **Class** Core · **Refs** v3 §4.6, D10, F10
**Build:**
- `_escalate` and the new core retry (D10: one retry when `verify_required` and the check failed, no plugin needed) redecide over candidates whose task score is above the failed model's, with `escalation=None` so the optimistic rung can't fire again.
- The retry is counted in `max_attempts`, stays in scope, and is skipped after the first streamed token.
**Accept when:** tests cover cheap fail then stronger pass; no stronger candidate gives the original answer flagged; local scope retries only locally; the existing escalation test is updated so it goes up.

### HDW-130 · A mandatory gate that checked nothing is not a pass · **Before HDW-204**
**Model** Sonnet · **Effort** Low · **Depends** 116, 117 · **Class** Core · **Refs** v3 §4.5 step 4, F11
**Build:** `VerifyOutcome.checked` (checks that actually ran, skips excluded); when `decision.verify_required` and `checked == 0`, the result carries `verify="unchecked"`, the outcome row stores `unchecked`, and `below_floor` stays visible to the engine.
**Accept when:** P8's prose case reports `unchecked`; a missing grammar is a skip, not a pass; the old "not run" test is replaced.

### HDW-131 · Live rules in long-running engines · **With HDW-201**
**Model** Sonnet · **Effort** Medium · **Depends** 101, 117 · **Class** Core · **Refs** v3.3 §C, F12, F22
**Build:**
- `Router`, `Executor` and `QuotaTracker` take a rules provider (`Callable[[], Rules]`, `RulesManager.current`), read once per request in `_select` and in `enforce_scope`. `enforce_scope` re-checks `rules.private_lock` and `airplane` itself.
- The daemon schedules `poll_key` with the live `airplane`/lock state.
**Accept when:** a test toggles `private_lock` between two requests on one Router and the second never builds a cloud client; airplane set mid-run blocks the next cloud call.

### HDW-132 · `primary_first` means the primary first · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 114 · **Class** Core · **Refs** v3.3 §A, F13
**Build:** under `primary_first`, every usable primary leads (flag `below_floor` when under the floor), then `openrouter/auto`, then the cost_saver ladder.
**Accept when:** a golden row with a primary scoring under the floor picks it, flagged; the walled-primary row still falls to auto.

### HDW-133 · Score terms that do something: offload, complexity · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 113 · **Class** Core · **Refs** v3 §7.1, F14, F19
**Build:** `scoring.offload_penalty` (rules) applied to `fit.status == "offload"`; make `complexity_penalty` model-dependent (scaled by the gap to 100) or remove it and its rule.
**Accept when:** P4 shows offload below fits; a golden row prefers a fitting model over an offloaded one with an equal score.

### HDW-134 · Core session stickiness (D11) · **Before HDW-302**
**Model** Sonnet · **Effort** Medium · **Depends** 117, 123 · **Class** Core · **Refs** v3 §4.5 session rules, D11, F15, F16
**Build:**
- Separate the session preference from residency: never zero `eviction_penalty`, never give a VRAM bonus to cloud models.
- The D11 rule: the last *successful* model leads while usable, in scope, passes the hard filter and still meets the floor (or is the same local best-effort choice). Pin only on `ok`.
- The reason line says "kept session model".
**Accept when:** P5 shows no cloud warm bonus and keeps the evict penalty; a three-turn test keeps one model across a small ranking change and moves on a wall and on `verify_failed`.

### HDW-135 · Remote images are refused, not dropped, for local models · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 115 · **Class** Core · **Refs** v3.3 §D2, F17
**Build:** a local call with a non-`data:` `image_url` raises a structured `image_url_not_local` error (no fetch); the engine may inline it outside local scope.
**Accept when:** tests cover a data URL (sent), an https URL in local scope (refused) and nothing fetched.

### HDW-136 · Unknown context is not "too small" · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 113 · **Class** Core · **Refs** F18
**Build:** `ctx is None` uses `unknown_model_defaults.ctx` (via the candidate builder) and rejects with `ctx_unknown` only if that is also too small.
**Accept when:** the filter test is updated; the suggestion is not `split_task` for unknown context.

### HDW-137 · Local fix hints name a model · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 117 · **Class** Core · **Refs** v3.3 §C, F20
**Build:** the router builds `local_fix_hint` and the `pull_model` suggestion text from `catalog.gpu_profiles[hw.profile].recommended` for the request's role (vision, agentic_coder, general).
**Accept when:** a vision request in local scope with no vision model says "pull <catalog model>".

### HDW-138 · Hard-coded tunables to rules and catalog; startup fail-safe · **Can wait**
**Model** Sonnet · **Effort** Medium · **Depends** 101, 103 · **Class** Core · **Refs** section 5 of this review, F26, F27
**Build:**
- Move every value marked "Tunable" or "Fact" in section 5 to `rules.yaml` or the catalog (one schema bump each); regenerate both schema files; delete `project_config.effective_scope` (F23) and the duplicate `CTX_HEADROOM`.
- On a startup parse error, keep any readable `private_lock`, `airplane` and `default_scope`; if none can be read, start in local scope and say why (F27).
**Accept when:** the section 5 table rows are gone from code; schema checks pass; an invalid file with `private_lock: true` starts locked.

### HDW-139 · Seed catalog: classifier and laziness patterns · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 103 · **Class** Core (data) · **Refs** v3 §4.6, F21
**Build:** a tiny text classifier entry for both GPU profiles; `placeholder_patterns` for the v3 4.6 laziness markers (`rest of (the )?code`, `// existing code`, `... remaining`, `TODO: implement`), all `verify: true`.
**Accept when:** the seed validates; the verify fixtures catch each marker.

### HDW-140 · Core hooks for `headwaters_think` (D12) · **Before HDW-205**
**Model** Sonnet · **Effort** Medium · **Depends** 106, 115, 117 · **Class** Core · **Refs** v3.4 §B1, D12
**Build:** `RouteRequest.task_hint` (forces `soft.task`); `RouteRequest.reasoning` (mapped to `ExecOptions.reasoning`); `ModelRow.reasoning_ok` from Ollama `capabilities: thinking` and OpenRouter `supported_parameters: reasoning` (migration 3) plus a `HardRequirements.reasoning` filter; a documented rule that reasoning text is never returned or stored.
**Accept when:** a think-style request in local scope picks a local thinking model with `think` on; reasoning text is absent from rows, events and the default result rendering.

### HDW-141 · Pin Python 3.13 for development · **Can wait**
**Model** Sonnet · **Effort** Low · **Depends** 001 · **Class** – · **Refs** owner question 2026-10-31, gap 12
**Build:** add `.python-version` with `3.13`; delete `.venv313`; add `.venv*/` to `.gitignore`; keep `requires-python = ">=3.11"`; add an allowed-to-fail CI job on the newest Python.
**Accept when:** a fresh `uv sync` builds a 3.13 env on a machine where 3.15 is the default; `ruff check .` passes on the whole tree.

---

## 9. Doc corrections

| # | Document and location | Statement | Correction |
| --- | --- | --- | --- |
| 1 | `03_KNOWN_GAPS.md` item 11 | The cp1252 failure "failed once ... passed on every later run". | It fails 5 of 5 in a cp1252 console (re-run 2026-10-31). It is deterministic under that environment, not a flake (F6). |
| 2 | `ASSUMPTIONS.md` A-OLL-8 (line 97) | "the loopback assertion must reject any non-loopback host" is presented as sufficient for local scope. | Add A-OLL-9: the local server can proxy `:cloud` models to ollama.com, so loopback alone does not prove local. Local scope also needs a remote-model check and `OLLAMA_NO_CLOUD` advice (F2). |
| 3 | `ASSUMPTIONS.md` Executor row (line 179) | "litellm is imported lazily ... with `LITELLM_LOCAL_MODEL_COST_MAP=True` and `telemetry=False`" reads as the full set of litellm hardening. | Add that `suppress_debug_info` is off by default and litellm prints to stdout on mapped exceptions (F8). |
| 4 | `ASSUMPTIONS.md` Policy B and Router rows (178, 181) | Describe local scope erroring below the floor as the design. | v3 4.5 step 4 and v3.3 D3 say best-effort cheap with mandatory verification, then local escalation, then error. Superseded by D10 and HDW-123. |
| 5 | `ASSUMPTIONS.md` Router row (181) | "warm bonus" from the session pin. | Per D11, it is session stickiness, separate from residency (HDW-134). |
| 6 | `ASSUMPTIONS.md` Quotas row (175) | Exhausted is lifted "by a balance check or the reset time". | No balance check is wired, and generic exhausted walls have no reset, so they never lift (F3, HDW-122). |
| 7 | `ASSUMPTIONS.md` CLI row (182) | `features` shows "registered star features; `_`-prefixed internal features stay hidden". | Hidden from the listing, but still registered and active in routing (F7). |
| 8 | Backlog SPW-301 (line 527) | "following D7 (LiteLLM proxy + hooks **or** thin FastAPI + litellm SDK)". | D7 decided the thin FastAPI gateway. Remove the proxy alternative; also SPW-303 "Use LiteLLM's translation if D7 chose it" (D7: yes, `litellm.anthropic.messages`). |
| 9 | Backlog SPW-905 (935-940), Context Pack (76) | `sampling` is listed as a ★ module; SPW-905 is written as buildable. | Deferred by D9: mark it DEFERRED in the backlog and drop `sampling` from the features layout. |
| 10 | Backlog SPW-001 (125) | "seed with D1–D6". | `docs/DECISIONS.md` now holds D1-D12; packets should point there. |
| 11 | Backlog section 2 "Open decisions" (108-111) | Still open. | Settled by D10-D12; replace with links. |
| 12 | Backlog SPW-114 build (360-370) | The optimistic rung exists "only if an EscalationPlugin is registered". | Still true, but D10 adds the core retry for mandatory checks; note the interaction (the optimistic rung stays ★). |
| 13 | `02_BRIEF.md` priority 4 | "`litellm` is isolated to `executor/cloud.py`". | True by convention only: `cloud_map.py` also names it, and no contract enforces it (F5). |
| 14 | `DECISIONS.md` D7 "Costs accepted" | "isolate all litellm use behind `headwaters/core/executor`". | Add "enforced by an import-linter external-package contract (HDW-124)". |
| 15 | v3 design 5.1 and the 2. diagram | LiteLLM Proxy plus hooks; FastMCP. | Superseded by D7 and D8. Add a pointer, since packets still excerpt v3. |

---

## 10. Questions for the owner

1. **Ollama cloud models (F2).** Should Headwaters ever route to Ollama `:cloud` models? If yes,
   which tier: `free_cloud` (subscription with a free allowance), `paid`, or a new
   `ollama_cloud`? v3 section 11 rule 4 ("don't route consumer subscription credentials") suggests
   never. The safe default in HDW-121 is "not routable".
2. **Below-floor answers in local scope (D10).** Confirm that `headwaters/local` should answer with
   the best local model, flagged and verified, rather than refuse. This is the user-visible change
   behind HDW-123.
3. **Default strategy for picker models.** `primary_first` is the only way paid primaries lead.
   Should `headwaters/auto` with the ★ paid-scopes feature default to `primary_first` (v3.2 D2,
   "saver mode") or stay on `cost_saver`?
4. **Startup with an invalid `rules.yaml` (F27).** Start locked to local (safe, surprising), or on
   defaults with a loud error (today)? HDW-138 proposes "keep what can be read, else local".
5. **Catalog signing key (gap 8).** When will the real key be generated? Updates, and so the fixes
   to catalog data such as F21, can't ship to testers until it is.
6. **Telemetry of the litellm SDK.** `telemetry=False` is set at runtime. Do you also want a CI
   check that litellm's telemetry and cost-map fetch stay off after every version bump (D7 costs)?
7. **Python version (asked 2026-10-31).** Confirm 3.13 as the development and installer version
   (HDW-141) and 3.11 as the floor.
