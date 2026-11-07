# Changelog

## 0.1.0

Unreleased. Built and installable as a `.vsix`; not published to the Marketplace or Open VSX yet.

### Added

- A status bar item showing tier and warm model, active walls, estimated savings, and the purple
  Private Mode lock. Clicking it opens the dashboard (`HDW-507`).
- Commands: `Refresh Status`, `Set Scope…`, `Toggle Private Mode`, `Pin Model…`,
  `Create Handoff Packet…`, `Run Doctor`, `Open Dashboard`.
- The status bar goes to an explicit *daemon not reachable* state on the next tick when the daemon
  dies or the token is rejected, instead of showing the last reading as if it were current.
- Marketplace packaging: `README`, `LICENSE`, `CHANGELOG`, `.vscodeignore`, a vsce devDependency and
  a CI job that builds the `.vsix`.
- Headwaters virtual models (auto, free, local, cloud) in the VS Code language model picker, with
  the daemon's own context window and capability floor (`HDW-506`).
