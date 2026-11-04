# Integration merge order (howitzer-10-integration)

The owner approved one order for landing eight candidate branches on `master`. This is the
record of that run: the branch was cut from `36cafaa` (master: `101593e` doctor-host-probe and
`36cafaa` models-refresh are already in it and were not reverted), every candidate was cut from
`00ca8be`, and each step was merged with `git merge --no-ff -X no-renames <sha>` from
`howitzer-10-integration`. No candidate branch was checked out, rebased, fast-forwarded or moved;
the integration branch moved and nothing else.

## Sequence

| # | Source | What it is | Merge commit |
| --- | --- | --- | --- |
| 1 | `c78501e` | HDW-514 + HDW-515 + HDW-503 provider stack (registry, OpenCode, keys dashboard) | `f55f3ca` |
| 2 | `ad8fec9` | HDW-513 + HDW-516 cascade failure memory and descending tier order (tip of two branch names) | `a639638` |
| 3 | `c25e4ae` | HDW-511 tool cost diagnosis and prune-to-fit | `9a0f183` |
| 4 | `1ceda08` | HDW-512 observed `tools_ok`, `models.overrides`, opt-in answer footer | `f41ef0a` |
| 5 | `94922ea` | Routing / cascade / keep-warm ticket packets (`d228a07`, `cd4076b`) | `19308c4` |
| 6 | `fbccdab` | VRAMWeave reference doc and D19 (`69e8789` paid vocabulary) | `0944dd7` |
| 7 | `a3e0a0c` | Consolidated DECISIONS D1-D20 (`e4d1de0`) | `e2ae98a` |

## Conflicted files and how each was resolved

### Step 1 — `c78501e`

| File | Resolution |
| --- | --- |
| `headwaters/core/catalog/discovery.py` | Kept both behaviours. `refresh_models`/`_gather` now take master's `budget_s`, `progress`, `_Budget`/`_read_source` machinery **and** the provider path (`providers=`, `key_for=`, keyword-only); the `provider:<name>` loop goes through `_read_source` with `partial(registry_models, http, entry, key)`, so a provider earns no exemption from the 20 s refresh budget. |
| `headwaters/core/catalog/http.py` | Kept master's `LOOPBACK_IPV4` / `canonical_loopback_url` and the shared `USER_AGENT` from `headwaters.core.user_agent`; the provider registry keeps using that one client. |

### Step 2 — `ad8fec9`

| File | Resolution |
| --- | --- |
| `docs/SDK_FACTS.md` | Union: master's sections plus the HDW-513 provider-authentication section; nothing removed, nothing renumbered. |
| `docs/ASSUMPTIONS.md` | Union of rows in date order (HDW-513 on 2026-11-11 before HDW-503 on 2026-11-12). |
| `tests/test_status_page.py` | Kept both sides' imports and tests (`STANDARD_ENV`/`KeyStore` and `RouterRules`). |

Not on the pre-measured checklist: `docs/SDK_FACTS.md` and `docs/ASSUMPTIONS.md` also conflicted
here (both steps 1 and 2 append rows/sections).

### Step 3 — `c25e4ae`

| File | Resolution |
| --- | --- |
| `docs/ASSUMPTIONS.md` | Union of rows in date order (HDW-511 before HDW-513). |
| `tests/router_rig.py` | Kept both constructor kwargs (`resolve` and `candidates_for`). |

### Step 4 — `1ceda08`

