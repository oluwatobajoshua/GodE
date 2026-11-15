# D1 gate off vs on — EUR/GBP playbook splits + monthly OOS
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" "$root\research\fxmopol_playbook_d1_compare.py"
