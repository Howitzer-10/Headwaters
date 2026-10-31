# Mechanical checks (leads, not verdicts)

Regex over headwaters/core. Each hit is a lead to confirm or dismiss. A clean result proves little; a hit alone proves nothing.

## Network-capable imports

Where core can reach the network. Check each is behind the scope guard or loopback assertion, or is a catalog/cloud call that never runs in local scope.

10 hit(s).

```
headwaters/core/analyzer/ollama_client.py:12: import urllib.error
headwaters/core/analyzer/ollama_client.py:13: import urllib.request
headwaters/core/analyzer/ollama_client.py:16: from urllib.parse import urlparse
headwaters/core/catalog/http.py:11: import urllib.error
headwaters/core/catalog/http.py:12: import urllib.request
headwaters/core/catalog/http.py:14: from urllib.parse import urlparse
headwaters/core/catalog/updater.py:17: import urllib.error
headwaters/core/catalog/updater.py:18: import urllib.request
headwaters/core/executor/guard.py:10: from urllib.parse import urlparse
headwaters/core/executor/ollama.py:13: import httpx
```

## litellm references

Decision D7: all litellm use stays inside core/executor, loaded lazily so a local-scope request never builds a cloud client.

14 hit(s).

```
headwaters/core/executor/cloud.py:3: litellm is imported lazily, on the first cloud call only. A local-scoped request never gets here,
headwaters/core/executor/cloud.py:4: so no cloud client is ever constructed for it. All litellm use stays in this module.
headwaters/core/executor/cloud.py:19: def _load_litellm() -> Any:
headwaters/core/executor/cloud.py:22: module = importlib.import_module("litellm")
headwaters/core/executor/cloud.py:23: setattr(module, "telemetry", False)  # noqa: B010 - litellm ships no type stubs for it
headwaters/core/executor/cloud.py:65: def _litellm(self) -> Any:
headwaters/core/executor/cloud.py:67: self._module = _load_litellm()
headwaters/core/executor/cloud.py:72: litellm = self._litellm()
headwaters/core/executor/cloud.py:74: response = await litellm.acompletion(**kwargs)
headwaters/core/executor/cloud.py:79: except Exception as exc:  # litellm raises its own exception tree
headwaters/core/executor/cloud_map.py:3: Pure functions over duck-typed litellm objects, so they are tested without importing litellm.
headwaters/core/executor/cloud_map.py:32: through ``extra_body``; everything else through litellm's ``reasoning_effort``."""
headwaters/core/executor/executor.py:4: litellm SDK, local through Ollama's native ``/api/chat``. Streaming exposes a ``first_token``
headwaters/core/router.py:8: 4. execute          cloud through litellm, local through Ollama
```

## Provider names in code (not comments)

Provider names in logic may be hard-coded routing facts that belong in the catalog.

115 hit(s). First 90 shown.

