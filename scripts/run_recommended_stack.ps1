# Recommended path: promoted carry stack proof (+ optional playbook A/B + GodsEye tester)
param(
    [switch]$SkipPlaybook,
    [switch]$SkipTester,
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [string]$From = "2020.01.01",
    [string]$To = "2026.01.01"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "=== [1] Promoted carry_mom + Stack v1 (Python monthly proof) ==="
& $py (Join-Path $root "research\recommended_stack_proof.py")
$proofExit = $LASTEXITCODE

if (-not $SkipPlaybook) {
    Write-Host "`n=== [2] H1 playbook D1 gate A/B (not certified) ==="
    & $py (Join-Path $root "research\fxmopol_playbook_d1_compare.py")
}

if (-not $SkipTester) {
    Write-Host "`n=== [3] GodsEye MT5 tester (carry_mom) ==="
    & (Join-Path $root "scripts\run_gods_eye_tester.ps1") -TerminalDataDir $TerminalDataDir -From $From -To $To -NoVisual
}

Write-Host "`nVerdict: research\results\recommended_stack_verdict.json"
if ($proofExit -ne 0) {
    Write-Host "[WARN] Python 3-split gate not all green — see stdout."
}
exit $proofExit
