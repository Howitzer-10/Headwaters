"""
model_provisioner.py — One-click local-mode setup: pick the best local Ollama
models for this machine and pull them.

Shared by the dev CLI (``setup_models.py``) and the desktop tray app
(``gui_tray.py``). The routing config (``orchestrator_rules.json``) declares
*which roles* run locally (``destination: "local"`` tiers); this module decides
*which concrete model* serves each role based on the machine's memory, downloads
it, and writes the choice back into the rules so the proxy uses it.

The user never has to pick a model — the orchestrator selects the best one that
fits their hardware. Requires the ``ollama`` CLI on PATH (https://ollama.com/).
"""

import re
import shutil
import subprocess
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

import orchestrator

OLLAMA_SEARCH_URL = "https://ollama.com/search?q={}"
OLLAMA_LIBRARY_URL = "https://ollama.com/library/{}/tags"

# Recommended local models per tier role, ordered best -> lightest.
# Each entry: (model, approximate memory needed in GB including context headroom).
# Qwen3 is used for the balanced tier because it emits proper function-calling
# tool_calls via Ollama (qwen2.5-coder returned the call as JSON text instead).
RECOMMENDED_LOCAL_MODELS = {
    "balanced": [
        ("qwen3:32b", 20.0),
        ("qwen3:14b", 13.0),
        ("qwen3:8b", 6.0),
        ("qwen3:4b", 3.0),
    ],
    "vision_multimodal": [
        ("qwen2.5vl:7b", 6.0),
        ("qwen2.5vl:3b", 2.5),
    ],
}


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------
def detect_ram_gb() -> float:
    """Best-effort total physical RAM in GB. Returns a conservative default on
    failure so we never over-commit to a too-large model."""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullTotalPhys / (1024**3)
    except Exception:
        pass
    return 8.0


