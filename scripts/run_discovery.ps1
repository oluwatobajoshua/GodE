# Run Sovereign Discovery Lab — independent hypothesis testing.
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py (Join-Path $repoRoot "research\discovery_lab.py")
exit $LASTEXITCODE
