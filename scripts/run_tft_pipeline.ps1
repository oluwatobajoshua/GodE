param(
    [string]$Symbol = "GBPUSD",
    [int]$Epochs = 8,
    [switch]$PrepOnly
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$argsList = @(
    (Join-Path $Root "research\ml\tft_forecast_pipeline.py"),
    "--symbol", $Symbol,
    "--epochs", $Epochs
)
if ($PrepOnly) { $argsList += "--prep-only" }
Push-Location (Join-Path $Root "research\ml")
try {
    & $py @argsList
} finally {
    Pop-Location
}
exit $LASTEXITCODE
