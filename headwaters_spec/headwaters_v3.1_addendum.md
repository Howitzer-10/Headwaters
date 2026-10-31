# Headwaters v3.1 Addendum: Decisions, Windows-First, Editions Architecture

Applies to `headwaters_ultimate_design.md` and `headwaters_agent_implementation_prompt.md`. Where this file conflicts with either of them, this file wins.

## A. Decisions log
| # | Decision | Status |
| --- | --- | --- |
| D1 | Open-source Lite? | **Open.** Default is closed until Lite 0.1 has real users. Decide before the first public release. The architecture must keep both options possible (see B). |
| D2 | Primary platform | **Windows 10/11 x64 first.** Linux second, macOS best-effort (no NVIDIA; Apple Silicon unified memory handled later). |
| D3 | Pro pricing | **No discount for a staged Pro.** Use an early-access price that rises at 1.0. Early buyers get all 1.x updates. |
| D4 | Model catalog | Kept in a separate repo. Could be made public on its own (low-risk community contributions). |

## B. Editions architecture (replaces the "edition flag")
A build-time flag is not safe if Lite's code is ever public, because anyone could flip it. Split the code into packages instead:
```
repo: headwaters            (could become public)      repo: headwaters-pro (always private)
  headwaters/core/...                                      headwaters_pro/gateway/...
  headwaters/mcp/...                                       headwaters_pro/verify_full/...
  headwaters/verify_basic/...                              headwaters_pro/bridge/... sessions/...
  headwaters/plugins.py  ◀── entry points ──────────────   headwaters_pro/control_center/...
  headwaters/cli/...                                       headwaters_pro/learning/... fusion/ image/
repo: headwaters-catalog    (could become public)
```
- Core discovers Pro features through Python entry points (`headwaters.plugins`). The plugin interfaces are `SurfacePlugin`, `VerifierPlugin`, `BridgePlugin`, `DashboardPlugin` and `LedgerPlugin`. Version them, and include a contract test suite.
- Lite must work completely with no Pro installed. Pro installs on top as a wheel included in the Pro download.
- Licensing: no online DRM. Pro is simply the paid download.
- License options if D1 goes open: Apache-2.0 (includes a patent grant), MIT, or FSL-1.1-Apache-2.0 (source-available, becomes Apache after 2 years, and prevents competitors from reselling it). If you have an employer, check whether open-source publishing needs approval.

## C. Windows-first requirements
### C1. GPU, VRAM and power
- Detect the GPU with NVML (`nvidia-ml-py`). Fall back to `nvidia-smi` (usually in `C:\Windows\System32`), then to manual config.
- **Reserve depends on whether the GPU drives a display:**
  - Desktop with the monitor on the RTX: reserve **2.0–2.5 GB**.
  - Laptop with Optimus/MUX where the integrated GPU drives the display: about **1.0 GB**.
  - Detect which case applies (NVML display-active status, or use the higher value if unsure). Users can override it.
- **NVIDIA Sysmem Fallback detection:** recent Windows drivers can silently spill VRAM into system RAM. That makes a model run very slowly rather than fail with out-of-memory.
  - Detect it by comparing measured tokens/s against the expected speed for that model size, together with the `/api/ps` `size_vram` vs `size` values.
  - When detected: mark the model `offload`, and have `doctor` explain the NVIDIA Control Panel setting "CUDA – Sysmem Fallback Policy".
- **Battery awareness (laptop):** use `psutil.sensors_battery()`. On battery, add a local latency penalty (the GPU is throttled) and prefer free cloud models. This is configurable: `local.on_battery: prefer_cloud | normal | local_only`.
- Optional "GPU busy" check: if another process (a game, a render) holds most of the VRAM, mark the local tier degraded.

### C2. Ollama on Windows
- `doctor` reads user- and system-level environment variables (registry and `os.environ`) for `OLLAMA_FLASH_ATTENTION`, `OLLAMA_KV_CACHE_TYPE`, `OLLAMA_MAX_LOADED_MODELS` and `OLLAMA_HOST`.
  - Setting them writes user-level variables (`setx`-equivalent).
  - Tell the user to quit and restart the Ollama tray app, then verify the values through the actual behavior.
- Detect the Ollama install and version. Warn about versions older than the minimum supported one.
- WSL split setups (Ollama on Windows, IDE/agent inside WSL): document the host address and `OLLAMA_HOST=0.0.0.0` trade-offs. Keep Headwaters itself bound to 127.0.0.1 by default.

### C3. Networking and ports
- Bind to `127.0.0.1` only (avoids a Windows Firewall prompt and stays private).
- Before binding, check the Hyper-V/WSL excluded port ranges (`netsh int ipv4 show excludedportrange protocol=tcp`). Fall back to the next free port and write it back into the IDE snippets.
- Default ports 4000 (gateway) and 4001 (dashboard) are configurable.

