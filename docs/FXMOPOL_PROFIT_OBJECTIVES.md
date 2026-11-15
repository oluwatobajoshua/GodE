# FXMOPOL profit objectives (target vs proof)

## Targets (product intent)

1. **Higher profitability** — positive expectancy after costs on validation + OOS.
2. **Fewer losers** — block or skip buckets/setups with negative R history.
3. **Better reward:risk** — average win larger than average loss (payoff ratio ↑).

These are **goals**, not guarantees. Promotion still requires 3-split monthly proof.

## How the stack pursues them

| Mechanism | Effect on losers | Effect on R:R |
|-----------|------------------|---------------|
| Baseline PA + D1 + proximity | Fewer random entries | Exits: EMA21 ride, RSI80, extension exit |
| **Performance buckets** (per setup×hour×dow) | Blocks/scales repeat losers faster on FXMOPOL | Cuts size before full SL on bad legs |
| **`fxmopol_filter_learn.py`** | Drops bad design hours/dows/reasons | Keeps higher-PF entry types only |
| **`fxmopol_adapt.py`** | Surfaces blocked buckets from live/tester CSV | — |

KPIs tracked: `loss_rate`, `payoff_ratio` (avg win / |avg loss|), `exp_bps`, `PF` in `fxmopol_learned_filters.json` → `baseline_kpis` vs `filtered_kpis_all`.

## Commands

```powershell
.\scripts\run_fxmopol_autonomous.ps1
```

Or stepwise: `run_fxmopol_playbook.ps1`, `run_fxmopol_filter_learn.ps1`.

Read: `research/results/fxmopol_filter_learn_summary.csv`, `fxmopol_learned_filters.json`.

**Autonomous run logs:** `research/results/logs/fxmopol_autonomous.log` — each step stdout/stderr, exceptions, KPI summary.  
**Run manifest:** `fxmopol_autonomous_manifest.json` (`run_id`, per-step `status`, `stderr_tail` on failure).

## Honest ceiling

H1 FXMOPOL is **not** certified deployable. Objectives above guide **continuous improvement**; certified slow premia remain **carry+momentum** until playbook clears OOS.
