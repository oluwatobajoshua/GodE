$repo = Split-Path -Parent $PSScriptRoot
& (Join-Path $repo ".venv\Scripts\python.exe") (Join-Path $repo "research\fxmopol.py")
