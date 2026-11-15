# Project Sovereign - install and compile the MQL5 EA into MetaTrader 5.
#
# Usage:
#   .\scripts\install_ea.ps1
#   .\scripts\install_ea.ps1 -TerminalDataDir "C:\Users\me\AppData\Roaming\MetaQuotes\Terminal\<id>"
#   .\scripts\install_ea.ps1 -NoCompile

param(
    [string]$TerminalDataDir,
    [switch]$NoCompile,
    [switch]$SkipIngest
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $repoRoot "EA\SovereignGodsEyeEA.mq5"

if (-not (Test-Path $source)) {
    throw "EA source not found: $source"
}

function Get-TerminalDataDirs {
    $root = Join-Path $env:APPDATA "MetaQuotes\Terminal"
    if (-not (Test-Path $root)) { return @() }
    return @(Get-ChildItem $root -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "MQL5\Experts") } |
        ForEach-Object { $_.FullName })
}

if (-not $TerminalDataDir) {
    $dirs = @(Get-TerminalDataDirs)
    if ($dirs.Count -eq 0) {
        throw "No MetaTrader 5 data directory found under $env:APPDATA\MetaQuotes\Terminal."
    }
    if ($dirs.Count -gt 1) {
        Write-Host "Multiple MT5 terminals found. Re-run with -TerminalDataDir and one of:"
        $dirs | ForEach-Object { Write-Host "  $_" }
        throw "Ambiguous MT5 terminal selection."
    }
    $TerminalDataDir = $dirs[0]
}

$expertsDir = Join-Path $TerminalDataDir "MQL5\Experts\Sovereign"
$mql5Dir = Join-Path $TerminalDataDir "MQL5"
$includeSource = Join-Path $repoRoot "EA\Include"
$includeDest = Join-Path $mql5Dir "Include\Sovereign"
New-Item -ItemType Directory -Force -Path $expertsDir | Out-Null
New-Item -ItemType Directory -Force -Path $includeDest | Out-Null

if (Test-Path $includeSource) {
    Get-ChildItem $includeSource -Filter *.mqh -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $includeDest $_.Name) -Force
        Write-Host "[INSTALL] Copied include $($_.Name)"
    }
}

$filesDir = Join-Path $mql5Dir "Files\Sovereign"
$ratesDir = Join-Path $filesDir "rates"
$ratesSource = Join-Path $repoRoot "research\data\rates"
if (Test-Path $ratesSource) {
    New-Item -ItemType Directory -Force -Path $ratesDir | Out-Null
    Get-ChildItem $ratesSource -Filter *.csv -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $ratesDir $_.Name) -Force
        Write-Host "[INSTALL] Copied rate $($_.Name) to MQL5/Files/Sovereign/rates"
    }
}

$centuryTpl = Join-Path $repoRoot "backtest\mt5_files\Sovereign\century_directives.json"
$centuryDst = Join-Path $filesDir "century_directives.json"
New-Item -ItemType Directory -Force -Path $filesDir | Out-Null
if ((Test-Path $centuryTpl) -and -not (Test-Path $centuryDst)) {
    Copy-Item $centuryTpl $centuryDst -Force
    Write-Host "[INSTALL] Seeded century_directives.json"
}
$bestWinSrc = Join-Path $repoRoot "research\results\best_win_settings.json"
$bestWinStub = Join-Path $repoRoot "backtest\mt5_files\Sovereign\best_win_settings.json"
$bestWinDst = Join-Path $filesDir "best_win_settings.json"
if (Test-Path $bestWinSrc) {
    Copy-Item $bestWinSrc $bestWinDst -Force
    Write-Host "[INSTALL] Copied best_win_settings.json (full manifest)"
} elseif (Test-Path $bestWinStub) {
    Copy-Item $bestWinStub $bestWinDst -Force
    Write-Host "[INSTALL] Copied best_win_settings.json (stub)"
}
$symProfSrc = Join-Path $repoRoot "research\results\fxmopol_symbol_profiles.json"
$symProfDst = Join-Path $filesDir "fxmopol_symbol_profiles.json"
if (Test-Path $symProfSrc) {
    Copy-Item $symProfSrc $symProfDst -Force
    Write-Host "[INSTALL] Copied fxmopol_symbol_profiles.json (per-symbol trade gate)"
}
$chartTpl = Join-Path $repoRoot "templates\fxmopol.tpl"
$tplDir = Join-Path $mql5Dir "Profiles\Templates"
if (Test-Path $chartTpl) {
    New-Item -ItemType Directory -Force -Path $tplDir | Out-Null
    Copy-Item $chartTpl (Join-Path $tplDir "fxmopol.tpl") -Force
    Write-Host "[INSTALL] Copied chart template fxmopol.tpl"
}

