# FXMOPOL decision lab — chart states, multi entry/exit matrix, Q&A CSVs
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& "$Root\.venv\Scripts\python.exe" research\fxmopol_decision_lab.py @args
exit $LASTEXITCODE
