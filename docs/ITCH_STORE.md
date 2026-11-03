# Headwaters (Beta): Never run out of model

Headwaters is a local-first router for coding assistants on Windows and Linux. When the paid credits
or the rate limit runs out, it moves the same request to the next usable model, local or cloud,
without changing the tool you are typing in.

## Pitch

Every coding assistant is one wall away from stopping: a rate limit, a card that declined, a
subscription that lapsed mid-task. Headwaters sits between your editor and the models, serves the
OpenAI-compatible and Anthropic-compatible endpoints a host already understands, and routes each
request to whatever can answer it: your own API keys, a free tier, or a model running on your GPU.
You keep working; Headwaters picks the model, measures whether it fits your VRAM, verifies the
patch, and records what happened.

- **Keep coding when the wall hits.** Automatic failover across rate limits, exhausted credit and
  overloaded providers, in your existing editor.
- **Bring your own keys.** No account, no subscription, no middleman: Headwaters calls the providers
  you already pay, with keys you save locally.
- **Use the GPU you own.** A 12 GB or 16 GB card runs a coding model locally, with the context
  window computed from your actual free VRAM instead of a guess.
- **Keep the promise visible.** The status page shows which model answered, why it was chosen, and
  what was verified, for every request.

★ = free during beta; may become part of Headwaters Pro at 1.0

## What you need

- Windows 10/11 (per-user installer, no admin rights) or a Linux desktop with systemd user units.
  On Linux the itch download is an archive you unpack and install yourself: run `./install.sh` from
  it. There is no `itch.toml` launcher on Linux, because the itch app cannot register a systemd user
  unit for you.
- 12 GB of GPU memory for the local experience; 16 GB is comfortable. It also runs cloud-only with
  no GPU.
- One of: VS Code with GitHub Copilot BYOK, Cline, OpenCode or Claude Desktop.
- Optional: at least one API key (OpenAI, Anthropic, or a provider you already use), and Ollama if
  you want local models.

## What beta means for you

<!-- store-copy: beta -->
This is a beta build. It is free, it has no account and no telemetry, and it may break between
releases. The Windows clean-VM run and the Linux clean run are both still unproven, and the Windows
installer is not code-signed yet, so SmartScreen will warn the first time. Report a bug and it goes
on the list for the next build.

## FAQs

<!-- faq: stay-free -->
**Will it stop working when the beta ends?** The core does not move behind a paywall: the gateway,
failover, scopes, Private mode, the hardware guard and the installer stay free, in every build.
Some features are marked ★, which means they are free now and may join a paid tier at 1.0.

<!-- faq: tracking -->
**Do you see my code or my prompts?** No. There is no telemetry and no account, the request log
never stores `prompt`, `content` or `messages`, and nothing leaves the machine unless your own
request is routed to a provider key you supplied. See `docs/PRIVACY.md` in the repository.

**Which tools work today?** VS Code Copilot BYOK, Cline, OpenCode and Claude Desktop, each installed
by `headwaters install --host <id>`, which shows the exact files before writing them.

**Do I need a GPU?** No. Cloud-only installs work with your own keys. A local model needs a card
with enough VRAM for the weights plus the context window; 12 GB is the entry point.

**What does a beta tester get?** A place on the tester list, and that list is what the Pro promise
below is made to.

## The tester promise

<!-- promise: testers-pro -->
Everyone on the beta tester list gets Headwaters Pro free if a free/Pro split ships at 1.0. Joining
is one message to the maintainer during the beta; leaving is one message back and does not affect
the free core features above.

## Support and status

The release gate in the repository (`headwaters release-gate`) reports what has been proven and what
has not; while it is red, treat the build as a beta for people who can reinstall. Bugs, host config
questions and the six common failures are covered in `docs/TROUBLESHOOTING.md`.