```
headwaters/core/analyzer/classifier.py:3: Runs only when the earlier layers left the result ambiguous. One Ollama native ``/api/chat``
headwaters/core/analyzer/classifier.py:9: Privacy: the call goes to a loopback Ollama only (``assert_loopback``), so it is identical in
headwaters/core/analyzer/classifier.py:25: from headwaters.core.analyzer.ollama_client import (
headwaters/core/analyzer/classifier.py:27: OllamaChatClient,
headwaters/core/analyzer/classifier.py:28: OllamaHttpClient,
headwaters/core/analyzer/classifier.py:122: client: OllamaChatClient | None = None,
headwaters/core/analyzer/classifier.py:129: self._client: OllamaChatClient = client or OllamaHttpClient(host)
headwaters/core/analyzer/layers.py:15: from headwaters.core.analyzer.ollama_client import OllamaChatClient
headwaters/core/analyzer/layers.py:70: client: OllamaChatClient | None = None,
headwaters/core/analyzer/ollama_client.py:5: replace it; layer 3 only depends on the ``OllamaChatClient`` protocol.
headwaters/core/analyzer/ollama_client.py:25: class OllamaChatClient(Protocol):
headwaters/core/analyzer/ollama_client.py:44: class OllamaHttpClient:
headwaters/core/analyzer/requirements.py:78: ``messages`` follow OpenAI chat or Anthropic message shapes (content is a string or a list
headwaters/core/analyzer/requirements.py:79: of parts). ``system`` is the Anthropic top-level system prompt (string or text blocks);
headwaters/core/candidates.py:9: Ollama held at the last refresh.
headwaters/core/candidates.py:13: Not covered yet (engines add them): OpenRouter auto/free router routes and the premium route,
headwaters/core/candidates.py:47: ``ModelRow.size_gb`` is GiB (discovery divides Ollama's bytes by 1024^3) while
headwaters/core/candidates.py:56: provider=row.provider or "ollama",
headwaters/core/catalog/discovery.py:4: and its models are simply not refreshed (they are NOT deleted, so a brief Ollama outage does
headwaters/core/catalog/discovery.py:6: Offline modes: ``airplane`` and the local-only lock block every non-loopback source; Ollama
headwaters/core/catalog/discovery.py:24: ollama_models,
headwaters/core/catalog/discovery.py:26: opencode_config_paths,
headwaters/core/catalog/discovery.py:27: opencode_models,
headwaters/core/catalog/discovery.py:28: openrouter_models,
headwaters/core/catalog/discovery.py:36: OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
headwaters/core/catalog/discovery.py:72: models, warnings = ollama_models(http, rules.tiers.local.host)
headwaters/core/catalog/discovery.py:73: found["ollama"] = models
headwaters/core/catalog/discovery.py:76: result.warnings.append(f"ollama: {exc}")
headwaters/core/catalog/discovery.py:78: paths_ = opencode_config_paths(rules.models.opencode_config, env, home)
headwaters/core/catalog/discovery.py:79: oc, oc_warn = opencode_models(paths_, rules.tiers.free_cloud.opencode_free)
headwaters/core/catalog/discovery.py:80: found["opencode"] = oc
headwaters/core/catalog/discovery.py:88: found["openrouter"] = openrouter_models(http, env.get(OPENROUTER_KEY_ENV))
headwaters/core/catalog/discovery.py:90: result.warnings.append(f"openrouter: {exc}")
headwaters/core/catalog/sources.py:3: Nothing here sends prompt data. Ollama uses the NATIVE API only (/api/tags, /api/show, /api/ps)
headwaters/core/catalog/sources.py:4: and refuses a non-loopback host (A-OLL-8). OpenRouter attribution headers are not sent (A-OR-10).
headwaters/core/catalog/sources.py:21: OPENROUTER_BASE = "https://openrouter.ai/api/v1"
headwaters/core/catalog/sources.py:86: def ollama_models(http: HttpClient, host: str) -> tuple[list[LiveModel], list[str]]:
headwaters/core/catalog/sources.py:89: raise SourceError(f"Ollama host {host!r} is not loopback; local discovery refuses it")
headwaters/core/catalog/sources.py:102: provider="ollama",
headwaters/core/catalog/sources.py:103: source="ollama",
headwaters/core/catalog/sources.py:110: warnings.append(f"ollama: /api/show failed for {name}: {exc}")
headwaters/core/catalog/sources.py:126: warnings.append(f"ollama: /api/ps failed (warm state unknown): {exc}")
headwaters/core/catalog/sources.py:144: def openrouter_models(http: HttpClient, api_key: str | None = None) -> list[LiveModel]:
headwaters/core/catalog/sources.py:147: payload = _as_dict(http.request_json("GET", f"{OPENROUTER_BASE}/models", headers=headers))
headwaters/core/catalog/sources.py:166: provider="openrouter",
headwaters/core/catalog/sources.py:167: source="openrouter",
headwaters/core/catalog/sources.py:181: class OpenRouterKeyInfo:
headwaters/core/catalog/sources.py:188: def openrouter_key(http: HttpClient, api_key: str) -> OpenRouterKeyInfo:
headwaters/core/catalog/sources.py:192: _as_dict(http.request_json("GET", f"{OPENROUTER_BASE}/key", headers=headers)).get("data")
headwaters/core/catalog/sources.py:196: return OpenRouterKeyInfo(
headwaters/core/catalog/sources.py:233: def opencode_config_paths(explicit: str | None, env: Mapping[str, str], home: Path) -> list[Path]:
headwaters/core/catalog/sources.py:237: found = [home / ".config" / "opencode" / name for name in ("opencode.json", "opencode.jsonc")]
headwaters/core/catalog/sources.py:238: if env.get("OPENCODE_CONFIG"):
headwaters/core/catalog/sources.py:239: found.append(Path(env["OPENCODE_CONFIG"]))
headwaters/core/catalog/sources.py:243: def opencode_models(
headwaters/core/catalog/sources.py:249: user list decide what is free. Free OpenCode endpoints may retain data (A-OC-4).
headwaters/core/catalog/sources.py:259: warnings.append(f"opencode: cannot read {path}: {exc.strerror or exc}")
headwaters/core/catalog/sources.py:264: warnings.append(f"opencode: {path} is not valid JSON; skipped")
headwaters/core/catalog/sources.py:266: provider = _as_dict(_as_dict(config.get("provider")).get("opencode"))
headwaters/core/catalog/sources.py:270: model_id = name if name.startswith("opencode/") else f"opencode/{name}"
headwaters/core/catalog/sources.py:276: provider="opencode",
headwaters/core/catalog/sources.py:277: source="opencode",
headwaters/core/catalog/sources.py:281: match_names=[name.removeprefix("opencode/")],
headwaters/core/executor/cloud_map.py:15: OPENROUTER = "openrouter"
headwaters/core/executor/cloud_map.py:24: because Headwaters promises no telemetry. ``X-Title`` is still accepted by OpenRouter."""
headwaters/core/executor/cloud_map.py:27: return {"HTTP-Referer": attribution.referer, "X-OpenRouter-Title": attribution.title}
headwaters/core/executor/cloud_map.py:35: if provider == OPENROUTER:
headwaters/core/executor/cloud_map.py:90: if finish == "error":  # OpenRouter reports a mid-stream failure this way (A-OR-4)
headwaters/core/executor/executor.py:4: litellm SDK, local through Ollama's native ``/api/chat``. Streaming exposes a ``first_token``
headwaters/core/executor/executor.py:7: constructed for a local-only request, and the Ollama host must be loopback.
headwaters/core/executor/executor.py:19: from headwaters.core.executor.cloud_map import OPENROUTER, attribution_headers
headwaters/core/executor/executor.py:22: from headwaters.core.executor.ollama import OllamaBackend
headwaters/core/executor/executor.py:57: local_factory: Callable[[str], Backend] = OllamaBackend,
headwaters/core/executor/executor.py:132: attribution_headers(self._rules.attribution) if target.provider == OPENROUTER else {}
headwaters/core/executor/executor.py:148: provider = "ollama" if decision.tier == "local" else target.provider
headwaters/core/executor/guard.py:4: never builds a cloud client, and a local target must be a loopback Ollama host.
headwaters/core/executor/ollama.py:17: from headwaters.core.executor.ollama_convert import build_payload, parse_line
headwaters/core/executor/ollama.py:28: class OllamaBackend:
headwaters/core/executor/ollama_convert.py:50: def to_ollama_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
headwaters/core/executor/ollama_convert.py:97: "messages": to_ollama_messages(request.messages),
headwaters/core/executor/types.py:34: output (Ollama ``format``, or an OpenAI ``response_format``)."""
headwaters/core/hardware/residency.py:23: max_loaded_models: int = 1  # 12 GB profile: OLLAMA_MAX_LOADED_MODELS=1
headwaters/core/hardware/state.py:40: _OLLAMA_PREFIX = "ollama"
headwaters/core/hardware/state.py:131: def _is_ollama(proc: GpuProcess) -> bool:
headwaters/core/hardware/state.py:132: return proc.name.lower().startswith(_OLLAMA_PREFIX)
headwaters/core/hardware/state.py:144: others = [p for p in gpu.processes if p.pid != own and not _is_ollama(p)]
headwaters/core/hardware/state.py:150: elif gpu.free_mb is not None and not any(_is_ollama(p) for p in gpu.processes):
headwaters/core/logging.py:40: re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),  # OpenAI, Anthropic (sk-ant-), OpenRouter (sk-or-)
headwaters/core/policy/decide.py:11: 4. paid rung       the user's paid primary, then the OpenRouter auto router
headwaters/core/policy/decide.py:13: then the OpenRouter free router if enabled and complexity allows
```

