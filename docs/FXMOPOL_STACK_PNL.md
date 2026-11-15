# FXMOPOL full-stack P&L (EA parity)

Professional discipline: **prime liquidity sessions** + **carry/VIX fund filter** + **professor blocks** + **walk-forward governor**.

```powershell
.\scripts\run_stack_pnl.ps1 -Symbol GBPUSD
# or
.\.venv\Scripts\python.exe research\fxmopol_stack_pnl.py --symbol GBPUSD
```

## Modes (GBPUSD EMA50, HistData H1)

| Mode | What it is |
|------|------------|
| `chart_loose` | All hours; fund filter on |
| `chart_prime` | London/overlap + Tue–Thu (matches promoted tester inputs) |
| `professor_stack` | Prime + USD event / spread / macro blocks + risk scaling |
| `professor_governor` | Stack + walk-forward performance governor |

Artifact: `research/results/fxmopol_stack_pnl_manifest.json`  
Monthly CSVs: `research/results/stack_GBPUSD_mid50_*_monthly.csv`

## Unified dashboard

```powershell
.\scripts\run_profit_dashboard.ps1
```

Writes `research/results/profit_dashboard.json` and combines carry + stack last 12 months.

## H1 promotion

```powershell
.\.venv\Scripts\python.exe research\h1_promotion.py
```

Verdict: `research/results/h1_promotion_verdict.json` — **overlay_only** until OOS n≥30 and PF≥1.05.

## Honest allocation (hedge-fund view)

- **H1 FXMOPOL** after stack wiring: near **flat** full sample on GBP; OOS still **low N** — **not** a deployable alpha sleeve alone.
- **D1 carry + momentum** (`carry_mom_filter`, `lanes_summary.json` Lane 1) remains the only **3-split positive** FX sleeve in repo — use for slow premia capital; use FXMOPOL stack for **timed expression** only when gates allow.

## Tester alignment

`fxmopol_gbp_playbook` profile: `InpGoodSessionsOnly=true`, `InpWeekdayMask=14`, `InpEcosystemProfessor=true`.
