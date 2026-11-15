# Export max BTCUSD H1 from open MT5 terminal into research/data/BTCUSD_H1.csv
$Root = Split-Path -Parent $PSScriptRoot
& "$Root\.venv\Scripts\python.exe" "$Root\scripts\export_btcusd_h1.py"
