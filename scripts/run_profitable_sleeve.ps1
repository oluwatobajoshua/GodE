# Strategy fix path: research factory + discovery strict pick + M5 broker scan + sleeve verdict.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$env:SOVEREIGN_M5_SOURCE = "mt5_only"

Write-Host "[1/5] Research factory (carry + H1)..."
& $py (Join-Path $Root "research\research_factory.py")

Write-Host "`n[2/5] Discovery lab (D1 hypotheses)..."
& (Join-Path $Root "scripts\run_discovery.ps1")

Write-Host "`n[3/5] ORB gross vs net + H1 swing + M5 scan..."
& $py (Join-Path $Root "research\london_orb_gross_net.py")
& $py (Join-Path $Root "research\swing_h1_lane.py")
& $py (Join-Path $Root "scripts\export_mt5_bars.py") --timeframe M5 --symbols EURUSD GBPUSD --from-date 2020-01-01 2>$null
& $py (Join-Path $Root "research\mt5_m5_hypothesis_scan.py")

Write-Host "`n[4/5] Strategy certification + sleeve router..."
& (Join-Path $Root "scripts\run_strategy_certification.ps1")

Write-Host "`n[5/5] Optional MT5 D1 carry tester (close terminal64 first):"
Write-Host "  .\scripts\run_lane1_mt5_truth.ps1 -NoVisual"
Write-Host "`nSee configs\profitable_sleeve.json and docs\PROFITABLE_SLEEVE.md"
