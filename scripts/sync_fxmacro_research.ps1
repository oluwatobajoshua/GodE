# Lane 2: refresh FXMacroData risk sentiment into research/data/fxmacrodata
param(
    [string]$Start = "2024-01-01",
    [string]$End = "2026-12-31"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
$code = @"
from datetime import datetime
from research.fxmacrodata import FXMacroData

api = FXMacroData()
start, end = "$Start", "$End"
rs = api.risk_sentiment(start, end)
print(f"risk_sentiment rows: {len(rs)}")
cal = api.calendar("USD", start, end)
print(f"USD calendar rows: {len(cal)}")
"@
& $py -c $code
Write-Host "Synced macro extracts under research/data/fxmacrodata (cached JSON + CSV)."
