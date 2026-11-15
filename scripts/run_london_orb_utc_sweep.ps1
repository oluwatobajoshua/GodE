# Sweep broker UTC offset for London ORB — auto-collect JSON per offset.
param(
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1",
    [int[]]$Offsets = @(0, 1, 2, 3),
    [string]$OffsetList = ""
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$out = Join-Path $Root "research\results\london_orb_utc_sweep.json"
if ($OffsetList -ne "") {
    $Offsets = @($OffsetList -split "[,\s]+" | Where-Object { $_ -ne "" } | ForEach-Object { [int]$_ })
}
$rows = @()
foreach ($off in $Offsets) {
    Write-Host "`n=== InpBrokerUtcOffset=$off ==="
    & $py (Join-Path $Root "scripts\run_backtest.py") `
        --terminal-data $TerminalDataDir `
        --profile london_orb_eurusd_m5 `
        --symbol EURUSD --period M5 `
        --from-date 2024.07.01 --to-date 2026.01.01 `
        --model 1 --no-brain --no-telemetry `
        --name "london_orb_eurusd_oos_utc$off" `
        --set "InpBrokerUtcOffset=$off"
    $j = Join-Path $Root "backtest\reports\london_orb_eurusd_oos_utc$off.json"
    if (Test-Path $j) {
        $rows += (Get-Content $j -Raw | ConvertFrom-Json)
    }
}
@($rows) | ConvertTo-Json -Depth 6 | Set-Content $out -Encoding utf8
Write-Host "Wrote $out"
