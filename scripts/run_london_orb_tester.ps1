# Strategy Tester — promoted EURUSD London ORB (M5).
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [string]$From = "2024.07.01",
    [string]$To = "2026.01.01",
    [switch]$NoVisual,
    [switch]$AllowRunning
)

$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"

& (Join-Path $Root "scripts\install_ea.ps1") -TerminalDataDir $TerminalDataDir -NoCompile:$false -SkipIngest

$argsList = @(
    (Join-Path $Root "scripts\run_backtest.py"),
    "--terminal-data", $TerminalDataDir,
    "--profile", "london_orb_eurusd_m5",
    "--symbol", "EURUSD",
    "--period", "M5",
    "--from-date", $From,
    "--to-date", $To,
    "--model", "1",
    "--no-brain",
    "--no-telemetry",
    "--name", "london_orb_eurusd_m5_oos"
)
if ($NoVisual) { } else { $argsList += "--visual"; $argsList += "--no-early-fail" }
if ($AllowRunning) { $argsList += "--allow-running" }

Write-Host "London ORB OOS test EURUSD M5 $From -> $To"
& $py @argsList
$rc = $LASTEXITCODE
if ($rc -eq 0) {
    & $py (Join-Path $Root "research\london_orb_tester_collect.py")
}
exit $rc
