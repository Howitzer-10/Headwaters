"""Live discovery sources. Each returns ``LiveModel`` items and raises ``SourceError`` on trouble.

Nothing here sends prompt data. Ollama uses the NATIVE API only (/api/tags, /api/show, /api/ps)
and refuses a non-loopback host (A-OLL-8). OpenRouter attribution headers are not sent (A-OR-10).
Sizes are GiB (bytes / 1024**3) to match the MiB figures GPUs report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from headwaters.core.catalog.http import HttpClient, SourceError, is_loopback_url
from headwaters.core.catalog.schema import Endpoints, ProviderEntry
from headwaters.core.ollama_remote import RemotePatterns, has_remote_fields, is_remote_name
from headwaters.core.rules import OpenAICompatEndpoint

LiveTier = Literal["local", "free_cloud", "paid"]
_GIB = 1024**3


@dataclass
class LiveModel:
    id: str
    tier: LiveTier
    provider: str
    source: str
    modalities: list[str] = field(default_factory=lambda: ["text"])
    tools_ok: bool | None = None
    json_ok: bool | None = None
    reasoning_ok: bool | None = None
    ctx: int | None = None
    price_in: float | None = None  # USD per million tokens
    price_out: float | None = None
    size_gb: float | None = None
    arch: dict[str, int] | None = None
    warm: bool = False
    size_vram_gb: float | None = None
    retains_data: bool | None = None  # None = unknown; the catalog may say
    match_names: list[str] = field(default_factory=list)  # extra names to try in the catalog


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _int(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None and n > 0 else None


def _arch_from_show(info: Mapping[str, Any]) -> tuple[dict[str, int] | None, int | None]:
    """Architecture params for KV math (A-OLL-1). There is no single head_dim key."""
    prefix = info.get("general.architecture")
    if not isinstance(prefix, str):
        return None, None
    layers = _int(info.get(f"{prefix}.block_count"))
    heads = _int(info.get(f"{prefix}.attention.head_count"))
    kv_heads = _int(info.get(f"{prefix}.attention.head_count_kv")) or heads
    head_dim = _int(info.get(f"{prefix}.attention.key_length"))
    embed = _int(info.get(f"{prefix}.embedding_length"))
    if head_dim is None and embed and heads:
        head_dim = embed // heads
    ctx = _int(info.get(f"{prefix}.context_length"))
    if layers and kv_heads and head_dim:
        return {"n_layers": layers, "n_kv_heads": kv_heads, "head_dim": head_dim}, ctx
    return None, ctx


def ollama_models(
    http: HttpClient, host: str, remote_patterns: RemotePatterns = ()
) -> tuple[list[LiveModel], list[str]]:
    """Installed Ollama models plus warm state. Returns (models, warnings).

    Cloud-proxied models (``remote_model``/``remote_host`` in ``/api/tags`` or ``/api/show``, or a
    name matching ``remote_patterns``) are dropped, never listed as local (A-OLL-9, D13).
    """
    if not is_loopback_url(host):
        raise SourceError(f"Ollama host {host!r} is not loopback; local discovery refuses it")
    base = host.rstrip("/")
    tags = _as_dict(http.request_json("GET", f"{base}/api/tags"))
    warnings: list[str] = []
    models: dict[str, LiveModel] = {}
    for item in _as_list(tags.get("models")):
        name = _as_dict(item).get("name") or _as_dict(item).get("model")
        if not isinstance(name, str) or not name:
            continue
        if has_remote_fields(_as_dict(item)) or is_remote_name(name, remote_patterns):
            warnings.append(f"ollama: skipped cloud-proxied model {name} (never routed)")
            continue
        size = _num(_as_dict(item).get("size"))
        live = LiveModel(
            id=name,
            tier="local",
            provider="ollama",
            source="ollama",
            size_gb=round(size / _GIB, 3) if size else None,
            retains_data=False,
        )
        try:
            show = _as_dict(http.request_json("POST", f"{base}/api/show", body={"model": name}))
        except SourceError as exc:
            warnings.append(f"ollama: /api/show failed for {name}: {exc}")
            models[name] = live
            continue
        if has_remote_fields(show):
            warnings.append(f"ollama: skipped cloud-proxied model {name} (never routed)")
            continue
        caps = show.get("capabilities")
        if isinstance(caps, list):
            capset = {str(c) for c in caps}
            if "completion" not in capset:
                continue  # embedding-only model: not routable for chat
            if "vision" in capset:
                live.modalities = ["text", "image"]
            live.tools_ok = "tools" in capset
            live.reasoning_ok = "thinking" in capset
        live.arch, live.ctx = _arch_from_show(_as_dict(show.get("model_info")))
        models[name] = live
    try:
        ps = _as_dict(http.request_json("GET", f"{base}/api/ps"))
    except SourceError as exc:
        warnings.append(f"ollama: /api/ps failed (warm state unknown): {exc}")
    else:
        for item in _as_list(ps.get("models")):
            entry = _as_dict(item)
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name in models:
                models[name].warm = True
                vram = _num(entry.get("size_vram"))
                models[name].size_vram_gb = round(vram / _GIB, 3) if vram is not None else None
    return list(models.values()), warnings


def _per_million(value: Any) -> float | None:
    """OpenRouter prices are USD per token as strings (A-OR-1)."""
    n = _num(value)
    return round(n * 1_000_000, 6) if n is not None and n >= 0 else None


def openrouter_models(
    http: HttpClient, api_key: str | None = None, base_url: str | None = None
) -> list[LiveModel]:
    """Models from {base_url}/models (the catalog ``endpoints``). ``:free`` ids are tier
    free_cloud, the rest paid."""
    base_url = base_url or Endpoints().openrouter_base_url
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    payload = _as_dict(http.request_json("GET", f"{base_url}/models", headers=headers))
    out: list[LiveModel] = []
    for item in _as_list(payload.get("data")):
        entry = _as_dict(item)
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        arch = _as_dict(entry.get("architecture"))
        inputs = [str(m) for m in _as_list(arch.get("input_modalities"))]
        outputs = [str(m) for m in _as_list(arch.get("output_modalities"))]
        if outputs and "text" not in outputs:
            continue  # image/audio generators are not chat models
        params = {str(p) for p in _as_list(entry.get("supported_parameters"))}
        pricing = _as_dict(entry.get("pricing"))
        free = model_id.endswith(":free")
        out.append(
            LiveModel(
                id=model_id,
                tier="free_cloud" if free else "paid",
                provider="openrouter",
                source="openrouter",
                modalities=inputs or ["text"],
                tools_ok="tools" in params,
                json_ok=bool(params & {"response_format", "structured_outputs"}),
                reasoning_ok="reasoning" in params,
                ctx=_int(entry.get("context_length")),
                price_in=0.0 if free else _per_million(pricing.get("prompt")),
                price_out=0.0 if free else _per_million(pricing.get("completion")),
                match_names=[s for s in (entry.get("canonical_slug"),) if isinstance(s, str)],
            )
        )
    return out


@dataclass(frozen=True)
class OpenRouterKeyInfo:
    is_free_tier: bool | None
    free_daily_limit: int | None
    free_daily_remaining: int | None
    credit_remaining: float | None


def openrouter_key(
    http: HttpClient, api_key: str, base_url: str | None = None
) -> OpenRouterKeyInfo:
    """{base_url}/key. The deprecated ``rate_limit`` field is ignored (A-OR-3)."""
    base_url = base_url or Endpoints().openrouter_base_url
    headers = {"Authorization": f"Bearer {api_key}"}
    data = _as_dict(
        _as_dict(http.request_json("GET", f"{base_url}/key", headers=headers)).get("data")
    )
    free = _as_dict(data.get("free_model_daily_requests"))
    is_free = data.get("is_free_tier")
    return OpenRouterKeyInfo(
        is_free_tier=is_free if isinstance(is_free, bool) else None,
        free_daily_limit=_int(free.get("limit")),
        free_daily_remaining=(int(r) if (r := _num(free.get("remaining"))) is not None else None),
        credit_remaining=_num(data.get("limit_remaining")),
    )


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, leaving string contents alone."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 1
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def opencode_config_paths(explicit: str | None, env: Mapping[str, str], home: Path) -> list[Path]:
    """Where to look, lowest precedence first (A-OC-2). Windows path is unverified (R10)."""
    if explicit:
        return [Path(explicit)]
    found = [home / ".config" / "opencode" / name for name in ("opencode.json", "opencode.jsonc")]
    if env.get("OPENCODE_CONFIG"):
        found.append(Path(env["OPENCODE_CONFIG"]))
    return found


def opencode_models(
    config_paths: list[Path], user_list: list[str]
) -> tuple[list[LiveModel], list[str]]:
    """Free OpenCode models: the user list plus ``provider.opencode.models`` from the config.

    There is no machine-readable free-model endpoint (A-OC-3), so the curated catalog and the
    user list decide what is free. Free OpenCode endpoints may retain data (A-OC-4).
    """
    warnings: list[str] = []
    names: list[str] = list(user_list)
    for path in config_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as exc:
            warnings.append(f"opencode: cannot read {path}: {exc.strerror or exc}")
            continue
        try:
            config = _as_dict(json.loads(_strip_jsonc(text)))
        except ValueError:
            warnings.append(f"opencode: {path} is not valid JSON; skipped")
            continue
        provider = _as_dict(_as_dict(config.get("provider")).get("opencode"))
        names.extend(str(k) for k in _as_dict(provider.get("models")))
    seen: dict[str, LiveModel] = {}
    for name in names:
        model_id = name if name.startswith("opencode/") else f"opencode/{name}"
        seen.setdefault(
            model_id,
            LiveModel(
                id=model_id,
                tier="free_cloud",
                provider="opencode",
                source="opencode",
                price_in=0.0,
                price_out=0.0,
                retains_data=True,
                match_names=[name.removeprefix("opencode/")],
            ),
        )
    return list(seen.values()), warnings


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + api_key}


def _any_true(values: list[Any]) -> bool:
    return any(v is True for v in values)


def _registry_tier(entry: ProviderEntry, item: Mapping[str, Any], providers: list[Any]) -> LiveTier:
    """The provider's own tier, lifted to free only by a marker its listing carries."""
    if entry.tier == "free_cloud":
        return "free_cloud"
    if not entry.honor_free_markers:
        return "paid"
    model_id = str(item.get("id", ""))
    marked = (
        model_id.endswith(":free")
        or item.get("isFree") is True
        or _any_true([_as_dict(p).get("is_free") for p in providers])
    )
    return "free_cloud" if marked else "paid"


