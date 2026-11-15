# Daily pip budget + strategy capture table
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $repo ".venv\Scripts\python.exe") (Join-Path $repo "research\daily_pip_budget.py")
