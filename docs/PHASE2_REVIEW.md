# Phase 2 review (HDW-201 to HDW-208)

Reviewer: Cline session, 2026-11-01. Evidence is from this machine (Windows 11) and from CI run
41294994373 on commit `7c0f050`. No Opus session has reviewed the Phase 2 diff yet; this is the
evidence a gate session should start from.

## Verdict

**GO for Phase 3, after HDW-148.** Nothing found blocks the gateway except that it should be built on
the engine-rebuild hook (HDW-148) rather than on a second startup reading of the rules.

## Checks run

| Check | Result |
| --- | --- |
| `pytest` (full suite) | 1670 passed, 6 skipped, 201 s (Windows) |
| `ruff check`, `ruff format --check` | clean, 472 files |
| `mypy` | no issues in 307 files |
| `lint-imports` | 7 contracts kept, 0 broken (the `mcp` and web-framework contracts now have real code to check, which closes recheck risk R7) |
| CI, first push `29f3a32` | windows 3.11 and 3.13 green; **ubuntu 3.11, 3.13 and 3.14 failed** |
| CI, fix `7c0f050`, run 41294994373 | all five jobs green |

## Problems found

| ID | Severity | Where | Finding | Outcome |
| --- | --- | --- | --- | --- |
| P2-1 | high | `headwaters/core/daemon_control.py` `_pid_alive` | On Linux a daemon the caller started stays a zombie until reaped, and `psutil.pid_exists` is still true for it, so `stop_daemon` reported "still running". Two live tests failed on every ubuntu job. Windows has no zombies, so the Windows-only runs of HDW-201 and HDW-202 hid it (FU-201-7, FU-202-4 had asked for exactly this check). | Fixed in `7c0f050`: a zombie counts as gone; two tests fake the zombie and vanishing cases so Windows CI covers the logic. |
| P2-2 | medium | `headwaters/core/hardware/state.py:150-155` | With nothing but Ollama's model on the GPU, the Windows fallback counts all used VRAM as another app's, so GPU-busy is reported and keep-warm skips (FU-201-2). Read from code only; not reproduced on a GPU. | HDW-144 |
| P2-3 | medium | daemon lifetime under real hosts | A daemon started by the shim can be reaped when the first host closes, if the host runs the shim in a kill-on-close job (FU-202-1). Only the SDK's own stdio client was tried. | HDW-145 |
| P2-4 | medium | whole phase | Every Phase 2 test uses mocks or the SDK's in-process client. No real host, no real model, no real thinking model. | HDW-145 |
| P2-5 | medium | `headwaters/mcp/shim.py`, `link.py` | The shim does not forward elicitation or cancellation, so spend and scope confirmations always take the text fallback through a shim (FU-205-1, FU-206-1). | HDW-146 |
| P2-6 | medium | `headwaters/core/engine.py:153-160` | One startup reading of the rules feeds the candidate builder, hardware, feature flags and target builder; edits to paid models, the local host, features and endpoints need a restart (FU-131-1). Airplane and Private lock are safe (read per request). | HDW-148, before HDW-301 |
| P2-7 | low | `docs/reports/HDW-208.md` | `Commit: uncommitted` although committed as `949ad02`. | Fixed |
| P2-8 | low | backlog | No Phase 2 review-gate ticket existed. | This document; the Opus gate session is still to run |

## Carried-over risks from the Phase 1 recheck

| Risk | Status |
| --- | --- |
| R2 Private lock plus local scope gave a cloud hint | closed: HDW-128 (`70e0dfd`) |
| R3 executors built without `remote_patterns` | **closed**: `build_engine` passes `remote_patterns_from(catalog)`; `Executor` hands the same patterns to `OllamaBackend`; `tests/test_engine.py:123` checks both; no other construction site exists in `headwaters/` |
| R4 stale `local` row for a cloud model | open, covered at call time by the executor; the candidate-build check is HDW-153 |
| R5 older clients reject `ollama_remote_patterns` | merged into HDW-407 |
| R7 mcp / web contracts pass vacuously | closed: both bite now |

## Prerequisite order

The "before HDW-20x" tickets landed in time: HDW-131 and HDW-127 before HDW-202; HDW-129, HDW-130,
HDW-128 and HDW-140 before HDW-204 and HDW-205. HDW-142 (joined and encoded secrets) is still
unbuilt and must land before HDW-306.

## Not checked

- Behaviour on macOS.
- Any real host, real GPU or real model (HDW-145 and HDW-144).
- A line-by-line review of the Phase 2 diff against the design; that is the Opus gate session.
