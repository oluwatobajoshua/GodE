# Train per-symbol FXMOPOL profiles (OOS gate + optional ML skip).
param([switch]$ML)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$args = @((Join-Path $Root "research\fxmopol_symbol_train.py"))
if ($ML) { $args += "--ml" }
& $py @args
if ($LASTEXITCODE -eq 0) {
    & (Join-Path $Root "scripts\install_ea.ps1") -NoCompile:$true
}
exit $LASTEXITCODE
