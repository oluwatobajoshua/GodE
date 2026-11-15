$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py (Join-Path $root "research\fxmopol_price_action_parity.py")
& (Join-Path $root "scripts\install_ea.ps1")
exit $LASTEXITCODE
