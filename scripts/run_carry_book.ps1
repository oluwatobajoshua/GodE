# Sole certified automation path: D1 carry+momentum on SovereignGodsEyeEA (FROZEN live policy).
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [string]$From = "2024.01.01",
    [string]$To = "2026.01.01",
    [switch]$SkipTester,
    [switch]$FullHistory
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
if ($FullHistory) {
    $From = "2005.01.01"
    $To = "2026.01.01"
}

Write-Host "[CARRY BOOK] Install rates + EA (no ingest)..."
& (Join-Path $Root "scripts\install_ea.ps1") -TerminalDataDir $TerminalDataDir -SkipIngest | Out-Host

Write-Host "`n[CARRY BOOK] Python certification..."
& $py (Join-Path $Root "research\carry_promotion.py")
& $py (Join-Path $Root "research\strategy_certification.py")
& $py (Join-Path $Root "research\profitable_sleeve.py")

if (-not $SkipTester) {
    Get-Process terminal64 -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    foreach ($prof in @("gods_eye_carry_d1", "gods_eye_carry_d1_ecn")) {
        Write-Host "`n[CARRY BOOK] MT5 tester $prof $From -> $To ..."
        & $py (Join-Path $Root "scripts\run_backtest.py") `
            --terminal-data $TerminalDataDir `
            --profile $prof `
            --symbol EURUSD --period D1 `
            --from-date $From --to-date $To `
            --model 1 --no-brain --no-telemetry `
            --name "${prof}_EURUSD_D1_book"
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[WARN] Tester $prof exit $LASTEXITCODE"
        }
    }
    & $py (Join-Path $Root "research\carry_mt5_book_manifest.py")
    & $py (Join-Path $Root "research\carry_mt5_monthly.py")
}

Write-Host "`n[CARRY BOOK] Done."
Write-Host "  Router: configs\good_strategies.json"
Write-Host "  Mode:   configs\sovereign_trading_mode.json"
Write-Host "  Attach: SovereignGodsEyeEA on D1, InpStrategy=SOV_CARRY_MOM"
