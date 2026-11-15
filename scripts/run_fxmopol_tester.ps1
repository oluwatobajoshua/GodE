# MT5 Strategy Tester — FXMOPOL / GodsEye (see equity graph in HTML report).
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [ValidateSet("fxmopol_eur_chart", "fxmopol_gbp_playbook", "fxmopol_usdjpy_streak", "gods_eye_carry_d1")]
    [string]$Profile = "fxmopol_eur_chart",
    [string]$Symbol = "",
    [string]$Period = "",
    [string]$From = "2024.01.01",
    [string]$To = "2026.01.01",
    [switch]$NoVisual,
    [switch]$AllowRunning,
    [switch]$PrepareOnly
)

$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"

if (-not $Symbol) {
    switch ($Profile) {
        "fxmopol_usdjpy_streak" { $Symbol = "USDJPY" }
        "fxmopol_gbp_playbook" { $Symbol = "GBPUSD" }
        "gods_eye_carry_d1" { $Symbol = "EURUSD" }
        default { $Symbol = "EURUSD" }
    }
}
if (-not $Period) {
    if ($Profile -eq "gods_eye_carry_d1") { $Period = "D1" } else { $Period = "H1" }
}

Write-Host "[1/2] Install + compile EAs..."
& (Join-Path $Root "scripts\install_ea.ps1") -TerminalDataDir $TerminalDataDir -NoCompile:$false
$fxEx5 = Join-Path $TerminalDataDir "MQL5\Experts\Sovereign\SovereignFxmopolEA.ex5"
if (-not (Test-Path $fxEx5)) {
    Write-Host "[FAIL] SovereignFxmopolEA.ex5 missing after install."
    exit 1
}

$args = @(
    (Join-Path $Root "scripts\run_backtest.py"),
    "--terminal-data", $TerminalDataDir,
    "--profile", $Profile,
    "--symbol", $Symbol,
    "--period", $Period,
    "--from-date", $From,
    "--to-date", $To,
    "--model", "1",
    "--no-brain",
    "--no-telemetry",
    "--name", "${Profile}_${Symbol}_${Period}"
)
if (-not $NoVisual) {
    $args += "--visual"
    $args += "--no-early-fail"
    Write-Host "[VISUAL] Chart plus fxmopol.tpl. Close all MT5 windows before run (required)."
}
if ($AllowRunning) {
    $args += "--allow-running"
    Write-Host "[WARN] --allow-running: a second terminal64 may produce a 0-trade stub report."
}
if ($PrepareOnly) { $args += "--prepare-only" }

Write-Host "[2/2] Strategy Tester: $Profile on $Symbol $Period"
& $py @args
exit $LASTEXITCODE
