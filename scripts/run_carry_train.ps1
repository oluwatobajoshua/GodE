$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "research\carry_train_grid.py")
exit $LASTEXITCODE
