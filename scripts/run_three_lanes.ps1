# Run all three research lanes (decade fusion, discovery, micro event).
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"

Write-Host "=== Lane 1: Decade fusion ==="
& $py (Join-Path $repo "research\fusion_decade.py")

Write-Host "`n=== Lane 2: Discovery (custom + novel + frontier) ==="
& $py (Join-Path $repo "research\discovery_lab.py")

Write-Host "`n=== Lane 3: Micro event engine (M5) ==="
& $py (Join-Path $repo "research\micro_event_engine.py") --symbol EURUSD
& $py (Join-Path $repo "research\micro_event_engine.py") --symbol GBPUSD

Write-Host "`nDone. See research/results/{fusion_decade_summary,micro_event_summary,discovery_proof}.csv"
