# Push the staged itch payload to the windows-beta channel (HDW-405).
#
# Run this by hand, after `python scripts/build_windows.py --version X.Y.Z` has staged
# build/windows/itch and you have installed that build once on a real Windows machine.
# It is deliberately NOT wired into pytest, pre-commit or CI: an upload is a release decision.
#
# Requires butler: winget install itch.butler  (or https://itch.io/docs/butler/)
# and an authenticated session: butler login
#
# Usage:
#   .\packaging\butler\push_windows_beta.ps1 -User howitzer-10 -Game headwaters
#   .\packaging\butler\push_windows_beta.ps1 -User howitzer-10 -Game headwaters -Dir build\windows\itch
#
# The payload root must contain .itch.toml and runtime\uv.exe; butler validates the manifest
# when you pass -Validate, which is what to do the first time a new action is added.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$User,
    [Parameter(Mandatory = $true)][string]$Game,
    [string]$Dir = "build\windows\itch",
    [string]$Channel = "windows-beta",
    [switch]$Validate
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command butler -ErrorAction SilentlyContinue)) {
    throw "butler is not on PATH. Install it with: winget install itch.butler"
}

$stage = Resolve-Path -LiteralPath $Dir -ErrorAction SilentlyContinue
if (-not $stage) {
    throw "$Dir does not exist. Run scripts\build_windows.py first."
}

foreach ($required in @(".itch.toml", "runtime\uv.exe", "runtime\uv.lock", "runtime\pyproject.toml")) {
    if (-not (Test-Path -LiteralPath (Join-Path $stage $required))) {
        throw "$Dir is missing $required. Run scripts\build_windows.py first."
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

Write-Host "Pushed. In the itch dashboard, set the Windows build's channel to $Channel,"
Write-Host "then install it from the itch app and run the 'Open status page' action once."