### C4. Files, diffs and commands
- Paths: `platformdirs`. Config and state go in `%APPDATA%\Headwaters` and `%LOCALAPPDATA%\Headwaters`. Support long paths and paths with spaces.
- **Line endings:** normalize CRLF/LF before `git apply --check` and tree-sitter parsing. Keep the repo's line-ending style when producing diffs (respect `.gitattributes` and `core.autocrlf`).
- Project checks run through an allowlist with explicit shells (`pwsh` / `powershell` / `cmd`), timeouts, and no `shell=True` with untrusted strings.
- Workspace-root path validation must handle drive letters, UNC paths and case-insensitive comparison.

### C5. MCP on Windows
- IDE config snippets use **absolute paths** to the Headwaters launcher (`headwaters-mcp.exe` shim or the full venv `python.exe -m headwaters.mcp`), never a bare `python`.
- **Nothing except MCP JSON-RPC may go to stdout.** All logs go to stderr or a file. Add a test that fails if anything else writes to stdout.
- Force UTF-8 stdio (`PYTHONUTF8=1`, explicit encoding) to avoid cp1252 failures.

### C6. Claude Code / OpenCode on Windows
- The wizard sets `ANTHROPIC_BASE_URL` through Claude Code's settings `env` block where possible (scoped to that tool), rather than as a global variable.
- It writes or merges the OpenCode provider config in the correct Windows config location (verify the path) and shows a diff before writing.

### C7. Packaging and distribution
- **Installer:** Inno Setup `.exe` that bundles the `uv` binary plus a locked `uv.lock`, and creates a private venv under `%LOCALAPPDATA%\Headwaters\runtime`. No admin rights are needed for a per-user install.
- **Avoid PyInstaller one-file builds** (frequent Defender false positives). If you freeze the app later, prefer Nuitka.
- **Code signing:** sign the installer and launcher shims, so SmartScreen doesn't warn buyers. Budget for a code-signing certificate or a cloud signing service, and verify eligibility for individual developers in your country.
- **Autostart:** Task Scheduler "At log on" task for the current user (no admin rights), with an on/off toggle in the wizard.
- **Tray:** `pystray` (Windows notification area) with green/amber/red status and toast notifications on tier switches (optional, rate-limited).
- **Secrets:** `keyring` with the Windows Credential Manager backend.
- **itch.io:**
  - Ship a `.itch.toml` manifest with launch actions ("Open Control Center", "Run Setup Wizard", "Doctor").
  - Push with `butler push <dir> <user>/<game>:windows-lite` / `:windows-pro`.
  - The installer and the itch app install should both work. Detect an itch-managed install and avoid self-update conflicts.
- **Uninstaller:** removes the scheduled task, the runtime and, optionally, state and keys (ask first).

## D. Pricing and release plan
| Release | Contents | Suggested price | Gate to ship |
| --- | --- | --- | --- |
| Lite 0.1 | Core + MCP + local/free routing + wall failover + basic verification + doctor | Free / pay what you want | 10-minute install on a 12 GB laptop; stable in Cline + OpenCode + Claude Desktop |
| Pro Early Access | + gateway (OpenAI + Anthropic endpoints), dashboard, **savings ledger**, tray, wizard | ~$15, labeled "Early Access, price rises at 1.0" | The savings counter is accurate and the 402-failover demo works without user action |
| Pro 1.0 | + full verification/escalation, handoff packets, sessions, learning | ~$25 | Golden tests + 2 weeks of early-access feedback with no P1 bugs |
| Pro 1.x | Fusion `deliberate`, image tool, speculative drafting | Same price; free updates | — |

Notes:
- Itch buyers keep access to new uploads on the same page. State plainly that early-access buyers get 1.0 and all 1.x updates.
- **Hold-back criterion:** if early-access feedback shows the gateway alone isn't worth paying for, pause sales and ship 1.0 instead of lowering the price.

## E. Patch to the agent implementation prompt
Replace the "Editions and packaging" section with:
```
EDITIONS: Two codebases. `headwaters` (core, MCP, basic verify, CLI, doctor, status page)
must be fully functional standalone and contain NO Pro code. `headwaters_pro` is a separate
private package registered via Python entry points (group "headwaters.plugins") implementing
versioned interfaces SurfacePlugin, VerifierPlugin, BridgePlugin, DashboardPlugin,
LedgerPlugin. Write plugin contract tests. No edition flags, no DRM, no embedded
marketplace keys.

PLATFORM: Windows 10/11 x64 is primary; every feature must be tested on Windows first.
Implement Addendum §C in full: NVML GPU detection with display-aware reserve, sysmem-
fallback detection, battery-aware policy, Ollama env checks via registry, excluded-port-
range check + 127.0.0.1 binding, platformdirs paths, CRLF normalization in verify,
allowlisted pwsh/cmd project checks, absolute-path MCP launchers, stdout-cleanliness
test, UTF-8 stdio, Credential Manager keyring, Task Scheduler autostart, pystray tray,
Inno Setup + bundled uv + uv.lock per-user installer, code-signing hook in the build
script, .itch.toml launch actions, butler push scripts (windows-lite, windows-pro), and
a clean uninstaller. Linux = second target (systemd user service); macOS best-effort.

DECISION CHECKPOINT: after Phase 2, pause and present an open-source readiness report
(secrets scan, license headers, third-party notices, any Pro leakage) so the owner can
decide D1 before Lite 0.1 is published.
```
