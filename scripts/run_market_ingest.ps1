# Sovereign Ecosystem Professor — refresh market_snapshot.json for the EA (FILE_COMMON).
param(
    [string]$Symbols = "",
    [int]$TtlHours = 4,
    [string]$Mt5Files = ""
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
if ($Mt5Files) { $env:SOVEREIGN_MT5_FILES = $Mt5Files }
$argsList = @(
    (Join-Path $Root "python\market_ingest.py"),
    "--ttl-hours", $TtlHours
)
if ($Symbols) { $argsList += @("--symbols", $Symbols) }
& $py @argsList
exit $LASTEXITCODE
