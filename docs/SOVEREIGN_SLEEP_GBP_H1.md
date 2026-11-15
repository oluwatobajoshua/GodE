# Sleep profile — GBPUSD H1 (alive stack)

**Research only.** One symbol, one timeframe, full brain overnight.

## Before bed (one command)

```powershell
.\scripts\run_sleep_gbp_h1.ps1 -Prepare -Install
```

Runs Prophet + Professor ingest + trade adapt, writes manifests, copies JSON to MT5 `Common\Files\Sovereign`.

Optional daemon (hourly refresh):

```powershell
.\scripts\start_century_stack.ps1
```

## On chart

1. **GBPUSD H1**
2. Template **fxmopol.tpl**
3. **SovereignFxmopolEA** — preset **GBP_PLAYBOOK**
4. `InpEcosystemProfessor = true` (default in tester profile `fxmopol_gbp_playbook`)

## Morning

```powershell
.\scripts\run_sleep_gbp_h1.ps1 -Morning
```

Analyst brief + brain file checklist + prophet monthly tail + autonomous log tail.

Manifest: `research/results/sovereign_sleep_gbp_h1_manifest.json`

## Proof (not live P&L fantasy)

```powershell
.\scripts\run_fxmopol_tester.ps1 -Profile fxmopol_gbp_playbook -NoVisual
.\scripts\run_period_results.ps1
```

Profitability = positive **OOS monthly** for this sleeve; until then, the organism still learns from every tester/live close.
