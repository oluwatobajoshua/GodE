# Scalp profitability truth = broker export + Strategy Tester (not HistData reinvention).
# Close all terminal64.exe before tester steps.
param(
    [switch]$ExportOnly,
    [switch]$SkipTester,
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$env:SOVEREIGN_BAR_SOURCE = "mt5"
$env:SOVEREIGN_M5_SOURCE = "mt5_only"

Write-Host "[SCALP MT5] 1) Export H1 + M5 from terminal (copy_rates)..."
& (Join-Path $Root "scripts\sync_mt5_data_plane.ps1") -Export -ScalpM5 -SkipIngest
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 2) {
    Write-Host "[WARN] Export partial — install MT5 and log in to the broker account"
}

Write-Host "`n[SCALP MT5] 2) Python replay on broker M5 only (tester OOS window)..."
& $py (Join-Path $Root "research\scalp_lane_m5.py")
& $py (Join-Path $Root "research\scalp_promotion.py")

if ($ExportOnly) {
    Write-Host "`nExport + Python replay done. For EA certify: .\scripts\run_london_orb_promote.ps1"
    exit 0
}

if (-not $SkipTester) {
    Write-Host "`n[SCALP MT5] 3) Strategy Tester certify (London ORB)..."
    Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    & (Join-Path $Root "scripts\run_london_orb_promote.ps1") -TerminalDataDir $TerminalDataDir
}

Write-Host "`nArtifacts:"
Write-Host "  research/data/mt5/*_M5.csv"
Write-Host "  research/results/scalp_lane_manifest.json"
Write-Host "  research/results/scalp_promotion_verdict.json"
Write-Host "  research/results/london_orb_tester_manifest.json"
Write-Host "See docs/MT5_DATA_PLANE.md"