# Publish the flat ASCII template spec the EA reads at runtime and hot-reloads.
# The EA cannot open Profiles\Templates, so this is the only way a template edit
# reaches it. Re-run with scripts\sync_template.ps1 -Watch to keep it live.
$syncTpl = Join-Path $PSScriptRoot "sync_template.ps1"
if ((Test-Path $chartTpl) -and (Test-Path $syncTpl)) {
    & $syncTpl -Tpl $chartTpl -Name "fxmopol"
}

# The EA cannot read Profiles\Templates (MQL5 file I/O is sandboxed to Files),
# so export the template's indicator settings to JSON and copy that instead.
# This is the alignment contract behind the on-chart panel.
$chartJson = Join-Path $repoRoot "backtest\mt5_files\Sovereign\chart_template.json"
$exportPy = Join-Path $repoRoot "scripts\export_chart_template.py"
$pyExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
if ((Test-Path $pyExe) -and (Test-Path $exportPy)) {
    & $pyExe $exportPy | ForEach-Object { Write-Host $_ }
}
if (Test-Path $chartJson) {
    Copy-Item $chartJson (Join-Path $filesDir "chart_template.json") -Force
    Write-Host "[INSTALL] chart_template.json (EA <-> template alignment)"
}
$evidenceTpl = Join-Path $repoRoot "backtest\mt5_files\Sovereign\evidence_directives.json"
$evidenceDst = Join-Path $filesDir "evidence_directives.json"
if (Test-Path $evidenceTpl) {
    Copy-Item $evidenceTpl $evidenceDst -Force
    Write-Host "[INSTALL] Updated evidence_directives.json from research proof"
}
$snapSrc = Join-Path $repoRoot "backtest\mt5_files\Sovereign\market_snapshot.json"
$snapDst = Join-Path $filesDir "market_snapshot.json"
$pyIngest = Join-Path $repoRoot ".venv\Scripts\python.exe"
$ingestPy = Join-Path $repoRoot "python\market_ingest.py"
if (-not $SkipIngest -and (Test-Path $pyIngest) -and (Test-Path $ingestPy)) {
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $ingestOut = & $pyIngest 2>&1
    $ErrorActionPreference = $prevEap
    $ingestOut | ForEach-Object { Write-Host "[INGEST] $_" }
}
if (Test-Path $snapSrc) {
    Copy-Item $snapSrc $snapDst -Force
    Write-Host "[INSTALL] market_snapshot.json (ecosystem professor)"
}

# The EAs read every Sovereign data file with FILE_COMMON, which resolves to
# <APPDATA>\MetaQuotes\Terminal\Common\Files (shared by the terminal and every
# tester agent). Mirror the whole Sovereign folder there, otherwise rate CSVs,
# century directives and evidence directives are silently invisible to the EA.
$commonSovereign = Join-Path (Split-Path -Parent $TerminalDataDir) "Common\Files\Sovereign"
New-Item -ItemType Directory -Force -Path $commonSovereign | Out-Null
Copy-Item (Join-Path $filesDir "*") $commonSovereign -Recurse -Force
Write-Host "[INSTALL] Mirrored Sovereign data to Common\Files (FILE_COMMON path)"

