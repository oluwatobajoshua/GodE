# Train / score multi-TF next-move models; refresh prophet_forecasts.json
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\prophet_multitf.py") @args
if ($LASTEXITCODE -eq 0) {
    & $py (Join-Path $Root "python\market_ingest.py")
}
exit $LASTEXITCODE
