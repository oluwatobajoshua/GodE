$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\fxmopol_cross_ride.py")
exit $LASTEXITCODE
