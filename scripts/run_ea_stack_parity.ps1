param(
    [switch]$RunTester,
    [string]$From = "2024.07.01",
    [string]$To = "2026.01.01"
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$argsList = @(Join-Path $Root "research\ea_stack_parity_check.py")
if ($RunTester) { $argsList += "--run-tester"; "--from"; $From; "--to"; $To }
& $py @argsList
exit $LASTEXITCODE
