# Sovereign MT5 data plane - one broker-native tape for research + professor + tester prep.
#
#   .\scripts\sync_mt5_data_plane.ps1 -Export              # MT5 must be installed; terminal can be closed
#   .\scripts\sync_mt5_data_plane.ps1 -Export -Install    # export + ingest + copy to MQL5/Files
#   .\scripts\sync_mt5_data_plane.ps1 -AuditOnly
#
param(
    [switch]$Export,
    [switch]$Install,
    [switch]$AuditOnly,
    [switch]$ScalpM5,
    [switch]$SkipIngest,
    [string]$Symbols = "",
    [string]$From = "",
    [string]$To = "",
    [string]$TerminalDataDir = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
$cfg = Join-Path $Root "configs\mt5_data_plane.json"

if (-not (Test-Path $py)) {
    throw "Python venv not found: $py"
}

$env:SOVEREIGN_BAR_SOURCE = "mt5"

if (-not $AuditOnly) {
    if ($Export) {
        $exportArgs = @( (Join-Path $Root "scripts\export_mt5_bars.py") )
        if ($Symbols) { $exportArgs += @("--symbols") + ($Symbols -split "," | ForEach-Object { $_.Trim() }) }
        if ($From) { $exportArgs += @("--from-date", $From) }
        if ($To) { $exportArgs += @("--to-date", $To) }
        Write-Host "[DATA PLANE] Exporting broker bars..."
        & $py @exportArgs
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 2) {
            Write-Host "[WARN] MT5 export failed - audit may fall back to histdata (misaligned vs tester)"
        }
        $doM5 = $ScalpM5
        if (-not $doM5 -and (Test-Path $cfg)) {
            try {
                $j = Get-Content $cfg -Raw | ConvertFrom-Json
                if ($j.export.m5_symbols_from_scalp_lane) { $doM5 = $true }
            } catch { }
        }
        if ($doM5) {
            $scalpCfg = Join-Path $Root "configs\scalp_lane.json"
            $m5Syms = @("EURUSD", "GBPUSD")
            $m5From = "2020-01-01"
            $m5To = "2026-12-31"
            if (Test-Path $cfg) {
                try {
                    $j = Get-Content $cfg -Raw | ConvertFrom-Json
                    $m5From = $j.export.m5_from
                    $m5To = $j.export.m5_to
                } catch { }
            }
            if (Test-Path $scalpCfg) {
                try {
                    $s = Get-Content $scalpCfg -Raw | ConvertFrom-Json
                    $m5Syms = @($s.symbols)
                } catch { }
            }
            if ($Symbols) {
                $m5Syms = @($Symbols -split "," | ForEach-Object { $_.Trim() })
            }
            Write-Host "[DATA PLANE] Exporting broker M5 (scalp tape)..."
            $m5Args = @(
                (Join-Path $Root "scripts\export_mt5_bars.py"),
                "--timeframe", "M5",
                "--from-date", $m5From,
                "--to-date", $m5To,
                "--symbols"
            ) + $m5Syms
            & $py @m5Args
        }
    } else {
        $mt5Dir = Join-Path $Root "research\data\mt5"
        $any = Get-ChildItem $mt5Dir -Filter "*_H1.csv" -ErrorAction SilentlyContinue
        if (-not $any) {
            Write-Host "[DATA PLANE] No research/data/mt5/*_H1.csv - re-run with -Export or we use histdata fallback"
        }
    }

    if (-not $SkipIngest) {
        Write-Host "[DATA PLANE] Professor ingest (canonical mt5 bars)..."
        $ingestArgs = @( (Join-Path $Root "python\market_ingest.py"), "--ttl-hours", "4" )
        if ($Symbols) { $ingestArgs += @("--symbols", $Symbols) }
        & $py @ingestArgs
        if ($LASTEXITCODE -ne 0) { throw "market_ingest failed" }
    }

    if ($Install) {
        $installScript = Join-Path $Root "scripts\install_ea.ps1"
        $inst = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installScript, "-NoCompile")
        if ($TerminalDataDir) { $inst += @("-TerminalDataDir", $TerminalDataDir) }
        & powershell @inst
        if ($LASTEXITCODE -ne 0) { throw "install_ea failed" }
    }
}

Write-Host "[DATA PLANE] Audit..."
$auditPy = Join-Path $Root "research\mt5_data_plane_audit.py"
& $py $auditPy
$auditExit = $LASTEXITCODE

$parityPy = Join-Path $Root "research\ea_stack_parity_check.py"
if (Test-Path $parityPy) {
    Write-Host "[DATA PLANE] EA stack parity (existing tester CSV, no new MT5 run)..."
    & $py $parityPy
}

if ($auditExit -ne 0) {
    Write-Host "[DATA PLANE] Audit reported blockers - fix export/alignment before trusting H1 tester vs Python"
    exit $auditExit
}
Write-Host "[DATA PLANE] Done. See docs/MT5_DATA_PLANE.md"
exit 0
