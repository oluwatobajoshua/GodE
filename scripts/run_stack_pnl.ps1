param([string]$Symbol = "GBPUSD")
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\ml\microstructure_pipeline.py") --symbol $Symbol
& $py (Join-Path $Root "python\market_ingest.py") --symbols $Symbol
& $py (Join-Path $Root "research\fxmopol_stack_pnl.py") --symbol $Symbol
exit $LASTEXITCODE