def detect_gpu_vram_gb() -> float:
    """Best-effort discrete NVIDIA VRAM in GB (0.0 if none / not detectable)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            total = 0.0
            for line in result.stdout.splitlines():
                if line.strip():
                    total += float(line.strip()) / 1024.0  # MiB -> GiB
            return total
    except Exception:
        pass
    return 0.0


# ---------------------------------------------------------------------------
# Ollama library lookup (used to explain a failed pull)
# ---------------------------------------------------------------------------
class _LibraryLinks(HTMLParser):
    """Collect /library/* hrefs from an Ollama library page."""

    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            if href.startswith("/library/"):
                self.hrefs.append(href)


def _fetch_library_links(url):
    request = Request(url, headers={"User-Agent": "AI-Orchestrator model resolver"})
    with urlopen(request, timeout=10) as response:
        parser = _LibraryLinks()
        parser.feed(response.read().decode("utf-8", errors="replace"))
        return parser.hrefs


def _normalize_model_name(name):
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def find_ollama_model_suggestions(model):
    """Return the official Ollama tags matching ``model`` (no guessed replacement)."""
    model_name = model.split(":", 1)[0]
    search_links = _fetch_library_links(OLLAMA_SEARCH_URL.format(quote(model_name, safe="")))

    candidates = []
    for href in search_links:
        path = urlsplit(href).path
        slug = path.removeprefix("/library/").split(":", 1)[0].strip("/")
        if slug and slug not in candidates:
            candidates.append(slug)

    normalized = _normalize_model_name(model_name)
    exact = [c for c in candidates if _normalize_model_name(c) == normalized]
    if not exact:
        return []

    suggestions = []
    for candidate in exact:
        tags_url = OLLAMA_LIBRARY_URL.format(quote(candidate, safe="/"))
        for href in _fetch_library_links(tags_url):
            path = urlsplit(href).path
            prefix = f"/library/{candidate}:"
            if path.startswith(prefix):
                tag = path[len(prefix) :].strip("/")
                suggestion = f"{candidate}:{tag}"
                if tag and suggestion not in suggestions:
                    suggestions.append(suggestion)
    return suggestions


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------
def ollama_available() -> bool:
    """True if the ``ollama`` CLI is on PATH."""
    return shutil.which("ollama") is not None


def _pull_model(model: str) -> bool:
    """Return True if ``ollama pull`` succeeded for ``model``."""
    try:
        subprocess.run(
            ["ollama", "pull", model],
            check=True,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def collect_local_models():
    """Return the current ``target_model`` values for local tiers."""
    config = orchestrator.load_rules()
    models = set()
    for tier in config.get("tiers", []):
        if tier.get("destination") == "local" and tier.get("target_model"):
            models.add(tier["target_model"])
    return sorted(models)


def recommend_models(ram_gb=None, vram_gb=None):
    """Pick the best local model per role for this machine (largest that fits
    VRAM first, then RAM). Returns ``{role: model_name}``."""
    if ram_gb is None:
        ram_gb = detect_ram_gb()
    if vram_gb is None:
        vram_gb = detect_gpu_vram_gb()
    picks = {}
    for role, candidates in RECOMMENDED_LOCAL_MODELS.items():
        ranked = _rank_candidates(candidates, ram_gb, vram_gb)
        if ranked:
            picks[role] = ranked[0][0]
    return picks


def _rank_candidates(candidates, ram_gb, vram_gb):
    """Order candidates for a machine: the largest model that fits in VRAM first
    (fast GPU), then the largest that fits in RAM but not VRAM (CPU). ``candidates``
    is already sorted largest-first, so the first match is the best quality."""
    vram_fit = [(m, g) for (m, g) in candidates if vram_gb > 0 and g <= vram_gb]
    ram_fit = [(m, g) for (m, g) in candidates if g <= ram_gb]
    seen, ranked = set(), []
    for pair in vram_fit + ram_fit:
        if pair[0] not in seen:
            seen.add(pair[0])
            ranked.append(pair)
    return ranked


def provision_models(progress=None):
    """Detect hardware, pick the best local model for each local tier, download
    it, and write the choices back into ``orchestrator_rules.json``.

    ``progress(message)`` is called with status lines. Returns
    ``(selected_models, failed_tiers)`` lists of strings.
    """
    progress = progress or (lambda msg: None)

    if not ollama_available():
        progress("ERROR: 'ollama' command not found on PATH.")
        progress("Install Ollama from https://ollama.com/ then try again.")
        return [], []

    ram_gb = detect_ram_gb()
    vram_gb = detect_gpu_vram_gb()
    capacity_gb = max(ram_gb, vram_gb)
    if vram_gb > 0:
        progress(f"Detected ~{ram_gb:.0f} GB RAM and ~{vram_gb:.0f} GB VRAM.")
    else:
        progress(f"Detected ~{ram_gb:.0f} GB RAM (no discrete GPU found).")

    config = orchestrator.load_rules()
    local_tiers = [t for t in config.get("tiers", []) if t.get("destination") == "local"]
    if not local_tiers:
        progress("No 'destination: local' tiers configured.")
        return [], []

    selected, failed_tiers = [], []
    changed = False

    for tier in local_tiers:
        name = tier.get("name", "?")
        candidates = RECOMMENDED_LOCAL_MODELS.get(name)

        if not candidates:
            # Unknown local tier — fall back to its currently configured model.
            current = tier.get("target_model")
            if not current:
                progress(f"[{name}] No target model set; skipping.")
                continue
            candidates = [(current, capacity_gb)]

        ranked = _rank_candidates(candidates, ram_gb, vram_gb)
        if not ranked:
            progress(
                f"[{name}] No recommended model fits in ~{capacity_gb:.0f} GB; "
                "trying the lightest option anyway."
            )
            ranked = candidates[-1:]

        progress(f"[{name}] Selecting the best local model for this machine...")
        chosen = None
        for model, _ in ranked:
            progress(f"[{name}] Trying {model} ...")
            if _pull_model(model):
                chosen = model
                break

        if chosen:
            if tier.get("target_model") != chosen:
                tier["target_model"] = chosen
                changed = True
            selected.append(chosen)
            progress(f"[{name}] Ready: {chosen}")
        else:
            failed_tiers.append(name)
            progress(f"[{name}] Could not download a local model for this role.")
            try:
                suggestions = find_ollama_model_suggestions(candidates[0][0])
                if suggestions:
                    progress("  Official Ollama tags:")
                    for suggestion in suggestions[:5]:
                        progress(f"    {suggestion}")
            except (URLError, TimeoutError, ValueError):
                pass

    if changed:
        orchestrator.save_rules(config)
        progress("Updated orchestrator_rules.json with the selected local models.")
    else:
        progress("Routing rules were already up to date.")

    progress(f"Finished: {len(selected)} model(s) ready, {len(failed_tiers)} role(s) failed.")
    return selected, failed_tiers
