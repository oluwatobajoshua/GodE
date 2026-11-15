# Prophet horizons — attach any TF, wake to proof

## Research goal (your framing)

Attach the EA on **M5, M15, H1, H4, or D1**, let **ecosystem + Prophet + playbook** run while you sleep,
wake and read **daily / monthly P&L tables** — same discipline as a desk, automated.

Sovereign does not skip proof: each timeframe is a **sleeve** with its own OOS monthly CSV before promotion.

## How “sleep profitability” works in this repo

| Layer | Overnight behaviour |
|-------|---------------------|
| `century_daemon` + `market_ingest` | Refreshes professor + prophet JSON |
| FXMOPOL / GodsEye on chart TF | Entries/exits on **closed bars** of that TF |
| `SovereignPerformance` | Learns from every closed trade |
| `prophet_multitf.py` | Supplies `p_up` per TF for gates (research) |
| Reporting | `*_daily.csv`, `*_monthly.csv` — morning readout |

## Horizon ladder

Seconds → yearly are separate **expiry labels** (binary-style: “up before H elapses”).  
See `research/prophet_multitf.py` (H1/D1 today; M5 next; ticks when data lands).

## Morning checklist

```powershell
Get-Content research\results\logs\fxmopol_autonomous.log -Tail 40
.\.venv\Scripts\python.exe python\prophet_analyst_brief.py --symbol GBPUSD
.\scripts\run_period_results.ps1
```

Profitability is **declared** only when the monthly file for that TF+sleeve is positive OOS — otherwise preserve the negative row and keep researching.
