# GBP H1 sleep stack — refresh brain before bed, analyst brief in the morning.
param(
    [switch]$Prepare,
    [switch]$Morning,
    [switch]$Install,
    [string]$TerminalDataDir = "$env:APPDATA\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1"
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not $Prepare -and -not $Morning) {
    $Prepare = $true
    $Morning = $true
}
$mode = if ($Prepare -and $Morning) { "full" } elseif ($Morning) { "morning" } else { "prepare" }
& $py (Join-Path $Root "python\sovereign_sleep_stack.py") $mode
$code = $LASTEXITCODE
if ($Install -and $Prepare) {
    Write-Host "[sleep] install EAs + copy brain JSON to Common\Files..."
    & (Join-Path $Root "scripts\install_ea.ps1") -TerminalDataDir $TerminalDataDir
}
exit $code
