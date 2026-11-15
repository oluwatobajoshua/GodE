# Scalp lane: broker M5 export + London ORB / EMA21 pullback + promotion verdict.
param(
    [switch]$Export,
    [string]$Symbols = "GBPUSD,EURUSD",
    [string]$From = "2020-01-01",
    [string]$To = "2026-01-01"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$env:SOVEREIGN_BAR_SOURCE = "mt5"
$env:SOVEREIGN_M5_SOURCE = "mt5_only"

if ($Export) {
    $symArray = @($Symbols -split "," | ForEach-Object { $_.Trim() })
    $exportArgs = @(
        (Join-Path $Root "scripts\export_mt5_bars.py"),
        "--timeframe", "M5",
        "--from-date", $From,
        "--to-date", $To,
        "--symbols"
    ) + $symArray
    & $py @exportArgs
    if ($LASTEXITCODE -ne 0) { throw "M5 export failed" }
}

Write-Host "`n=== Scalp research (M5) ==="
& $py (Join-Path $Root "research\scalp_lane_m5.py")
& $py (Join-Path $Root "research\scalp_promotion.py")

Write-Host "`nManual EA test (promoted config only):"
Write-Host "  Attach SovereignEmaTrendEA to M5 GBPUSD; match research/ema21_system.py inputs."
Write-Host "  docs/SCALP_LANE.md"
