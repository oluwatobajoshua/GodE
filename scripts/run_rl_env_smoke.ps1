$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\ml\fx_gym_env.py")
exit $LASTEXITCODE
