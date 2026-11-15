# MT5 Strategy Tester — GodsEye carry_mom (D1 basket)
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [string]$From = "2020.01.01",
    [string]$To = "2026.01.01",
    [switch]$NoVisual,
    [switch]$AllowRunning
)

$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "[1/2] Install + compile EAs..."
& (Join-Path $Root "scripts\install_ea.ps1") -TerminalDataDir $TerminalDataDir | Out-Null

$args = @(
    (Join-Path $Root "scripts\run_backtest.py"),
    "--terminal-data", $TerminalDataDir,
    "--profile", "gods_eye_carry_d1",
    "--symbol", "EURUSD",
    "--period", "D1",
    "--from-date", $From,
    "--to-date", $To,
    "--model", "1",
    "--no-brain",
    "--no-telemetry",
    "--name", "gods_eye_carry_d1_EURUSD_D1"
)
if ($NoVisual) { } else { $args += "--visual" }
if ($AllowRunning) { $args += "--allow-running" }

Write-Host "[2/2] Strategy Tester: gods_eye_carry_d1 (chart EURUSD D1, basket rebalance)"
& $py @args
exit $LASTEXITCODE