def _registry_text_only(item: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """``(is a chat model, input modalities)`` from either listing shape."""
    arch = _as_dict(item.get("architecture"))
    modalities = _as_dict(item.get("modalities"))
    outputs = (
        [str(m) for m in _as_list(arch.get("output_modalities"))]
        or [str(m) for m in _as_list(item.get("output_modalities"))]
        or [str(m) for m in _as_list(modalities.get("output"))]
    )
    inputs = (
        [str(m) for m in _as_list(arch.get("input_modalities"))]
        or [str(m) for m in _as_list(item.get("input_modalities"))]
        or [str(m) for m in _as_list(modalities.get("input"))]
    )
    caps = _as_dict(item.get("capabilities"))
    if "completion_chat" in caps and caps["completion_chat"] is not True:
        return False, inputs
    if "vision" in caps and caps["vision"] is True and "image" not in inputs:
        inputs = [*(inputs or ["text"]), "image"]
    return (not outputs or "text" in outputs), inputs


def registry_models(http: HttpClient, entry: ProviderEntry, api_key: str | None) -> list[LiveModel]:
    """Models of one registered provider (HDW-514), read from its ``models_url``.

    Ids are ``{name}/{upstream id}`` so they cannot collide with another provider's. Without a
    key only ``free_cloud`` models are offered. Fields a listing does not carry stay ``None``.
    """
    headers = _bearer(api_key) if api_key else None
    payload = _as_dict(http.request_json("GET", entry.models_url, headers=headers))
    skip = re.compile(entry.skip_id_pattern) if entry.skip_id_pattern else None
    out: list[LiveModel] = []
    for raw in _as_list(payload.get("data")):
        item = _as_dict(raw)
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        if skip is not None and skip.search(model_id):
            continue
        chat, inputs = _registry_text_only(item)
        if not chat:
            continue
        providers = _as_list(item.get("providers"))
        tier = _registry_tier(entry, item, providers)
        if tier != "free_cloud" and not api_key:
            continue
        params = {str(p) for p in _as_list(item.get("supported_parameters"))}
        caps = _as_dict(item.get("capabilities"))
        tools = (
            "tools" in params
            or item.get("tools") is True
            or item.get("supports_tools") is True
            or caps.get("function_calling") is True
            or _as_dict(caps.get("tools")).get("supported") is True
            or _any_true([_as_dict(p).get("supports_tools") for p in providers])
        )
        ctx = (
            _int(item.get("context_length"))
            or _int(_as_dict(item.get("top_provider")).get("context_length"))
            or _int(_as_dict(item.get("limit")).get("context"))
            or next((c for p in providers if (c := _int(_as_dict(p).get("context_length")))), None)
        )
        free = tier == "free_cloud"
        out.append(
            LiveModel(
                id=f"{entry.name}/{model_id}",
                tier=tier,
                provider=entry.name,
                source=f"provider:{entry.name}",
                modalities=inputs or ["text"],
                tools_ok=tools,
                json_ok=bool(params & {"response_format", "structured_outputs"})
                or caps.get("structured_output") is True,
                reasoning_ok="reasoning" in params
                or item.get("reasoning") is True
                or _as_dict(caps.get("reasoning")).get("supported") is True,
                ctx=ctx,
                price_in=0.0 if free else None,
                price_out=0.0 if free else None,
                retains_data=entry.retains_data,
                match_names=[model_id],
            )
        )
    return out


def openai_compatible_models(
    http: HttpClient, endpoint: OpenAICompatEndpoint, env: Mapping[str, str]
) -> list[LiveModel]:
    """A user-supplied free OpenAI-compatible endpoint (explicit list, or GET /models)."""
    names = list(endpoint.models)
    if not names:
        headers = None
        if endpoint.api_key_env and env.get(endpoint.api_key_env):
            headers = {"Authorization": f"Bearer {env[endpoint.api_key_env]}"}
        payload = http.request_json(
            "GET", endpoint.base_url.rstrip("/") + "/models", headers=headers
        )
        names = [
            str(_as_dict(m).get("id"))
            for m in _as_list(_as_dict(payload).get("data"))
            if _as_dict(m).get("id")
        ]
    return [
        LiveModel(
            id=f"{endpoint.name}/{name}",
            tier="free_cloud",
            provider=endpoint.name,
            source=f"openai_compatible:{endpoint.name}",
            price_in=0.0,
            price_out=0.0,
            match_names=[name],
        )
        for name in names
    ]