| File | Resolution |
| --- | --- |
| `catalog/rules.schema.json` | Kept both `$defs` entries (`ToolsRules` from HDW-511, `ToolProbeRules` from HDW-512), then regenerated with `python -m headwaters.core.rules_io --write`; the generator reproduces exactly this union, and `models.tool_probe` and root `tools` both validate. |
| `headwaters/core/state_schema.py` | Two migrations both claimed number 6. Master's `key_tests` (HDW-503) keeps 6; the incoming `capability_probes` (HDW-512) became 7. `LATEST_VERSION` is derived, so it reports 7 and a fresh DB gets both tables. |
| `headwaters/cli/cmd_models.py` | `refresh` keeps master's `progress` callback **and** binds `db`, which the incoming verification path needs; `refresh_models(db, rules, catalog, progress=progress)`. |
| `docs/ASSUMPTIONS.md` | Union of rows in date order (HDW-516, then HDW-512 on 2026-11-03). |
| `docs/DECISIONS.md` | Both branches claimed D17. HDW-513's retry rule keeps D17 (later commit, `ad8fec9` 18:21 vs `1ceda08` 16:08, and that is the number the final consolidation gives it); HDW-512's behaviour-over-advertisement takes D20, its number in step 7's file, and its detail section is retitled `## D20 detail`. The stale numbering note was replaced with one that describes the merged file. |

### Step 5 — `94922ea`

| File | Resolution |
| --- | --- |
| `docs/DECISIONS.md` | Took our side (D18 + D20 + corrected note) and dropped the incoming note, which only restated the pre-merge D17 ambiguity that this branch had already resolved; no decision text was dropped. |
| `headwaters_ticket_packets/00_INDEX.md` | Union of rows, ascending ticket number: HDW-512 row stays, then HDW-514, 516, 517, 518, 519. |

### Step 6 — `fbccdab`

| File | Resolution |
| --- | --- |
| `docs/DECISIONS.md` | One `D18` row only, using our side's wording, which is byte-identical to step 7's consolidation; the incoming D19 row is inserted between D18 and D20. `docs/REFERENCE_VRAMWEAVE.md` landed, which closes the FU-520-3 precondition. |

`headwaters_ticket_packets/00_INDEX.md` and the rest were clean against step 5, as predicted.

### Step 7 — `a3e0a0c`

| File | Resolution |
| --- | --- |
| `docs/DECISIONS.md` | Took the consolidation for everything: its D16 and D17 table rows (both were detail-only before), its numbering note, and its normalisation of two blank-line hunks. The result contains D1 through D20 exactly once each (verified by parsing the table), and a diff against the pre-merge file shows only additions plus the note swap. |

## Verification (one pass, after the last merge)

| Command | Result |
| --- | --- |
| `ruff check` on the six hand-resolved Python files | All checks passed |
| `ruff format --check` on the same six | 6 files already formatted |
| `lint-imports` | 7 contracts kept, 0 broken |
| `python -m pytest --collect-only -q` | 2655/2663 collected, 8 deselected, exit 0 |
| Targeted `pytest` on the 19 files covering the hand-resolved modules | 356 passed |

The targeted set was `test_discovery_refresh`, `test_discovery_sources`, `test_status_page`,
`test_cli_status`, `test_hdw514_providers`, `test_hdw514_registry`, `test_hdw514_keys_cli`,
`test_hdw515_opencode`, `test_cli_models`, `test_hdw512_tool_probe`, `test_hdw512_overrides`,
`test_state`, `test_state_records`, `test_router_failures`, `test_policy_order`,
`test_policy_scoring`, `test_router_tool_cost`, `test_release_docs`, `test_import_contract`.

## Reviewer double-checks

1. **`discovery.refresh_models` signature.** The provider loop and the budget interact: a provider
   read is bounded by `_read_source` and reports progress like an HTTP source. Confirm a slow
   provider cannot hang a refresh, and that `daemon/service.py` (the other caller) still matches the
   keyword-only signature.
2. **Migration numbering.** Anything already carrying the HDW-512 branch's `capability_probes` at
   version 6 would see it as `key_tests`. Only scratch databases are affected; `key_tests` wins 6
   because it was merged first.
3. **`catalog/rules.schema.json`** must stay generator-clean: `python -m headwaters.core.rules_io
   --write` and `git diff` should be empty.
4. **`docs/DECISIONS.md`** is the consolidation's text, not a three-way blend. If a later branch
   expects the old detail sections for D10-D14, they were already gone before this run.
5. The unticketed `.env` key-name defect (`POLLINATIONS_KEY` / `MISTRAL_KEY` vs
   `POLLINATIONS_API_KEY` / `MISTRAL_API_KEY`) is untouched and out of scope here.
