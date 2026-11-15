# End-to-end proof after MT5 data plane: Python carry + H1 stack + optional tester.
param(
    [switch]$Export,
    [switch]$Tester,
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$env:SOVEREIGN_BAR_SOURCE = "mt5"

$syncArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "scripts\sync_mt5_data_plane.ps1"))
if ($Export) { $syncArgs += "-Export" }
$syncArgs += "-Install", "-TerminalDataDir", $TerminalDataDir
& powershell @syncArgs

Write-Host "`n=== Python carry promotion ==="
& $py (Join-Path $Root "research\carry_promotion.py")

Write-Host "`n=== Python GBP H1 stack (MT5 bars) ==="
& $py (Join-Path $Root "research\fxmopol_stack_pnl.py") --symbol GBPUSD

Write-Host "`n=== H1 promotion rule ==="
& $py (Join-Path $Root "research\h1_promotion.py") --symbol GBPUSD --mode professor_governor

if ($Tester) {
    Write-Host "`n=== MT5 Strategy Tester (close terminal first if headless fails) ==="
    & (Join-Path $Root "scripts\run_fxmopol_tester.ps1") -TerminalDataDir $TerminalDataDir -Profile gods_eye_carry_d1 -From "2024.01.01" -To "2026.01.01" -NoVisual -AllowRunning
    & (Join-Path $Root "scripts\run_fxmopol_tester.ps1") -TerminalDataDir $TerminalDataDir -Profile fxmopol_gbp_playbook -From "2024.07.01" -To "2026.01.01" -NoVisual -AllowRunning
    & $py (Join-Path $Root "research\fxmopol_tester_review.py") (Join-Path $Root "backtest\reports\fxmopol_gbp_playbook_GBPUSD_H1.htm")
    & $py (Join-Path $Root "research\ea_stack_parity_check.py")
}

Write-Host "`nResults:"
Write-Host "  research\results\carry_promotion_verdict.json"
Write-Host "  research\results\fxmopol_stack_pnl_manifest.json"
Write-Host "  research\results\h1_promotion_verdict.json"
Write-Host "  research\results\mt5_data_plane_audit.json"
