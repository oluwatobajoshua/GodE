# Lane 1b: MT5 D1 carry — long window + ECN spread cap (close MT5 first)
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [string]$From = "2005.01.01",
    [string]$To = "2026.01.01",
    [ValidateSet("gods_eye_carry_d1", "gods_eye_carry_d1_ecn", "gods_eye_carry_high_risk")]
    [string]$Profile = "gods_eye_carry_d1",
    [switch]$NoVisual
)

$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
Write-Host "[Lane1] Install + compile..."
& (Join-Path $Root "scripts\install_ea.ps1") -TerminalDataDir $TerminalDataDir -SkipIngest | Out-Null

$args = @(
    (Join-Path $Root "scripts\run_backtest.py"),
    "--terminal-data", $TerminalDataDir,
    "--profile", $Profile,
    "--symbol", "EURUSD",
    "--period", "D1",
    "--from-date", $From,
    "--to-date", $To,
    "--model", "1",
    "--no-brain",
    "--no-telemetry",
    "--name", "${Profile}_EURUSD_D1"
)
if ($NoVisual) { } else { $args += "--visual" }

Write-Host "[Lane1] Tester $Profile $From -> $To (close all terminal64.exe if report is empty)"
& $py @args
exit $LASTEXITCODE
