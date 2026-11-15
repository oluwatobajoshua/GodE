$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
& "$root\.venv\Scripts\python.exe" "$root\research\fxmopol_filter_learn.py"
& "$root\.venv\Scripts\python.exe" "$root\python\fxmopol_adapt.py"
