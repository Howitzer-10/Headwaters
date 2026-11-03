# Push the staged Linux payload to the linux-beta channel (HDW-406).
#
# Run this by hand, on a Linux box (or with pwsh installed there), after
# `python scripts/build_linux.py --version X.Y.Z` has staged build/linux/itch and you have
# installed and started that payload once on a real systemd machine. It is deliberately NOT
# wired into pytest, pre-commit or CI: an upload is a release decision.
#
# Requires butler: https://itch.io/docs/butler/installing.html
# and an authenticated session: butler login
#
# Usage:
#   ./packaging/butler/push_linux_beta.ps1 -User howitzer-10 -Game headwaters
#   ./packaging/butler/push_linux_beta.ps1 -User howitzer-10 -Game headwaters -Dir build/linux/itch
#
# The payload root must contain install.sh and runtime/uv plus the lockfile and the project;
# butler validates the payload when you pass -Validate, which is what to do the first time a
# channel is created.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Game,
    [string]$Dir = "build/linux/itch",
    [string]$Channel = "linux-beta",
    [switch]$Validate
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command butler -ErrorAction SilentlyContinue)) {
    throw "butler is not on PATH. Install it from https://itch.io/docs/butler/installing.html"
}

$stage = Resolve-Path -LiteralPath $Dir -ErrorAction SilentlyContinue
if (-not $stage) {
    throw "$Dir does not exist. Run scripts/build_linux.py first."
}

foreach ($required in @("install.sh", "runtime/uv", "runtime/uv.lock", "runtime/pyproject.toml")) {
    if (-not (Test-Path -LiteralPath (Join-Path $stage $required))) {
        throw "$Dir is missing $required. Run scripts/build_linux.py first."
    }
}

$target = "$User/$Game`:$Channel"
Write-Host "butler push $stage $target"

if ($Validate) {
    butler validate $stage
}

butler push $stage $target
if ($LASTEXITCODE -ne 0) {
    throw "butler push failed with exit code $LASTEXITCODE"
}

Write-Host "Pushed. In the itch dashboard, set the Linux build's channel to $Channel,"
Write-Host "then install it from the itch app or with 'butler dl' and run ./install.sh once."
