# Export FX H1 from MT5 + walk-forward train + install profiles (MT5 must be open for export).
param(
    [string[]]$Symbols = @("GBPUSD", "EURUSD", "USDJPY", "BTCUSD")
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$export = Join-Path $Root "scripts\export_rates.py"
foreach ($s in $Symbols) {
    if ($s -eq "BTCUSD") {
        & $py (Join-Path $Root "scripts\export_btcusd_h1.py")
    } else {
        & $py $export --symbol $s --timeframe H1 --from-date 2015-01-01
    }
    if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] export failed for $s" }
}
& $py (Join-Path $Root "research\fxmopol_symbol_train.py")
if ($LASTEXITCODE -eq 0) {
    & (Join-Path $Root "scripts\install_ea.ps1")
}
exit $LASTEXITCODE
