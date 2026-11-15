# Prove EMA 21 configs (design / validation / OOS) — read ema21_manifest.json before live.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
& $py (Join-Path $repo "research\ema21_system.py")
$manifest = Join-Path $repo "research\results\ema21_manifest.json"
if (Test-Path $manifest) {
    Get-Content $manifest | Write-Host
}
