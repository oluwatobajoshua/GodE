# Research factory (study → train → test)

**Primary hypothesis:** `carry_mom_filter` (monthly, 3-split proof) — see **`docs/PROFITABLE_SLEEVE.md`**  
**Secondary hypothesis:** `GBPUSD_H1_chart_prime_professor_governor` (overlay until promoted)  
**Scalp:** no primary until `scalp_promotion_verdict.json` + MT5 tester pass

## Weekly command

```powershell
.\scripts\run_research_factory.ps1
```

Pipeline:

1. `daily_pip_budget.py` — study: pips in D1 vs strategies
2. `fxmopol_filter_learn.py --baseline prime` — train timing on design (frozen val/OOS)
3. `fxmopol_stack_pnl.py` — test H1 stack modes
4. `h1_promotion.py` — overlay_only until OOS n≥30, PF≥1.05
5. `carry_promotion.py` — core 3-split verdict
6. `carry_train_grid.py` — train cost/top_n on design only; score val/OOS
7. `profit_dashboard.py` — last 12 months stdout
8. `portfolio_combine.py` — 85/15 carry + H1 satellite monthly
9. `ea_stack_parity_check.py` — Python vs tester trades

## Strategy Tester (MT5) — when Python is not enough

**Prerequisite:** close all `terminal64.exe` windows (unless you pass `-AllowRunning`).

```powershell
# Core carry (matches carry_promotion) + GBP H1 playbook OOS + review
.\scripts\run_tester_suite.ps1

# Or separately:
.\scripts\run_lane1_mt5_truth.ps1 -NoVisual -From 2020.01.01 -To 2026.01.01
.\scripts\run_fxmopol_tester.ps1 -Profile fxmopol_gbp_playbook -From 2024.07.01 -To 2026.01.01 -NoVisual
```

**Clear results:** open `backtest\reports\*.htm` (equity at top) + run `research\fxmopol_tester_review.py` on the GBP report.

The weekly **Python** factory can run without MT5; schedule **tester suite** after each EA/profile change or monthly.

## Artifacts

| File | Purpose |
|------|---------|
| `configs/research_hypothesis_active.json` | Active queue + verdicts |
| `research/results/research_factory_manifest.json` | Last run log |
| `research/results/carry_promotion_verdict.json` | Core sleeve pass/fail |
| `research/results/h1_promotion_verdict.json` | Satellite role |
| `research/results/carry_train_pick.json` | Design-winner cost params |
| `research/results/profit_dashboard.json` | Combined snapshot |

## Good result definition

- **Carry:** all three monthly splits sum%>0, PF≥1.0 (`carry_promotion.py`)
- **H1:** `h1_promotion.py` promoted OR remain **overlay_only**
- **Combined:** `portfolio_combined_monthly.csv` for desk view — not live until you change FROZEN policy

See also `docs/H1_PROMOTION.md`, `docs/FXMOPOL_STACK_PNL.md`.