$eaSources = @(
    @{ Name = "SovereignGodsEyeEA.mq5"; Path = Join-Path $repoRoot "EA\SovereignGodsEyeEA.mq5" },
    @{ Name = "SovereignEmaTrendEA.mq5"; Path = Join-Path $repoRoot "EA\SovereignEmaTrendEA.mq5" },
    @{ Name = "SovereignFxmopolEA.mq5"; Path = Join-Path $repoRoot "EA\SovereignFxmopolEA.mq5" },
    @{ Name = "SovereignOpportunityScoutEA.mq5"; Path = Join-Path $repoRoot "EA\SovereignOpportunityScoutEA.mq5" },
    @{ Name = "SovereignLondonOrbEA.mq5"; Path = Join-Path $repoRoot "EA\SovereignLondonOrbEA.mq5" }
)
foreach ($ea in $eaSources) {
    if (-not (Test-Path $ea.Path)) { continue }
    $destination = Join-Path $expertsDir $ea.Name
    Copy-Item $ea.Path $destination -Force
    Write-Host "[INSTALL] Copied EA to $destination"
}
$destination = Join-Path $expertsDir "SovereignGodsEyeEA.mq5"
if (-not (Test-Path $destination)) {
    throw "Main EA source not found: $source"
}

if ($NoCompile) {
    Write-Host "[SKIP] Compilation skipped (-NoCompile)."
    exit 0
}

# Locate MetaEditor64.exe from the terminal's origin.txt, then common install paths.
$metaEditor = $null
$originFile = Join-Path $TerminalDataDir "origin.txt"
if (Test-Path $originFile) {
    $origin = (Get-Content $originFile -Raw).Trim()
    $candidate = Join-Path $origin "MetaEditor64.exe"
    if (Test-Path $candidate) { $metaEditor = $candidate }
}
if (-not $metaEditor) {
    $candidates = @(
        "C:\Program Files\MetaTrader 5 Terminal\MetaEditor64.exe",
        "C:\Program Files\MetaTrader 5\MetaEditor64.exe",
        "C:\Program Files (x86)\MetaTrader 5\MetaEditor64.exe"
    )
    $metaEditor = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $metaEditor) {
    throw "MetaEditor64.exe not found. Compile manually with F7 in MetaEditor."
}

$log = Join-Path $env:TEMP "SovereignGodsEyeEA_compile.log"
if (Test-Path $log) { Remove-Item $log -Force }

$compileList = @("SovereignGodsEyeEA.mq5", "SovereignEmaTrendEA.mq5", "SovereignFxmopolEA.mq5", "SovereignOpportunityScoutEA.mq5", "SovereignLondonOrbEA.mq5")
foreach ($mq5 in $compileList) {
    $destMq5 = Join-Path $expertsDir $mq5
    if (-not (Test-Path $destMq5)) { continue }
    $log = Join-Path $env:TEMP ("Sovereign_" + ($mq5 -replace '\.mq5$','') + "_compile.log")
    if (Test-Path $log) { Remove-Item $log -Force }
    Write-Host "[COMPILE] $mq5"
    & $metaEditor /compile:"$destMq5" /log:"$log" /inc:"$mql5Dir" | Out-Null
    Start-Sleep -Seconds 2
    if (-not (Test-Path $log)) {
        Write-Host "[WARN] No compile log for $mq5"
        continue
    }
    $logText = Get-Content $log -Encoding Unicode
    $resultLine = $logText | Where-Object { $_ -match "Result:" } | Select-Object -Last 1
    $logText | Where-Object { $_ -match "error|warning|Result:" } | ForEach-Object { Write-Host $_ }
    $ex5 = Join-Path $expertsDir ($mq5 -replace '\.mq5$','.ex5')
    if ($resultLine -match "0 errors" -and (Test-Path $ex5)) {
        Write-Host "[PASS] $ex5"
    } else {
        Write-Host "[WARN] Compile issue for $mq5 - $resultLine"
    }
}

Write-Host '[NEXT] GodsEye: attach to any chart, SOV_RSI_BAND trades every bar. EMA trend: chart TF matches InpEmaPeriod.'
Write-Host '[INFO] Default strategy: RSI band (per-bar). Run install to copy FRED rates CSVs.'
Write-Host '[INFO] Native TCP telemetry on 127.0.0.1:5556; EA falls back if heartbeat stale.'
Write-Host '[WARN] Python feed is synthetic; do not deploy live without a validated edge.'
