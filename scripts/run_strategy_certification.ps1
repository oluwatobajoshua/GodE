# Evaluate all sleeves against configs/strategy_quality_bar.json
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"

# Refresh promotion inputs when manifests are stale
& $py (Join-Path $Root "research\carry_promotion.py")
& $py (Join-Path $Root "research\scalp_promotion.py")
& $py (Join-Path $Root "research\strategy_certification.py")
& $py (Join-Path $Root "research\profitable_sleeve.py")

Write-Host "`nRouter: configs\good_strategies.json"
Write-Host "Detail: research\results\strategy_certification_verdict.json"
