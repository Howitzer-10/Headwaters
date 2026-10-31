# HDW-119 recheck (second pass)

- Date: 2026-10-31
- Checked tree: `C:\Projects\Headwaters` at `150d1db` (clean working tree, in sync with `origin/master`)
- Scope: sections 1, 4 and 8 of `review_bundle_HDW-119/HDW-119_REVIEW.md` (same SHA-256 as `docs/HDW-119_REVIEW.md`: `A744D0E3...AFBEFC2`). Blockers HDW-120 to HDW-126, with HDW-121 checked on its own.
- This replaces the first recheck (written at `7a277b8`, verdict NO-GO because HDW-121 was unbuilt and CI had never run). Since `7a277b8` only HDW-121 (`ollama_remote.py`, `guard.py`, `executor.py`, `ollama.py`, `sources.py`, `discovery.py`, `schema.py`, `classifier.py`, `layers.py`, `cli/runtime.py`, seed catalog and schema, two test files) and HDW-143 (`ci.yml`, `core/state.py` WAL retry) changed code. Nothing under HDW-120, 122, 123, 124, 125 or 126 was touched, so their evidence is carried from the first pass and backed by this pass's full run.
- Nothing was fixed. Throwaway probe and run scripts were deleted.

## Verdict: GO for HDW-201

All eight Blocks-Phase-2 items pass their accept-when, the full suite and `lint-imports` are green locally, and the head commit is green on all four CI cells. One accept-when item (HDW-120, "no secret text in rows") stays partial; it is carried by FU-120-1 / HDW-306 and does not block HDW-201.

## Full run

| Check | Result |
| --- | --- |
| `uv run pytest -q --no-cov -rs` | 971 passed, 0 failed, 1 skipped (`tests/test_paths.py:68`, non-Windows passthrough), 98.7 s |
| `uv run lint-imports` | 4 contracts kept, 0 broken |
| `uv run ruff check .` | All checks passed |
| `uv run mypy` | no issues in 175 source files |
| CI, head commit `150d1db`, run 41237677091 | success. Same code as `f4a967f`, run 41237444364: windows and ubuntu, Python 3.11 and 3.13, all four green |

## Per ticket

| Ticket | Result | Evidence |
| --- | --- | --- |
| HDW-120 secret scan | **PASS, one item partial** | `tests/test_router_secret_parts.py:64` `test_every_shape_is_detected` (user text, `tool_result`, OpenAI `tool_calls` arguments, Anthropic `tool_use` input); `:103` `test_a_secret_only_in_a_part_routes_local_without_network` (local model, no cloud client, no remote connect). Code: `headwaters/core/analyzer/_extract.py:13,48,109-122`, `router_prep.py:50`. **Partial:** "no secret text in reasons or rows" is asserted on `result.reasons`, `result.decision` and `result.attempts` (`:112-114`); the stored SQLite rows are not read back. FU-120-1, merged into HDW-306. |
| HDW-121 Ollama cloud models never local | **PASS** | Discovery: `headwaters/core/catalog/sources.py` `ollama_models(http, host, remote_patterns)` drops a model with remote fields or a matching name; `headwaters/core/ollama_remote.py:39,44` `has_remote_fields`, `is_remote_name`. Patterns are data: `catalog/seed_catalog.json:104` `ollama-cloud-tag`, `Catalog.ollama_remote_patterns`. Accept-when 1: `tests/test_ollama_remote.py:38` `test_live_capture_cloud_model_never_yields_a_local_candidate` (real capture `docs/ollama_tags_sample_cloud.json`, with and without name patterns), `:59`, `:64`, `:76`, `:82`, `:87`. Accept-when 2: `tests/test_ollama_remote_exec.py:34` `test_private_lock_routing_to_a_cloud_model_raises_before_any_backend` (`LocalScopeViolation`, no backend or cloud client built). Defence in depth: `executor/guard.py:17,61-62`, `executor/executor.py:145,148-149`, `executor/ollama.py:47-48`; tests `test_ollama_remote_exec.py:51,59,74,92,108`. Accept-when 3: `docs/ASSUMPTIONS.md:98` A-OLL-9 (verified 2026-10-31, Ollama 0.40.13, source named). Live check this pass on the real capture: 17 tags, 16 kept, `gpt-oss:120b-cloud` dropped with one warning, every kept model `tier=local`; also dropped with no patterns, because the capture carries `remote_host`/`remote_model`. D13 followed (dropped, never `free_cloud`/`paid`). Not built, per the report's deviation: the `doctor` hint (HDW-402, FU-121-1). |
| HDW-122 credit walls | **PASS** | `headwaters/core/quotas.py:78,84` `credit_class` / `credit_subject`; `:211,378` `until = now + exhausted_recheck_s`; `headwaters/core/rules.py:154`; `headwaters/core/router.py:308,349-355` key alias and 429 counting. Tests: `tests/test_router_walls.py:77` `test_p9_paid_402_leaves_the_free_openrouter_model_ok_and_the_wall_lifts`, `:99`; `tests/test_quotas.py:177` and `:342` `test_p9_paid_402_leaves_free_models_of_the_same_provider_ok`. |
| HDW-123 local scope below the floor | **PASS** | `headwaters/core/policy/ladder.py:23,38` (`is_local_only`, below-floor entries), `decide.py:75`; no `local_below_floor` is raised in `headwaters/`; no `int(agentic)` in `analyzer/requirements.py` (comment `:201`). Golden rows `tests/test_policy_golden_b.py:64,78,92`; `tests/test_policy_golden_c.py:128,138`; CLI `headwaters/cli/pick_report.py:94`, `tests/test_cli_pick.py:84`. |
| HDW-124 import contract | **PASS** | `lint-imports` 4 kept, 0 broken (run this pass). `.importlinter` forbids `headwaters.cli` in core and confines litellm, mcp and fastapi/starlette. Deliberate violations fail: `tests/test_import_contract.py:68`, `:89`; allowed imports pass `:103`. `tests/test_litellm_guard.py:70` source scan; `tests/test_import_contract.py:116-117` `sys.modules`; `:129` telemetry and cost map. The `mcp`, `fastapi`, `starlette` contracts pass vacuously (none installed; FU-124-1). |
| HDW-125 subprocess decoding | **PASS** | `headwaters/core/hardware/gpu.py:151-153` (`encoding="utf-8"`, `errors="replace"`, `PYTHONIOENCODING`); `tests/test_hardware_probes.py:141,149`. The three consecutive cp1252 runs are recorded in `docs/reports/HDW-125.md` (955 passed each, earlier tree). This pass ran the suite once in the default console, not three cp1252 runs; that proof is carried from the report, and the code did not change. |
| HDW-126 no `_example` in production | **PASS** | `headwaters/features/_example` is gone; `tests/fixtures/features/_example/__init__.py` exists; `tests/test_features_discovery.py:26` `test_production_discovery_registers_no_example`; contract harness `tests/contracts/test_example_contracts.py:11`. |

