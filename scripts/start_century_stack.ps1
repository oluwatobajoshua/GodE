# Start automated Century stack: telemetry brain + hourly adapter daemon.
param(
    [string]$Mt5Files,
    [switch]$NoBrain
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

if ($Mt5Files) {
    $env:SOVEREIGN_MT5_FILES = Join-Path $Mt5Files "Sovereign"
    $env:SOVEREIGN_OUTCOMES = Join-Path $env:SOVEREIGN_MT5_FILES "trade_outcomes.csv"
    $env:SOVEREIGN_BUCKETS = Join-Path $env:SOVEREIGN_MT5_FILES "performance_buckets.csv"
    $env:SOVEREIGN_DIRECTIVES = Join-Path $env:SOVEREIGN_MT5_FILES "century_directives.json"
}

& $python (Join-Path $repoRoot "python\century_adapt.py")
if ($LASTEXITCODE -ne 0) { throw "century_adapt failed" }

$daemon = Start-Process -FilePath $python `
    -ArgumentList (Join-Path $repoRoot "python\century_daemon.py") `
    -WorkingDirectory (Join-Path $repoRoot "python") `
    -PassThru -WindowStyle Hidden
Write-Host "[CENTURY] daemon pid=$($daemon.Id)"

if (-not $NoBrain) {
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$repoRoot\scripts\start_brain.ps1`"" `
        -WorkingDirectory $repoRoot | Out-Null
    Write-Host "[CENTURY] started institutional_quant_master (TCP 5556)"
}

Write-Host "[CENTURY] Directives: $($env:SOVEREIGN_DIRECTIVES)"
Write-Host "[NEXT] Attach SovereignGodsEyeEA on D1; InpEnableCenturyAdapt=true"
