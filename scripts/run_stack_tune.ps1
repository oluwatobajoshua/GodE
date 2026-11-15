# Sovereign carry + VIX overlay grid (literature-informed)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" "$root\research\sovereign_stack_tune.py"
