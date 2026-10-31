# HDW-119 recheck

- Date: 2026-10-31
- Checked tree: `C:\Projects\Headwaters` at `7a277b8` (clean working tree; the code was last changed in `8d0728e`)
- Scope: sections 1, 4 and 8 of `docs/HDW-119_REVIEW.md` (identical to `review_bundle_HDW-119/HDW-119_REVIEW.md`, same SHA-256). Blockers HDW-120 to HDW-126, with HDW-121 checked on its own.
- Nothing was fixed. The two throwaway probe scripts I ran (P3 and P7 in one, P8 in the other) lived in `%TEMP%` and are deleted.
- Two untracked files appeared in `docs/` during this check, `ollama_tags_sample.json` and `ollama_tags_sample_cloud.json` (written 15:32 and 15:34). I did not create them and did not commit or change them. They look like a live `/api/tags` capture from this machine and are used as evidence in R1 and R5.

## Verdict: NO-GO for HDW-201

HDW-121 (F2, a blocker) was never built. A cloud-proxied Ollama model is still stored as `tier=local`, `retains_data=False`, and the executor guard has no remote-model check, so a `headwaters/local` or Private-lock request can still leave the machine. The other six blockers pass, with one open accept-when item (see HDW-120). Details and a second condition (CI) are at the end.

## Full run

| Check | Result |
| --- | --- |
| `uv run pytest -q --no-cov -rs` | 955 passed, 0 failed, 1 skipped (`tests/test_paths.py:68`, non-Windows passthrough), 94.8 s |
| `uv run lint-imports` | 4 contracts kept, 0 broken |
| `uv run ruff check .` | All checks passed |
| `uv run mypy` | no issues in 172 source files |
| Regression tests named below, re-run on their own | 35 passed, plus 7 golden rows passed |

CI has not run on these commits (see finding R2), so these are local Windows results only.

## Per ticket

