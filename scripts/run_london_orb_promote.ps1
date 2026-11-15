# Full scalp certify path: UTC sweep -> pin offset -> canonical tester -> promotion.
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [int[]]$Offsets = @(0, 1, 2, 3),
    [string]$OffsetList = ""
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$cfgPath = Join-Path $Root "configs\scalp_lane.json"
$sweepArgs = @{ TerminalDataDir = $TerminalDataDir }
if ($OffsetList -ne "") {
    $sweepArgs["OffsetList"] = $OffsetList
} else {
    $sweepArgs["Offsets"] = $Offsets
}

Write-Host "[1/4] UTC offset sweep (close MT5 before this)..."
& (Join-Path $Root "scripts\run_london_orb_utc_sweep.ps1") @sweepArgs

Write-Host "`n[2/4] Pick best offset..."
& $py (Join-Path $Root "research\london_orb_utc_pick.py")
$pickPath = Join-Path $Root "research\results\london_orb_utc_pick.json"
if (-not (Test-Path $pickPath)) { throw "Missing utc pick" }
$pick = Get-Content $pickPath -Raw | ConvertFrom-Json
$off = [int]$pick.pick.utc_offset
Write-Host "Pinned InpBrokerUtcOffset=$off parity_ok=$($pick.parity_ok)"

# Update config pinned offset
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfg.mt5_ea.pinned_utc_offset = $off
$cfg | ConvertTo-Json -Depth 8 | Set-Content $cfgPath -Encoding utf8

Write-Host "`n[3/4] Canonical OOS tester with pinned offset..."
& $py (Join-Path $Root "scripts\run_backtest.py") `
    --terminal-data $TerminalDataDir `
    --profile london_orb_eurusd_m5 `
    --symbol EURUSD --period M5 `
    --from-date 2024.07.01 --to-date 2026.01.01 `
    --model 1 --no-brain --no-telemetry `
    --name london_orb_eurusd_m5_oos `
    --set "InpBrokerUtcOffset=$off"
if ($LASTEXITCODE -ne 0) { throw "Tester failed" }

& $py (Join-Path $Root "research\london_orb_tester_collect.py")

Write-Host "`n[4/4] Scalp promotion (Python + MT5 gates)..."
& $py (Join-Path $Root "research\scalp_promotion.py")

& $py (Join-Path $Root "research\london_orb_tester_collect.py")

Write-Host "`nDone. See research\results\scalp_promotion_verdict.json"
