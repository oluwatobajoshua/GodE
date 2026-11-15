# IT-safe ML stack: only .venv\Scripts\python.exe — no pip.exe, no installers.
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] python.exe not found at $py"
    exit 1
}
Write-Host "[1/3] RL env core smoke..."
& $py (Join-Path $Root "research\ml\fx_env_core.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[2/3] sklearn RL train..."
& $py (Join-Path $Root "research\ml\rl_train_sklearn.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[3/4] sklearn horizon forecast -> prophet_forecasts.json..."
& $py (Join-Path $Root "research\ml\horizon_forecast_sklearn.py") GBPUSD
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "[4/4] microstructure OFI + Hawkes + Neural ODE (Euler)..."
& $py (Join-Path $Root "research\ml\microstructure_pipeline.py") --symbol GBPUSD
exit $LASTEXITCODE
