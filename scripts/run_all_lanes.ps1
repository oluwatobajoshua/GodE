# Run lanes 1–3 (Python) + merge summary. Lane 1b MT5: run_lane1_mt5_truth.ps1 with MT5 closed.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "=== Lane 1: ECN friction sweep (monthly research) ==="
& $py (Join-Path $root "research\ecn_carry_friction.py")

Write-Host "`n=== Lane 2: Alt sleeves (BTC / XAU / DXY tsmom) ==="
& $py (Join-Path $root "research\alt_sleeve_scan.py")

Write-Host "`n=== Lane 3: High-risk carry (documented DD floor) ==="
& $py (Join-Path $root "research\carry_high_risk.py")

Write-Host "`n=== Balanced stack (unchanged default path) ==="
& $py (Join-Path $root "research\stack_refine.py")
& $py (Join-Path $root "research\apply_best_settings.py")

Write-Host "`n=== Merge lane summary ==="
& $py (Join-Path $root "research\lanes_summary.py")

Write-Host "`nDone. Optional MT5 truth: .\scripts\run_lane1_mt5_truth.ps1 -NoVisual -Profile gods_eye_carry_d1_ecn"
Write-Host "See docs\FOUR_LANES.md"