| Ticket | Result | Evidence |
| --- | --- | --- |
| HDW-120 secret scan | **PASS, one item partial** | Shapes: `tests/test_router_secret_parts.py:64` `test_every_shape_is_detected` (user text, `tool_result`, OpenAI `tool_calls` arguments, Anthropic `tool_use` input). Routes local without network: `:103` `test_a_secret_only_in_a_part_routes_local_without_network` (asserts local model, `rig.cloud_clients == 0`, no refused connect). Code: `headwaters/core/analyzer/_extract.py:13,48,109-122` (`MAX_LEAF_DEPTH`, `_leaves`, `collect`), `router_prep.py:50`. **Partial:** "no secret text in reasons or rows" is asserted for `result.reasons`, `result.decision` and `result.attempts` (`:112-114`), but the stored decision and outcome rows are never read back from SQLite. Already tracked as FU-120-1, merged into HDW-306. |
| HDW-121 Ollama cloud models never local | **FAIL, not built** | No commit mentions HDW-121 and `docs/reports/HDW-121.md` does not exist. `headwaters/core/catalog/sources.py` `ollama_models` still builds `LiveModel(tier="local", retains_data=False)` for every tag (no remote check). `headwaters/core/executor/guard.py:36-53` `enforce_scope` checks tier, scope, airplane and loopback URL only. There is no `ollama_remote_patterns` in `catalog/seed_catalog.json` or the schema, and nothing reads `OLLAMA_NO_CLOUD`. `docs/ASSUMPTIONS.md:98` A-OLL-9 exists but is the reviewer's note ("no live `/api/tags` probe yet"), not the ticket's verified record. No test uses a `:cloud`/`-cloud` model. |
| HDW-122 credit walls | **PASS** | `headwaters/core/quotas.py:78,84` `credit_class` / `credit_subject`; `:211,378` `until = now + exhausted_recheck_s`; `headwaters/core/rules.py:154` the rule; `headwaters/core/router.py:308,349-355` key alias and 429 counting. Tests: `tests/test_router_walls.py:77` `test_p9_paid_402_leaves_the_free_openrouter_model_ok_and_the_wall_lifts`, `:99` the paid model is tried again; `tests/test_quotas.py:177` recheck after the configured time, `:342` `test_p9_paid_402_leaves_free_models_of_the_same_provider_ok`. All of `tests/test_quotas.py` passes. |
| HDW-124 import contract | **PASS** | `.importlinter`: `headwaters.cli` is in the core contract, plus contracts for litellm, mcp and fastapi/starlette; `lint-imports` 4 kept, 0 broken. Deliberate violations fail: `tests/test_import_contract.py:68` `test_core_importing_a_forbidden_package_fails` (6 targets) and `:89` `test_external_package_violations_fail`; allowed imports pass at `:103`. Source scan: `tests/test_litellm_guard.py:70` `test_litellm_only_referenced_in_cloud_modules`. `sys.modules`: `:116-117` `test_importing_does_not_load_litellm` for `headwaters.core.router` and `headwaters.cli.main`. Telemetry and cost map: `:129`, plus the CI step at `.github/workflows/ci.yml:28`. The `mcp`, `fastapi` and `starlette` contracts pass vacuously (none of the three is installed yet); tracked as FU-124-1. |
| HDW-125 subprocess decoding | **PASS** | `headwaters/core/hardware/gpu.py:151-153` (`encoding="utf-8"`, `errors="replace"`, `PYTHONIOENCODING`); `tests/test_import_contract.py` `_lint_imports` decodes as UTF-8. `tests/test_hardware_probes.py:141` `test_smi_parses_non_ascii_process_name` and `:149` `test_run_nvidia_smi_decodes_utf8_regardless_of_code_page` pass. Three consecutive full runs in a cp1252 console (`locale.getpreferredencoding(False)` = `cp1252`, `PYTHONIOENCODING` and `PYTHONUTF8` unset) gave 955 passed, 1 skipped each (92.9 s, 88.9 s, 94.0 s), recorded in `docs/reports/HDW-125.md`. |
| HDW-126 no `_example` in production | **PASS** | `headwaters/features/_example` no longer exists; `tests/fixtures/features/_example/__init__.py` does. `tests/test_features_discovery.py:26` `test_production_discovery_registers_no_example`; the contract harness still runs against it through `tests/contracts/test_example_contracts.py:11` (`discover_features(_registry, "tests.fixtures.features")`) and `tests/test_features_discovery.py:45`. Live probe: `discover_features(FeatureRegistry())` returns `[]`, with no features and no verifier plugins; `headwaters/cli/runtime.py` still builds its registry with `discover_features(registry)`. |
| HDW-123 local scope below the floor | **PASS** | `headwaters/core/policy/ladder.py:23,38` (`is_local_only`, below-floor suggestions); `decide.py:75`; no `local_below_floor` is raised anywhere in `headwaters/`. Analyzer: no `int(agentic)` in `analyzer/requirements.py` (comment at `:201`); fixtures `tests/analyzer_fixtures_a.py:93,130` assert complexity 1. Golden rows `tests/test_policy_golden_b.py:64` `agentic-local-scope`, `:78` `agentic-local-scope-p2`, `:92` `scope-local-too-hard`; `tests/test_policy_golden_c.py:128` `test_local_below_floor_is_never_raised`, `:138` `test_no_golden_row_leaves_the_local_tier_in_local_scope`. CLI: `headwaters/cli/pick_report.py:94` Warning line, `tests/test_cli_pick.py:84` `test_pick_scope_local_below_floor_shows_the_warning`. |


## Probes

Each probe is the failing case from the review's findings table (section 4). "Test" means a test now in the repo that fails if the defect comes back.

| Probe | Finding | Result | Evidence |
| --- | --- | --- | --- |
| P1 secret in tool calls | F1 | **PASS, regression test exists** | `tests/test_router_secret_parts.py:64`: all four shapes (user text, `tool_result`, `tool_calls.arguments`, `tool_use.input`) are detected. |
| P2 agentic request in local scope | F4 | **PASS, regression test exists** | `tests/test_policy_golden_b.py:78` `agentic-local-scope-p2` (local model code 88 / agentic 85, scope local): served as `best_effort`, `below_floor=True`, `verify_required=True`. Passes. |
| P3 Private lock plus local scope gives a cloud hint | F9 | **FAIL, still open, no regression test** | This probe belongs to HDW-128 (Before HDW-205), not to a blocker, so it is not required for HDW-201. Live run: with `private_lock: true`, `resolve_scope(requested=None)` and `resolve_scope(requested="local")` both give `locks=()` and `local_only=True`. `headwaters/core/policy/ladder.py:41,44,80` key the cloud hint on `req.scope.locks`, so a local-only pick below the floor gets a `use_cloud` suggestion while Private Mode is on. `tests/test_policy_golden_b.py` row `private-lock-battery-no-cloud-hint` still injects `locks` by hand, and `tests/test_scope.py:200` only covers the case where the lock narrowed the request. |
| P7 `gpt-oss:120b-cloud` in `/api/tags` | F2 | **FAIL, still open, no regression test** | Live run through `sources.ollama_models` with a fake `/api/tags` (`gpt-oss:120b-cloud`, `remote_host` set): `tier=local retains_data=False`, the same as the review's result. No test uses a cloud-tagged model. |
| P8 `_example` registered in production | F7 | **PASS, regression test exists** | `tests/test_features_discovery.py:26` `test_production_discovery_registers_no_example`; live run returns `[]` and no verifier plugins. |
| P9 paid 402 blocks `:free` OpenRouter models | F3 | **PASS, regression test exists** | `tests/test_router_walls.py:77` and `tests/test_quotas.py:342`. Both pass. |

