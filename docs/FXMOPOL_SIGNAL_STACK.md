# FXMOPOL signal stack (how the EA is “trained”)

Training is **not** one black-box model. We pre-commit a walk-forward protocol and let each symbol pick which **layers** help on unseen data.

## Five pillars

| Pillar | Live EA | Research replay |
|--------|---------|-----------------|
| **Price action** | Playbook pullbacks, stop-hunt wicks, criminal/engulf (legacy), EMA21 cross entries | `research/fxmopol_playbook.py` |
| **Support / resistance** | Swing hi/lo, fib 50–61.8%, daily pivot S1/R1 (`SovereignFxmopol.mqh` legacy path) | Optional `USE_SR_FILTER` via `research/fxmopol_signal_layers.py` |
| **Technical analysis** | EMA21 / EMA50 / SMMA100 stack, RSI(9), proximity chart grades | `fxmopol_chart_core.py` + playbook filters |
| **RSI divergence** | (Research-first; enable when profile `signal_stack.rsi_divergence` is true) | Pivot-based bull/bear div in `fxmopol_chart_core.py` |
| **Fundamental + sentiment** | `SovereignTrafficLight.mqh` macro + sentiment lights, session/spread | Optional `USE_FUND_SENTIMENT`: FRED rate diff + VIX 20d change (`data/rates/`) |

Live entries still pass **TrafficLight** (structure, momentum, macro, sentiment, room to S/R). Research stack layers mirror that split so WFO chooses rules, not story-telling.

## Walk-forward grid (per symbol)

Beyond EMA mid / proximity / stay-away / cost, the trainer toggles:

- `rsi_divergence` — extra entries/exits on regular RSI div with EMA bias
- `sr_filter` — require BabyPips-style S/R confluence on classic playbook entries
- `fund_sentiment` — skip entries that fight carry or spike into risk-off VIX

Winning combination is stored in `fxmopol_symbol_profiles.json` under `signal_stack` and `config`.

## Commands

```powershell
# Full per-symbol WFO + profile JSON (slower: larger grid)
.\scripts\run_fxmopol_symbol_train.ps1

# One symbol
.\.venv\Scripts\python.exe research\fxmopol_symbol_train.py --symbol GBPUSD

# Layer ablation table (validation split, no promotion)
.\.venv\Scripts\python.exe research\fxmopol_stack_audit.py --symbol GBPUSD
```

## What “understanding the symbol” means

The EA does **not** permanently “know” GBP vs EUR. It gets:

1. A **promoted** flag only if static hold-out OOS + chained WFO pass thresholds.
2. A **small rule vector** (EMA mid, filters, optional div/SR/macro layers).
3. Ongoing **performance governor** and optional Century adapter from trade history.

Regimes change; negative OOS still means `promoted: false` and chart-only mode when `InpEnforceSymbolProfile=true`.

## Data for fundamentals

Copy FRED CSVs to `research/data/rates/` (same as GodsEye carry). Refresh VIX via repo scripts or `research/data/rates/VIX.csv`. Without both, `fund_sentiment` configs are omitted from the grid for that symbol.

See also: [FXMOPOL_SYMBOL_TRAIN.md](FXMOPOL_SYMBOL_TRAIN.md), [FXMOPOL_CHART_SNAPSHOT.md](FXMOPOL_CHART_SNAPSHOT.md).
