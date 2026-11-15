# Store an LLM API key as a persistent Windows user environment variable.
#
# Two ways to configure a key, pick one:
#
#   1. .env file (recommended, repo-local, easy to rotate)
#        Copy-Item .env.example .env
#        notepad .env
#
#   2. This script (user-level env var, survives terminal restarts)
#        .\scripts\set_llm_key.ps1 -Provider openai -Key "sk-..."
#
# Real environment variables win over .env, so this script overrides the file.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("openai", "anthropic")]
    [string]$Provider,

    [Parameter(Mandatory = $true)]
    [string]$Key
)

$ErrorActionPreference = "Stop"

$name = if ($Provider -eq "openai") { "OPENAI_API_KEY" } else { "ANTHROPIC_API_KEY" }

if ([string]::IsNullOrWhiteSpace($Key)) {
    throw "Key is empty."
}

[Environment]::SetEnvironmentVariable($name, $Key, "User")
Set-Item -Path "Env:$name" -Value $Key

$masked = if ($Key.Length -gt 8) { $Key.Substring(0, 6) + "..." } else { "***" }
Write-Host "Set $name = $masked for the current user."
Write-Host "Current session updated. New terminals will inherit it automatically."
Write-Host ""
Write-Host "Verify:"
Write-Host "  .\.venv\Scripts\python.exe research\sovereign_env.py"
