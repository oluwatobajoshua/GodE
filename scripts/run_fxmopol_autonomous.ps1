# FXMOPOL autonomous stack — parity, playbook, calendar, learn, adapt (one shot).
# Log: research/results/logs/fxmopol_autonomous.log
# Manifest: research/results/fxmopol_autonomous_manifest.json
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] Missing venv: $py"
    exit 1
}
Push-Location (Join-Path $root "research")
try {
    & $py fxmopol_autonomous.py
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