## Model-slug-shaped string literals

vendor/model or name:14b literals. Should be empty outside tests.

3 hit(s).

```
headwaters/core/verify_diff.py:47: if "w/crlf" in info or "eol=crlf" in info:
headwaters/core/verify_diff.py:49: if "w/lf" in info:
headwaters/core/verify_diff.py:70: files = sorted({m for m in _DIFF_PATH.findall(text) if m.lstrip("/") != "dev/null"})
```

## Module-level constants with numbers

Limits and thresholds. For each: protocol/safety constant (fine), or a tunable that should live in rules.yaml or the catalog?

60 hit(s).

```
headwaters/core/analyzer/_extract.py:12: MAX_DEPTH = 6
headwaters/core/analyzer/classifier.py:36: TASKS = ("code", "reasoning", "chat", "summarize", "extract", "creative", "vision")
headwaters/core/analyzer/classifier.py:37: RISKS = ("low", "medium", "high")
headwaters/core/analyzer/classifier.py:38: _MAX_PROMPT_CHARS = 2000  # head + tail of the last user message sent to the tiny model
headwaters/core/analyzer/classifier.py:39: _MIN_CONFIDENCE = 0.75  # model answers are trusted a little above the usual threshold
headwaters/core/analyzer/classifier.py:40: _SYSTEM = (
headwaters/core/analyzer/ollama_client.py:18: _MAX_RESPONSE_BYTES = 1_000_000
headwaters/core/analyzer/requirements.py:28: _KEYWORD_SCAN_HEAD = 4000  # chars of the last user message scanned for keywords (head + tail)
headwaters/core/analyzer/requirements.py:29: _KEYWORD_SCAN_TAIL = 4000
headwaters/core/analyzer/requirements.py:30: _SHORT_PROMPT_TOKENS = 100
headwaters/core/analyzer/requirements.py:32: _TASK_ORDER = ("vision", "code", "reasoning", "extract", "summarize", "creative", "chat")
headwaters/core/analyzer/requirements.py:34: _IMAGE_BONUS = 3
headwaters/core/analyzer/requirements.py:35: _JSON_BONUS = 2
headwaters/core/analyzer/requirements.py:36: _FENCE_BONUS = 2
headwaters/core/candidates.py:41: _ARCH_KEYS = ("n_layers", "n_kv_heads", "head_dim")
headwaters/core/catalog/schema.py:152: ANALYZER_TASKS = ("code", "reasoning", "chat", "summarize", "extract", "creative")
headwaters/core/catalog/sources.py:22: _GIB = 1024**3
headwaters/core/catalog/updater.py:32: MAX_BYTES = 5_000_000
headwaters/core/catalog/updater.py:33: TIMEOUT_SECONDS = 10.0
headwaters/core/executor/cloud_map.py:17: _MAX_ERROR_CHARS = 4096
headwaters/core/executor/ollama.py:20: DEFAULT_CONNECT_S = 5.0
headwaters/core/executor/ollama.py:21: _MAX_ERROR_BYTES = 4096
headwaters/core/hardware/fit.py:18: GIB = 1024**3
headwaters/core/hardware/fit.py:21: CTX_STEPS: tuple[int, ...] = (8192, 16384, 24576, 32768, 49152, 65536)
headwaters/core/hardware/fit.py:22: CTX_HEADROOM = 1.2  # num_ctx must be >= 1.2 x estimated prompt tokens
headwaters/core/hardware/fit.py:23: GB_TO_GIB = 1e9 / 1024**3
headwaters/core/hardware/fit.py:24: DEFAULT_OVERHEAD_GB = 1.0  # v3 section 7.1 says 0.5-1.0; top of range = safe
headwaters/core/hardware/fit.py:25: DEFAULT_OFFLOAD_WEIGHT_FACTOR = 2.0  # weights up to 2x the GPU budget run (slowly) offloaded
headwaters/core/hardware/gpu.py:21: _MIB = 1024 * 1024
headwaters/core/hardware/gpu.py:23: _NVML_NA_THRESHOLD = 1 << 60
headwaters/core/hardware/gpu.py:24: _SMI_TIMEOUT_S = 5.0
headwaters/core/hardware/state.py:31: RESERVE_DISPLAY_GB = 2.0  # monitor on the RTX (v3.1 C1: 2.0-2.5)
headwaters/core/hardware/state.py:32: RESERVE_UNKNOWN_GB = 2.5  # display status unknown: use the higher value
headwaters/core/hardware/state.py:33: RESERVE_NO_DISPLAY_GB = 1.0  # iGPU drives the display (Optimus/MUX)
headwaters/core/hardware/state.py:35: DEFAULT_BUSY_THRESHOLD_PCT = 50.0
headwaters/core/hardware/state.py:36: DEFAULT_CACHE_TTL_S = 5.0
headwaters/core/hardware/state.py:38: _LAPTOP_RANGE_GB = (10.0, 13.5)
headwaters/core/hardware/state.py:39: _DESKTOP_RANGE_GB = (14.0, 17.5)
headwaters/core/hardware/sysmem.py:16: SLOW_RATIO = 0.5  # measured < 50% of expected counts as slow (first-draft threshold)
headwaters/core/hardware/sysmem.py:17: SPILL_TOLERANCE = 0.02  # size_vram may be 2% under size before we call it a spill
headwaters/core/hardware/sysmem.py:19: SYSMEM_HINT = (
headwaters/core/logging.py:37: _SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
headwaters/core/paths.py:21: _WIN_LONG_PATH_THRESHOLD = 240
headwaters/core/plugins.py:17: CURRENT_API_VERSION = 1
headwaters/core/policy/scoring.py:24: _TOKENS_PER_MILLION = 1_000_000
headwaters/core/quotas.py:39: _TRANSIENT_KINDS = ("rate_limit", "overloaded")
headwaters/core/quotas.py:40: _STALE_AFTER_S = 3600.0  # a wall that ended this long ago no longer counts as "repeated"
headwaters/core/router_log.py:25: _TOKENS_PER_MILLION = 1_000_000.0
headwaters/core/router_session.py:15: _MAX_ID_CHARS = 128
headwaters/core/router_session.py:16: _HASH_CHARS = 32
headwaters/core/rules.py:183: SCORE_TASKS = (
headwaters/core/rules_io.py:73: _HEADER = (
headwaters/core/rules_watch.py:22: DEFAULT_DEBOUNCE_S = 0.3
headwaters/core/state.py:23: BUSY_TIMEOUT_MS = 30_000
headwaters/core/state_decisions.py:104: _OUTCOME_COLUMNS = (
headwaters/core/state_schema.py:8: MIGRATIONS: tuple[tuple[int, str, str], ...] = (
headwaters/core/verify_schema.py:18: MAX_SCHEMA_ERRORS = 5
headwaters/core/walls.py:32: _BODY_SCAN_CHARS = 32_768  # bodies are matched on their first 32K characters only
headwaters/core/walls.py:33: _EPOCH_MS_MIN = 1e11  # bare numbers above this are epoch milliseconds
headwaters/core/walls.py:34: _EPOCH_S_MIN = 1e9  # bare numbers above this (and below the ms limit) are epoch seconds
```