## Probes

| Probe | Finding | Result | Evidence |
| --- | --- | --- | --- |
| P1 secret in tool calls | F1 | **PASS, regression test exists** | `tests/test_router_secret_parts.py:64`, all four shapes. |
| P2 agentic request in local scope | F4 | **PASS, regression test exists** | `tests/test_policy_golden_b.py:78` `agentic-local-scope-p2`: served `best_effort`, `below_floor=True`, `verify_required=True`. |
| P3 Private lock plus local scope gives a cloud hint | F9 | **FAIL, still open, no regression test** | Belongs to HDW-128 (Before HDW-205), not a blocker. Live run this pass with `private_lock=True`: `resolve_scope(requested=None)` gives `locks=('private_lock',)`; `resolve_scope(requested="local")` gives `locks=()`. `ladder.py:41,44,80` key the hint on `locks`, so the second case still gets a cloud hint. `scope.py` and `ladder.py` are unchanged since the first recheck; `tests/test_policy_golden_b.py:144` still injects `locks` by hand. |
| P7 `gpt-oss:120b-cloud` in `/api/tags` | F2 | **PASS, regression test exists** | `tests/test_ollama_remote.py:38`; live run above: dropped, never `local`. |
| P8 `_example` registered in production | F7 | **PASS, regression test exists** | `tests/test_features_discovery.py:26`. |
| P9 paid 402 blocks `:free` OpenRouter models | F3 | **PASS, regression test exists** | `tests/test_router_walls.py:77`, `tests/test_quotas.py:342`. |

Summary: P1, P2, P7, P8 and P9 are regression tests and pass. Only P3 (HDW-128) still reproduces and has no test.


## Findings (remaining problems; none fixed)

| ID | Severity | Where | Finding |
| --- | --- | --- | --- |
| R1 | medium | HDW-120 accept-when, `tests/test_router_secret_parts.py:112-114` | "No secret text appears in rows" is asserted only on in-memory objects, not on the SQLite decision and outcome rows. FU-120-1, merged into HDW-306. |
| R2 | medium | HDW-128, `headwaters/core/scope.py:91,272`, `ladder.py:41,44,80` | P3 / F9: Private lock plus requested `local` gives `locks=()` and a `use_cloud` hint while Private Mode is on. Before HDW-205, not a Phase 2 start blocker. |
| R3 | medium | `Executor` and `OllamaBackend` construction sites (FU-121-2) | They default to no `remote_patterns`; only the CLI dry run passes `remote_patterns_from(catalog)`. Discovery still drops the models, but the executor's name guard is off at any site that forgets the patterns. HDW-201 builds the first real daemon site, so it must pass them and test it. |
| R4 | low | `headwaters/core/candidates.py` (FU-121-4) | A stale `local` row for a cloud model stays in SQLite until Ollama next answers a refresh. The executor refuses it only if patterns are passed (see R3). |
| R5 | low | `catalog/` repo, updater (FU-121-3) | `Catalog` rejects unknown keys, so an older client rejects a signed catalog that carries `ollama_remote_patterns`. Publish it only after clients ship, or bump `schema_version`. |
| R6 | low | HDW-121 `doctor` hint (FU-121-1, HDW-402) | The `OLLAMA_NO_CLOUD=1` recommendation is not built. Discovery and executor already refuse cloud models; the hint is advisory. |
| R7 | low | contracts `mcp-sdk-only-in-mcp`, `web-framework-only-in-gateway` | They pass because nothing imports those packages, so whether they bite is unproven until HDW-201 and HDW-202 (FU-124-1). The layout may put the daemon's HTTP API under `headwaters.mcp` (FU-TRI-3); HDW-201 must settle which package owns the server before coding. |
| R8 | low | `headwaters/core/state.py` `_enable_wal` (HDW-143) | The WAL retry matches the text "locked" in the error. It fixed the CI race, but there is no deterministic test (FU-143-2). |
| R9 | info | `docs/reports/HDW-125.md`, `HDW-126.md` | Written after the fact; the original sessions' deviation notes were lost. |

## Go / no-go for HDW-201

**GO.**

Both conditions of the first recheck are met. HDW-121 is built and passes its three accept-when items plus probe P7. HDW-143 put the head commit green on windows-latest and ubuntu-latest, Python 3.11 and 3.13. R2 to R9 are not blockers. R3 is the one to carry into HDW-201 itself: every executor and Ollama backend it constructs must receive the catalog's remote patterns.

