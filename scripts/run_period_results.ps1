# Daily + monthly return tables (carry, stack, FXMOPOL)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" "$root\research\export_period_results.py"