## URLs in code

Hard-coded endpoints.

7 hit(s).

```
headwaters/core/analyzer/classifier.py:123: host: str = "http://localhost:11434",
headwaters/core/analyzer/ollama_client.py:45: def __init__(self, host: str = "http://localhost:11434") -> None:
headwaters/core/catalog/loader.py:90: schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
headwaters/core/catalog/sources.py:21: OPENROUTER_BASE = "https://openrouter.ai/api/v1"
headwaters/core/quotas.py:34: OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
headwaters/core/rules.py:72: host: str = "http://localhost:11434"
headwaters/core/rules_io.py:98: schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
```

## Bind addresses and hosts

Servers must bind to 127.0.0.1 only.

8 hit(s).

```
headwaters/core/analyzer/classifier.py:123: host: str = "http://localhost:11434",
headwaters/core/analyzer/ollama_client.py:35: if name != "localhost":
headwaters/core/analyzer/ollama_client.py:45: def __init__(self, host: str = "http://localhost:11434") -> None:
headwaters/core/catalog/http.py:27: if name == "localhost":
headwaters/core/executor/guard.py:20: Accepts a plain ``http`` URL whose host is ``localhost`` or a loopback IP (127.0.0.0/8,
headwaters/core/executor/guard.py:21: ``::1``). Names are not resolved: anything else, including other host names, is refused.
headwaters/core/executor/guard.py:31: if name.lower() != "localhost":
headwaters/core/rules.py:72: host: str = "http://localhost:11434"
```

