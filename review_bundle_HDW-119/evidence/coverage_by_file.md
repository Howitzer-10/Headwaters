# Coverage per file (core only, branch coverage), lowest first

Total: 97.32% (gate: 85%).

| Cover | File | Stmts | Missed | Missing lines |
| ---: | --- | ---: | ---: | --- |
| 86% | `headwaters/core/hardware/gpu.py` | 143 | 18 | 79, 80, 106, 122, 123, 124, 134, 135, 136, 137, 138, 139, 146, 147 ... |
| 88% | `headwaters/core/verify_diff.py` | 51 | 5 | 28, 37, 38, 79, 80 |
| 89% | `headwaters/core/executor/ollama.py` | 44 | 4 | 58, 61, 62, 66 |
| 90% | `headwaters/core/executor/cloud.py` | 51 | 4 | 39, 43, 49, 78 |
| 91% | `headwaters/core/executor/ollama_convert.py` | 104 | 5 | 27, 47, 65, 103, 124 |
| 92% | `headwaters/core/router_prep.py` | 40 | 2 | 37, 43 |
| 93% | `headwaters/core/router.py` | 279 | 17 | 106, 207, 318, 319, 343, 353, 354, 355, 370, 371, 372, 373, 441, 442 ... |
| 93% | `headwaters/core/rules_edit.py` | 67 | 4 | 54, 62, 63, 65 |
| 93% | `headwaters/core/router_session.py` | 45 | 2 | 33, 57 |
| 94% | `headwaters/core/paths.py` | 50 | 2 | 91, 97 |
| 94% | `headwaters/core/analyzer/_extract.py` | 96 | 5 | 18, 19, 51, 53, 118 |
| 94% | `headwaters/core/router_log.py` | 74 | 5 | 113, 114, 115, 146, 147 |
| 95% | `headwaters/core/rules_watch.py` | 93 | 1 | 34 |
| 95% | `headwaters/core/verify_basic.py` | 137 | 8 | 85, 212, 222, 223, 224, 227, 228, 229 |
| 96% | `headwaters/core/secrets_detect.py` | 37 | 1 | 60 |
| 96% | `headwaters/core/verify_fences.py` | 40 | 1 | 43 |
| 97% | `headwaters/core/catalog/updater.py` | 146 | 3 | 117, 180, 188 |
| 97% | `headwaters/core/stdio.py` | 54 | 1 | 28 |
| 97% | `headwaters/core/state_limits.py` | 70 | 1 | 59 |
| 97% | `headwaters/core/walls.py` | 169 | 4 | 78, 89, 189, 190 |
| 98% | `headwaters/core/analyzer/layers.py` | 39 | 1 | 62 |
| 98% | `headwaters/core/executor/cloud_map.py` | 71 | 2 | 52, 53 |
| 98% | `headwaters/core/catalog/loader.py` | 78 | 1 | 121 |
| 98% | `headwaters/core/analyzer/requirements.py` | 157 | 0 |  |
| 98% | `headwaters/core/executor/executor.py` | 112 | 1 | 119 |
| 98% | `headwaters/core/rules_io.py` | 90 | 1 | 129 |
| 98% | `headwaters/core/executor/accumulate.py` | 39 | 0 |  |
| 98% | `headwaters/core/logging.py` | 90 | 1 | 80 |
| 98% | `headwaters/core/catalog/sources.py` | 199 | 2 | 213, 214 |
| 98% | `headwaters/core/catalog/merge.py` | 87 | 1 | 84 |
| 99% | `headwaters/core/verify_schema.py` | 77 | 0 |  |
| 99% | `headwaters/core/quotas.py` | 197 | 0 |  |
| 100% | `headwaters/core/__init__.py` | 0 | 0 |  |
| 100% | `headwaters/core/analyzer/__init__.py` | 4 | 0 |  |
| 100% | `headwaters/core/analyzer/classifier.py` | 116 | 0 |  |
| 100% | `headwaters/core/analyzer/ollama_client.py` | 38 | 0 |  |
| 100% | `headwaters/core/candidates.py` | 54 | 0 |  |
| 100% | `headwaters/core/catalog/__init__.py` | 0 | 0 |  |
| 100% | `headwaters/core/catalog/discovery.py` | 90 | 0 |  |
| 100% | `headwaters/core/catalog/http.py` | 53 | 0 |  |
| 100% | `headwaters/core/catalog/schema.py` | 180 | 0 |  |
| 100% | `headwaters/core/catalog/trust.py` | 19 | 0 |  |
| 100% | `headwaters/core/executor/__init__.py` | 0 | 0 |  |
| 100% | `headwaters/core/executor/errors.py` | 28 | 0 |  |
| 100% | `headwaters/core/executor/guard.py` | 37 | 0 |  |
| 100% | `headwaters/core/executor/types.py` | 80 | 0 |  |
| 100% | `headwaters/core/features.py` | 96 | 0 |  |
| 100% | `headwaters/core/hardware/__init__.py` | 0 | 0 |  |
| 100% | `headwaters/core/hardware/fit.py` | 91 | 0 |  |
| 100% | `headwaters/core/hardware/residency.py` | 29 | 0 |  |
| 100% | `headwaters/core/hardware/state.py` | 171 | 0 |  |
| 100% | `headwaters/core/hardware/sysmem.py` | 45 | 0 |  |
| 100% | `headwaters/core/plugins.py` | 44 | 0 |  |
| 100% | `headwaters/core/policy/__init__.py` | 0 | 0 |  |
| 100% | `headwaters/core/policy/decide.py` | 51 | 0 |  |
| 100% | `headwaters/core/policy/errors.py` | 17 | 0 |  |
| 100% | `headwaters/core/policy/filters.py` | 79 | 0 |  |
| 100% | `headwaters/core/policy/ladder.py` | 76 | 0 |  |
| 100% | `headwaters/core/policy/scoring.py` | 87 | 0 |  |
| 100% | `headwaters/core/policy/types.py` | 41 | 0 |  |
| 100% | `headwaters/core/project_config.py` | 38 | 0 |  |
| 100% | `headwaters/core/router_plan.py` | 17 | 0 |  |
| 100% | `headwaters/core/router_run.py` | 27 | 0 |  |
| 100% | `headwaters/core/router_types.py` | 87 | 0 |  |
| 100% | `headwaters/core/router_verify.py` | 41 | 0 |  |
| 100% | `headwaters/core/rules.py` | 165 | 0 |  |
| 100% | `headwaters/core/scope.py` | 113 | 0 |  |
| 100% | `headwaters/core/state.py` | 81 | 0 |  |
| 100% | `headwaters/core/state_decisions.py` | 64 | 0 |  |
| 100% | `headwaters/core/state_misc.py` | 74 | 0 |  |
| 100% | `headwaters/core/state_models.py` | 80 | 0 |  |
| 100% | `headwaters/core/state_schema.py` | 3 | 0 |  |
| 100% | `headwaters/core/verify_types.py` | 53 | 0 |  |
