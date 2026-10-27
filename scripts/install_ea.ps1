# Project Sovereign - install and compile the MQL5 EA into MetaTrader 5.
#
# Usage:
#   .\scripts\install_ea.ps1
#   .\scripts\install_ea.ps1 -TerminalDataDir "C:\Users\me\AppData\Roaming\MetaQuotes\Terminal\<id>"
#   .\scripts\install_ea.ps1 -NoCompile

param(
    [string]$TerminalDataDir,
    [switch]$NoCompile
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

$destination = Join-Path $expertsDir "SovereignGodsEyeEA.mq5"
Copy-Item $source $destination -Force
Write-Host "[INSTALL] Copied EA to $destination"

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

Write-Host "[COMPILE] $metaEditor"
& $metaEditor /compile:"$destination" /log:"$log" /inc:"$mql5Dir" | Out-Null
Start-Sleep -Seconds 2

if (-not (Test-Path $log)) {
    throw "Compilation log was not produced: $log"
}

$logText = Get-Content $log -Encoding Unicode
$resultLine = $logText | Where-Object { $_ -match "Result:" } | Select-Object -Last 1
$logText | Where-Object { $_ -match "error|warning|Result:" } | ForEach-Object { Write-Host $_ }

if (-not $resultLine) {
    throw "Compiler produced no result line. Review $log"
}
if ($resultLine -notmatch "0 errors") {
    throw "EA compilation failed: $resultLine"
}

$ex5 = Join-Path $expertsDir "SovereignGodsEyeEA.ex5"
if (-not (Test-Path $ex5)) {
    throw "Compiler reported success but no .ex5 was produced."
}

Write-Host "[PASS] Installed and compiled: $ex5"
Write-Host "[NEXT] In MT5: enable Algo Trading, open an M5 chart, attach SovereignGodsEyeEA."
Write-Host "[INFO] Native TCP telemetry is on 127.0.0.1:5556; the EA falls back if the heartbeat is stale > 3s."
Write-Host "[WARN] The Python feed is synthetic and InpRequirePythonSignal=false; do not deploy live without a validated edge."
