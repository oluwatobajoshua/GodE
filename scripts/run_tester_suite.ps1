# Strategy Tester suite — matches research factory hypotheses (close MT5 first).
param(
    [switch]$CoreOnly,
    [switch]$H1Only,
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (Get-Process terminal64 -ErrorAction SilentlyContinue) {
    Write-Host "[WARN] Close MetaTrader 5 before non-visual tester runs (or use -AllowRunning on individual scripts)."
}

function Run-Carry {
    Write-Host "`n=== MT5: gods_eye_carry_d1 (core, D1) 2020-2026 ==="
    & (Join-Path $Root "scripts\run_lane1_mt5_truth.ps1") -TerminalDataDir $TerminalDataDir -From "2020.01.01" -To "2026.01.01" -NoVisual
}

function Run-H1 {
    Write-Host "`n=== MT5: fxmopol_gbp_playbook (satellite, H1) OOS window ==="
    & (Join-Path $Root "scripts\run_fxmopol_tester.ps1") -TerminalDataDir $TerminalDataDir -Profile fxmopol_gbp_playbook -From "2024.07.01" -To "2026.01.01" -NoVisual
    Write-Host "`n=== Review parity ==="
    & (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "research\fxmopol_tester_review.py") (Join-Path $Root "backtest\reports\fxmopol_gbp_playbook_GBPUSD_H1.htm")
    & (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "research\ea_stack_parity_check.py")
}

if ($H1Only) { Run-H1; exit $LASTEXITCODE }
if ($CoreOnly) { Run-Carry; exit $LASTEXITCODE }
Run-Carry
Run-H1
Write-Host "`nReports: $Root\backtest\reports\"
exit $LASTEXITCODE
