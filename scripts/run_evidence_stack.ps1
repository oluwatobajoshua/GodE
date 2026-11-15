# Literature-backed FX strategies + 3-split promotion rule → evidence_directives.json
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$py = Join-Path $repo ".venv\Scripts\python.exe"
& $py (Join-Path $repo "research\fx_evidence_stack.py")
Get-Content (Join-Path $repo "research\results\fx_evidence_manifest.json") | Write-Host