## print() in core

Core must not write to stdout (MCP stdio safety).

0 hit(s).

## Broad exception handling

except Exception can hide scope violations. Check none swallows ScopeViolation or LocalScopeViolation.

18 hit(s).

```
headwaters/core/analyzer/layers.py:54: except Exception:  # a layer must never break routing
headwaters/core/executor/cloud.py:79: except Exception as exc:  # litellm raises its own exception tree
headwaters/core/hardware/gpu.py:60: except Exception:
headwaters/core/hardware/gpu.py:79: except Exception:
headwaters/core/hardware/gpu.py:95: except Exception as exc:
headwaters/core/hardware/gpu.py:112: except Exception:
headwaters/core/hardware/gpu.py:122: except Exception as exc:
headwaters/core/hardware/state.py:120: except Exception:
headwaters/core/hardware/state.py:271: except Exception:
headwaters/core/router.py:318: except Exception:  # a locked or missing database must not block routing
headwaters/core/router.py:441: except Exception:  # a broken plugin means: do not escalate
headwaters/core/router.py:561: except Exception:  # best effort
headwaters/core/router.py:572: except Exception:  # a callback must never break routing
headwaters/core/router_log.py:146: except Exception as exc:  # a plugin must never break routing
headwaters/core/router_verify.py:70: except Exception:  # a broken verifier must not fail the user's answer
headwaters/core/rules_edit.py:81: except BaseException:
headwaters/core/state.py:86: except BaseException:
headwaters/core/verify_basic.py:227: except Exception as exc:  # grammar missing or parser crash: never fail the candidate
```

