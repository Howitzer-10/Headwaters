# Release evidence

`headwaters release-gate` separates two kinds of readiness. **Machine checks** are facts the
repository can prove on its own: the docs exist and are not stubs, the generated host pages match
the installer, the privacy notice matches the code, the packaging payload is present, the catalog
feed is valid. **Owner steps** are facts only a person can prove: a timed install on a real 12 GB and
16 GB machine, four hosts used for real, a week of closed alpha, a clean Windows VM, a clean Linux
distribution, an Authenticode signature, two itch uploads, an open-source review and a tester list.
Nothing in this file makes a machine check pass, and no test can make an owner step pass.

## Where the evidence lives

`docs/release/evidence.json`, committed with the release pull request:

```json
{
  "version": 1,
  "steps": {
    "install-12gb": { "minutes": 8, "gpu_gb": 12, "date": "2026-01-31", "machine": "..." },
    "windows-clean-vm": { "date": "2026-01-31", "machine": "...", "result": "pass" }
  }
}
```

The gate reads that path by default; `--evidence <path>` points at another file and
`--feed <path>` at another catalog feed. A missing file is not an error: every owner step is then
reported blocked, which is the honest state until somebody does the work. A malformed file is an
error, exit code 2, because a typo must not silently read as "everything blocked".

## What each step takes

| Key | Proof the gate validates |
| --- | --- |
| `install-12gb` | `minutes` <= 10, `gpu_gb` >= 12, `date`, `machine` |
| `install-16gb` | `minutes` <= 10, `gpu_gb` >= 16, `date`, `machine` |
| `host-stability` | notes for `hosts.vscode`, `hosts.cline`, `hosts.opencode`, `hosts.claude-desktop`, `date` |
| `p1-week` | `days` >= 7, `p1_open` = 0, `from`, `to` |
| `windows-clean-vm` | `date`, `machine`, `result` = `pass` |
| `linux-clean-run` | `date`, `distribution`, `result` = `pass` |
| `code-signing` | `signer`, `certificate`, `verified` = true, `timestamped` = true, `date` |
| `itch-windows` | `channel` = `windows-beta`, `uploaded` = true, `build_id`, `date` |
| `itch-linux` | `channel` = `linux-beta`, `uploaded` = true, `build_id`, `date` |
| `open-source-review` | `reviewer`, `date` |
| `tester-registration` | `system`, `registered` >= 1 |

Dates are `YYYY-MM-DD`. A field the gate does not recognise is ignored; a field that is present and
wrong fails that step with the reason, so the report says what to fix instead of "blocked".

## Running it

```
headwaters release-gate            # report, exit 1 while anything is blocked
headwaters release-gate --json     # the same result for a script
headwaters release-docs --check    # are docs/hosts/*.md current with the installer?
headwaters release-docs --write    # regenerate them after an installer change
```

Exit codes: `0` everything proven, `1` not ready, `2` bad evidence file or not a checkout. Run the
gate at the end of [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) and
[RELEASE_CHECKLIST_LINUX.md](RELEASE_CHECKLIST_LINUX.md), and paste the report into the release
pull request.