Summary: P1, P2, P8 and P9 are regression tests and pass. P7 (HDW-121) and P3 (HDW-128) still reproduce and have no test.

## Findings (remaining problems; none fixed)

| ID | Severity | Where | Finding |
| --- | --- | --- | --- |
| R1 | blocker | HDW-121, `headwaters/core/catalog/sources.py` `ollama_models`; `headwaters/core/executor/guard.py:36-53` | Review finding F2 is unresolved. A cloud-proxied Ollama model (`:cloud`/`-cloud`, or a `remote_host` field in `/api/tags`) is stored as `tier=local`, `retains_data=False`. The loopback assertion passes because the hop to ollama.com happens inside Ollama, so a `headwaters/local` or Private-lock request can leave the machine. D13 (owner, 2026-10-31) says these models are never routed and discovery drops them; none of that is built. Nothing in the tree reads `OLLAMA_NO_CLOUD` or has `ollama_remote_patterns`. The review's own probe P7 reproduces today. The captured `docs/ollama_tags_sample_cloud.json` shows the real shape: `gpt-oss:120b-cloud` has `remote_host: "https://ollama.com"` and `remote_model: "gpt-oss:120b"`, and `size` is 307 bytes. The 16 local models in the same capture have neither field and a multi-GB size. My P7 probe used a faked `remote_host` on the same name, and the result would be the same for the real file, because `ollama_models` never reads those fields. |
| R2 | high | `.github/workflows/ci.yml:5` | CI runs on pushes to `main` only, but the repo's branch is `master`, so none of the Phase 1.5 work has run on ubuntu-latest or on Python 3.11. Every "tests must pass on windows-latest and ubuntu-latest" claim is unverified. Tracked as HDW-143 (Before HDW-201). |
| R3 | medium | HDW-120 accept-when, `tests/test_router_secret_parts.py:112-114` | "No secret text appears in rows" is only asserted on in-memory objects, not on the SQLite decision and outcome rows. Tracked as FU-120-1, merged into HDW-306. |
| R4 | medium | HDW-128, `headwaters/core/scope.py:91,272`, `ladder.py:41,44,80` | P3 / F9 (see Probes). Before HDW-205, so not a Phase 2 start blocker, but it does mean the cloud hint contradicts Private Mode today. |
| R5 | low | `docs/ASSUMPTIONS.md:98` A-OLL-9 | The row is the reviewer's note and says "no live `/api/tags` probe yet". HDW-121's own accept-when needs the assumption recorded with a verified source and date. The capture in `docs/ollama_tags_sample_cloud.json` is the live probe it asks for (it shows `remote_host`/`remote_model` for the cloud entry), but nothing in the tree records or uses it yet. |
| R6 | low | contracts `mcp-sdk-only-in-mcp`, `web-framework-only-in-gateway` | They pass because no module imports those packages. Whether they bite is unproven until HDW-201 and HDW-202 (FU-124-1), and the layout may put the daemon's HTTP API under `headwaters.mcp` (FU-TRI-3). |
| R7 | info | `docs/reports/HDW-125.md`, `docs/reports/HDW-126.md` | These reports were written after the fact (2026-10-31); the original sessions' notes on deviations were lost. |

## Go / no-go for HDW-201

**NO-GO.**

1. **Required: build HDW-121.** It is a Blocks-Phase-2 ticket and a privacy defect (R1). When it is done, this recheck item passes if: a fixture `/api/tags` with a cloud model never yields a candidate (D13: dropped), a Private-lock request that names it is refused, `docs/ASSUMPTIONS.md` records the verified behaviour with a source and date, and a P7 regression test exists.
2. **Required before HDW-201 per the backlog: HDW-143** (R2), so the first run on ubuntu-latest happens before the daemon work starts and not after.

Every other Blocks-Phase-2 ticket (HDW-120, 122, 123, 124, 125, 126) passes its accept-when, apart from the one HDW-120 item in R3, which is carried by HDW-306 and does not block HDW-201.