## Suppressions

type: ignore and noqa in core.

4 hit(s).

```
headwaters/core/catalog/merge.py:88: record.row = replace(record.row, **changes)  # type: ignore[arg-type]
headwaters/core/executor/cloud.py:23: setattr(module, "telemetry", False)  # noqa: B010 - litellm ships no type stubs for it
headwaters/core/executor/guard.py:26: parsed.port  # noqa: B018 - raises ValueError on a bad port
headwaters/core/policy/scoring.py:57: return getattr(rules.scoring.strategies, strategy)  # type: ignore[no-any-return]
```

## TODO / FIXME

Unfinished work.

0 hit(s).

## Subprocess use

Fixed argv, no shell.

8 hit(s).

```
headwaters/core/hardware/gpu.py:14: import subprocess
headwaters/core/hardware/gpu.py:147: done = subprocess.run(
headwaters/core/hardware/gpu.py:150: except (OSError, subprocess.SubprocessError):
headwaters/core/stdio.py:78: extensions or subprocesses inheriting stdout are not.
headwaters/core/verify_diff.py:14: import subprocess
headwaters/core/verify_diff.py:25: ) -> subprocess.CompletedProcess[bytes] | None:
headwaters/core/verify_diff.py:30: return subprocess.run(
headwaters/core/verify_diff.py:37: except (OSError, subprocess.TimeoutExpired):
```
