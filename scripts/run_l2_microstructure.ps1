# Export ticks (if MT5 up) then run microstructure pipeline — no pip.
param(
    [string]$Symbol = "GBPUSD",
    [int]$Days = 14,
    [switch]$SkipExport
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not $SkipExport) {
    Write-Host "[1/2] Export L2 ticks from MT5..."
    & (Join-Path $Root "scripts\export_l2.ps1") -Symbol $Symbol -Days $Days
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] export failed — pipeline will use M5 synthetic book."
    }
}
Write-Host "[2/2] Microstructure pipeline..."
& $py (Join-Path $Root "research\ml\microstructure_pipeline.py") --symbol $Symbol
exit $LASTEXITCODE
