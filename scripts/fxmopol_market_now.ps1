# Instant FXMOPOL market pulse from latest H1 research CSV.
param([string]$Symbol = "GBPUSD")
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\fxmopol_market_pulse.py") --symbol $Symbol @args
exit $LASTEXITCODE
