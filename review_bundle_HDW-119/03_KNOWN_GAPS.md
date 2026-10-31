# HDW-119: known gaps and caveats

Read this before judging the bundle. Each item says what is missing or odd and what to do about it.

## About the bundle itself

1. **No git history.** There is no baseline, so there is no real diff. Review the snapshot. All of
   Phase 1 is new code, so nothing is lost, but you cannot see how a file changed.
2. **Ticket prefixes differ.** The backlog uses `SPW-nnn`, the packets use `HDW-nnn`. Same tickets.
3. **The design files are the standalone ones.** `design/` holds the standalone documents from
   `headwaters_spec/` (v3 design, v3.1 to v3.5 addenda). The long `headwaters_full_session.md`
   (about 168 KB) contains older copies of the same text as appendices A to G plus the whole chat; it
   is **not** in the bundle because it is mostly duplicate and **does not contain v3.5**. Where a
   standalone file and the appendix differ, the standalone file is newer. Appendix A (the original
   HMR spec) is superseded and is not needed.
4. **The backlog predates the decisions.** Packet and backlog text still mention things D7 to D9
   replaced (FastMCP, the LiteLLM proxy with hooks, sampling, roots). `decisions/DECISIONS.md` wins;
   Deferred tickets (HDW-905) must not be built.
5. **The bundle does not run anything for you.** `evidence/` holds output captured on one Windows
   machine. If you can execute code, these reproduce it from the repo root: `uv run pytest --cov`,
   `uv run lint-imports`, `uv run ruff check .`, `uv run mypy`.

## About the code

6. **Windows only.** Tests were run on Windows 11 with Python 3.13 and `uv`. CI is configured for
   `windows-latest` and `ubuntu-latest`, but the Linux run has not happened. One test is skipped on
   Windows (`tests/test_paths.py:68`, a non-Windows passthrough).
7. **No live tests.** Nothing was run against a real Ollama server, a real NVIDIA GPU or any real
   provider. All HTTP is mocked. Assumption risks R6 (Ollama structured output with `num_gpu: 0`) and
   R14 (NVML display-active) are still owed a `-m live` probe, and the KV/fit math and sysmem-fallback
   detection are verified on formulas and mocked data only.
8. **Catalog trust key is empty.** `core/catalog/trust.py` ships an empty `EMBEDDED_PUBLIC_KEY_B64`
   until the owner runs `scripts/sign_catalog.py keygen`. Until then every downloaded catalog is
   rejected and the bundled seed is used. This is intended, but updates cannot work yet.
9. **The seed catalog is placeholder data.** `catalog/seed_catalog.json` contains four `example-*`
   models and invented speeds, prices and scores (marked `verify: true`). Policy tests run on this
   and on synthetic candidates, not on real model data.
10. **Numbers are first drafts.** Many thresholds are marked first-draft in code and in
    `ASSUMPTIONS.md` (floors, scoring weights, `max_attempts`, the sysmem slow ratio, reserves).
    Judge whether they are in the right place (rules or catalog), not whether the values are right.
11. **A test failed once.** On the first full run in a clean shell,
    `tests/test_import_contract.py::test_deliberate_violation_fails[features]` failed with a
    `UnicodeDecodeError` (cp1252) raised in a pytest reader thread. It passed on every later run.
    `subprocess.run(..., text=True)` there has no explicit encoding. Treat as a probable
    locale-dependent flake and fix it.
12. **Repository hygiene.** `ruff check .` over the whole tree fails on files in `.venv313/`
    (a second virtualenv that `.gitignore` does not cover). The root also holds `.tmp*` files and two
    stray scripts (`model_provisioner_previous.py`, `setup_models_previous.py`) that ruff excludes.
    `docs/ASSUMPTIONS.md` has five lines with LF instead of CRLF endings. None of this affects core.

## Late additions to Phase 1 (ticket 118) that were not in the plan

13. `Router.plan()` and `core/router_plan.py` (a dry run that shares `_select` with the real path),
    `core/candidates.py` (`CandidateBuilder`, database-backed candidate source), `core/rules_edit.py`
    (validated in-place edit of `rules.yaml`) and `state_models.effective_scores`. They are core code
    that other tickets did not ask for. Check that `_select` did not change `handle`'s behaviour
    (every router test passed unchanged).
14. **Deliberately not built (listed in `ASSUMPTIONS.md`):** OpenRouter auto and free-router
    candidates, the premium route, API-key presence checks for paid models, live `/api/ps`
    calibration, and a sysmem-fallback flag. Candidates for the punch list.
15. `models list` changed from plain lines to a table, and one existing test assertion was loosened to
    match (`tests/test_discovery_refresh.py`).

## About the three open decisions

16. The code cannot answer them. Decide on the design, the star/core boundary (v3.5 section B) and
    the cost of each choice; the code facts are in `02_BRIEF.md`.
