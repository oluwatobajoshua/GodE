# FXMOPOL pulse + optional GPT/Claude narrative (set OPENAI_API_KEY or ANTHROPIC_API_KEY).
param(
    [string]$Symbol = "GBPUSD",
    [ValidateSet("auto", "openai", "anthropic")]
    [string]$Provider = "auto"
)
$Root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $Root ".venv\Scripts\python.exe"
& $py (Join-Path $Root "research\fxmopol_llm_brief.py") --symbol $Symbol --provider $Provider
exit $LASTEXITCODE
