# Research playbook backtest + equity PNG/HTML (fast, no MT5).
param(
    [string[]]$Symbol = @("GBPUSD", "EURUSD"),
    [switch]$Mt5Report
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$args = @((Join-Path $Root "research\fxmopol_plot_equity.py"))
foreach ($s in $Symbol) { $args += "--symbol"; $args += $s }
& $py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Mt5Report) {
    Write-Host "`n[MT5] Strategy Tester HTML (equity in report)..."
    & (Join-Path $Root "scripts\run_fxmopol_tester.ps1") -Profile fxmopol_gbp_playbook -From "2025.01.01" -To "2025.07.01" -NoVisual -AllowRunning
}
exit $LASTEXITCODE
