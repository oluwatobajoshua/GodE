# Export MT5 ticks -> research/data/l2 (no pip.exe). Terminal must be running.
param(
    [string]$Symbol = "GBPUSD",
    [int]$Days = 14,
    [switch]$BookSnapshot
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$argsList = @(
    (Join-Path $Root "scripts\export_l2_ticks.py"),
    "--symbol", $Symbol,
    "--days", $Days
)
if ($BookSnapshot) { $argsList += "--book-snapshot" }
& $py @argsList
exit $LASTEXITCODE
