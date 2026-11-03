# Privacy

Headwaters runs on your machine and keeps your work on your machine. This page is the whole
notice; there is no separate policy, because there is no server of ours to have one for.

## No telemetry

There is no telemetry. No analytics, no crash reporting, no usage counters, no install ping and no
account: the `telemetry` rule is the literal `false` and the CLI, the status page and the doctor all
read that same value, so it cannot be turned on by accident. Any future telemetry would be opt-in,

```yaml
# rules.yaml, the only value the schema accepts
telemetry: false
``` Any future telemetry would be opt-in,per feature, off by default, visible in `headwaters doctor`, and announced as a breaking change of
this notice before it ships.

## What is stored, and where

Everything lives in the Headwaters state directory of your own user profile: `rules.yaml`, the
catalog, the daemon token, the request log, backups made by the installer, and the redacted
`doctor --bundle` zips you ask for. Keys you save with `headwaters keys set` go to the OS keyring
where one exists, and to a file in the state directory with owner-only permissions where it does
not. Nothing in that directory is uploaded anywhere by Headwaters.

## What is never logged

The request log drops `prompt`, `content` and `messages` before a row is written
(`headwaters.core.logging.DROPPED_FIELDS`), so the code you paste into a model and the model's
output are not in the log at all: only metadata such as the model, the token counts, the latency and
the outcome. When the daemon writes an error it masks secrets it recognises, and `headwaters doctor
--bundle` writes a zip with no prompt, no key and no path outside the Headwaters app directories.

## What leaves the machine

A request leaves the machine only when *you* route it to a cloud provider, using a key *you*
supplied, in a scope that allows the cloud. Free and local scopes, the Private lock and airplane
mode all keep working with no network. Headwaters itself has no endpoint to call: no update check
phones home, and the catalog update reads the signed feed you configured, or nothing.

## Delete it

`headwaters private` shows the Private modes; `headwaters stop` stops the daemon; the uninstaller
and `headwaters uninstall --host <id>` remove what the installer wrote for a host. To delete
everything, remove the state directory (the doctor prints its path) and revoke the keys you saved
with `headwaters keys remove <name>`; saved keys in the OS keyring are deleted by that command and
by nothing else. Backups the installer made are inside the state directory, so they go with it.
