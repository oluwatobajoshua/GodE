$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\profit_dashboard.py") --run-stack @args
exit $LASTEXITCODE
