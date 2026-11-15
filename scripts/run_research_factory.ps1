$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root ".venv\Scripts\python.exe") (Join-Path $Root "research\research_factory.py")
exit $LASTEXITCODE
