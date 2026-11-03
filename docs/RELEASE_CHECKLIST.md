# Windows release checklist

Use this for every Windows release. It covers the Inno Setup installer, the itch.io
`windows-beta` channel, code signing and the uninstall path. Everything here is per-user: no step
needs an administrator, and nothing writes to `HKEY_LOCAL_MACHINE` (HDW-405).

The Linux target has its own document: `docs/RELEASE_CHECKLIST_LINUX.md` (HDW-406).

Two facts this document depends on and that a test asserts:

- the installer never ships Python or model weights; the runtime is created on the target machine
  by the bundled `runtime\uv.exe` from `runtime\uv.lock` (`uv sync --frozen --no-dev`);
- the install root *is* the per-user state dir, `%LOCALAPPDATA%\Headwaters`, so an uninstall
  removes `runtime` and leaves the database, rules and saved keys unless the user says otherwise.

## 0. What the owner has to supply once

| What | Where it goes | Notes |
| --- | --- | --- |
| Inno Setup 6 compiler | `HEADWATERS_ISCC` (or the default install folder) | `winget install JRSoftware.InnoSetup` |
| uv binary to ship | `HEADWATERS_UV` (default: `uv` on `PATH`) | uv is bundled into `runtime\uv.exe`; no Python |
| Windows code-signing certificate | `HEADWATERS_CERT_PATH` | a `.pfx` file that never enters the repo |
| its password, if any | `HEADWATERS_CERT_PASSWORD` | never committed, never printed |
| `signtool.exe` | `HEADWATERS_SIGNTOOL` (default: `signtool.exe` on `PATH`) | from the Windows SDK |
| timestamp server | `HEADWATERS_TIMESTAMP_URL` (default: `http://timestamp.digicert.com`) | |
| butler | `brew`/`winget install itch.butler`, then `butler login` | only for the itch upload |

No certificate means **the build still succeeds** and skips signing with a message. That is a
supported state (an unsigned internal build); shipping to users is not.

## 1. Build

```powershell
$env:HEADWATERS_CERT_PATH = "C:\certs\owner.pfx"      # omit for an unsigned build
$env:HEADWATERS_CERT_PASSWORD = "..."                  # only if the .pfx has one
uv run python scripts\build_windows.py --version 0.6.0
```

It stages `build\windows\payload` (pyproject.toml, uv.lock, uv.exe, `headwaters\`, `catalog\`),
fails if any of those is missing, stages `build\windows\itch` (the payload plus `.itch.toml`),
compiles `packaging\innosetup\headwaters.iss` into `dist\HeadwatersSetup-0.6.0.exe` and signs it
when a certificate is present. `--dry-run` prints the plan and writes nothing; `--skip-iscc`
stages only.

## 2. Clean Windows VM run (manual, IT MUST BE RUN BY A PERSON)

The HDW-405 session could not start a clean VM, so this section is **unproven**: it is written
from the code and from the tests that read the artifacts, not from an actual run. Do it on a
fresh Windows 10/11 x64 VM (or a new local user with no Headwaters state) and tick every line.

- [ ] Create a **standard user** (not administrator) and log in as them.
- [ ] Copy `HeadwatersSetup-0.6.0.exe` to the VM and run it with no elevation prompt.
- [ ] The default folder is `%LOCALAPPDATA%\Headwaters` and the wizard does not ask for admin.
- [ ] Leave "Start Headwaters when I log on" ticked, finish, and wait for the runtime step:
      the last page takes a few minutes (it runs `uv sync --frozen`).
- [ ] `%LOCALAPPDATA%\Headwaters\runtime\.venv\Scripts\headwaters.exe` and `headwaters-mcp.exe`
      exist; `uv.lock` and `pyproject.toml` are in `runtime\`.
- [ ] The final wizard page opens the status page (`headwaters status --open`): the daemon starts
      and the dashboard answers in the browser.
- [ ] Start Menu shows "Open status page", "Doctor" and "Uninstall Headwaters".
- [ ] `headwaters doctor` reports `install` as OK and names the update path.
- [ ] Sign out and back in: the daemon starts, with no console window left open.
- [ ] Task Scheduler (per-user view) shows `\Headwaters`, trigger "At log on", run level
      "Least privilege".
- [ ] `python -m headwaters.packaging.autostart --status` prints `enabled`; `--disable` then
      sign-out/sign-in leaves the daemon down; `--enable` restores it.
- [ ] `headwaters install --host vscode` (or the itch action) still shows its diff and writes.
- [ ] Install the same version again over itself: no error, state preserved.
- [ ] Uninstall from "Apps & features" or the Start Menu, answering **No** to "also delete your
      settings": `runtime` is gone, `%LOCALAPPDATA%\Headwaters\headwaters.db` and
      `%APPDATA%\Headwaters\` are still there, and `\Headwaters` is gone from Task Scheduler.
- [ ] Reinstall: the old database and rules are picked up again.
- [ ] Uninstall again, answering **Yes**: settings, history and saved API keys are gone.
- [ ] `%LOCALAPPDATA%\Headwaters` and `%APPDATA%\Headwaters` are empty or absent; no service, no
      HKLM key, no scheduled task remains.

Record the result (VM image, date, build hash, anything that failed) in the release ticket.

## 3. itch.io `windows-beta` channel

```powershell
.\packaging\butler\push_windows_beta.ps1 -User howitzer-10 -Game headwaters -Validate
```

The script refuses to run unless `build\windows\itch` holds `.itch.toml`, `runtime\uv.exe`,
`runtime\uv.lock` and `runtime\pyproject.toml`. `-Validate` runs `butler validate` first; use it
whenever an action in the manifest changed. It is deliberately **not** wired into pytest,
pre-commit or CI: an upload is a release decision, and no test may reach the network.

On the itch app, the first launch offers six actions, all of which run
`runtime\uv.exe run --frozen --no-dev --project runtime headwaters ...`:

- Open status page → `status --open`
- Doctor → `doctor`
- Install for VS Code / Cline / OpenCode / Claude Desktop → `install --host <id> --yes`

Because `.itch.toml` is pushed, `headwaters doctor` and the status page report the install as
itch-managed: updates come from the itch app, and the installer's "run a newer setup over it"
path is refused. Never point a `HeadwatersSetup` at an itch-installed folder, and never push an
installer build to the itch channel.

## 4. After the release

- [ ] `git log --oneline -1` matches the build you uploaded.
- [ ] Keep the signed installer and its hash; the certificate stays off the repo.
- [ ] If the clean-VM run found a problem, file it before the next release and note it here.
