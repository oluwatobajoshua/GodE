# Project Sovereign - launch the Python analytics brain.
#
# Usage:
#   .\scripts\start_brain.ps1
#   .\scripts\start_brain.ps1 -Port 5555 -Symbol EURUSD -Interval 0.5

param(
    [int]$Port = 5555,
    [string]$Symbol = "EURUSD",
    [double]$Interval = 0.5
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
$script = Join-Path $repoRoot "python\institutional_quant_master.py"

if (-not (Test-Path $script)) {
    throw "Python brain not found: $script"
}

$env:PYTHONUNBUFFERED = "1"
Write-Host "[SOVEREIGN] Python: $python"
Write-Host "[SOVEREIGN] Port: $Port | Symbol: $Symbol | Interval: ${Interval}s"
& $python $script --port $Port --symbol $Symbol --interval $Interval
exit $LASTEXITCODE
