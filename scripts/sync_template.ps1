# Publish an MT4/MT5 .tpl template as a flat ASCII spec the EA can read at runtime.
#
# Why this exists: MQL5 FileOpen is sandboxed to MQL5\Files and Common\Files. It
# cannot read Profiles\Templates, so the EA cannot open the .tpl directly. This
# converts the template into a KEY=VALUE spec and writes it where the EA is
# allowed to look, so a template change reaches the EA without a recompile.
#
#   .\scripts\sync_template.ps1                     # one shot
#   .\scripts\sync_template.ps1 -Watch              # republish on change
#   .\scripts\sync_template.ps1 -Tpl templates\my.tpl -Name my
#
# The EA polls the spec's modify time and reloads itself when it changes.

[CmdletBinding()]
param(
    [string]$Tpl  = "templates\fxmopol.tpl",
    [string]$Name = "",
    [switch]$Watch,
    [int]$IntervalSec = 5
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "python not found at $python - create the venv first (see AGENTS.md)"
}

$common = Join-Path $env:APPDATA "MetaQuotes\Terminal\Common\Files"
$outDir = Join-Path $common "Sovereign\template"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$tplPath = if ([System.IO.Path]::IsPathRooted($Tpl)) { $Tpl } else { Join-Path $root $Tpl }
if (-not (Test-Path $tplPath)) {
    throw "template not found: $tplPath"
}
if ([string]::IsNullOrWhiteSpace($Name)) {
    $Name = [System.IO.Path]::GetFileNameWithoutExtension($tplPath)
}
$iniPath = Join-Path $outDir "$Name.ini"

function Publish {
    & $python (Join-Path $root "research\chart_template.py") --tpl $tplPath --ini $iniPath --name $Name | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "chart_template.py failed with exit $LASTEXITCODE"
    }
    $stamp = (Get-Date).ToString("HH:mm:ss")
    Write-Host "[$stamp] published $tplPath -> $iniPath"
}

Publish

if ($Watch) {
    Write-Host "watching $tplPath (every ${IntervalSec}s, Ctrl+C to stop)"
    $last = (Get-Item $tplPath).LastWriteTimeUtc
    while ($true) {
        Start-Sleep -Seconds $IntervalSec
        $now = (Get-Item $tplPath).LastWriteTimeUtc
        if ($now -ne $last) {
            $last = $now
            Publish
        }
    }
}
