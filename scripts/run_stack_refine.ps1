# Refinement grid → best_win_settings stack overlay
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py (Join-Path $root "research\stack_refine.py")
& $py (Join-Path $root "research\apply_best_settings.py")
& $py (Join-Path $root "research\recommended_stack_proof.py")
