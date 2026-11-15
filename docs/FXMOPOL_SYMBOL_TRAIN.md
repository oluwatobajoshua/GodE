# How an EA should be trained (FXMOPOL implementation)

Industry practice for MetaTrader (see [MQL5 walk-forward article](https://www.mql5.com/en/articles/3279), [overfitting guide](https://www.mql5.com/en/blogs/post/756386)) — **not** “train a neural net once and forget.”

## Pre-committed protocol (do not change after seeing results)

| Step | What we do |
|------|------------|
| **1. Few parameters** | Grid: EMA mid, proximity, stay-away, cost bps, plus signal stack toggles (RSI div, S/R, fund+sentiment) — see [FXMOPOL_SIGNAL_STACK.md](FXMOPOL_SIGNAL_STACK.md) |
| **2. Walk-forward** | 18mo in-sample → optimize pick; 3mo out-of-sample → record; roll 3mo |
| **3. Fitness** | **Sharpe on trade returns** on IS (not raw net profit) |
| **4. Deployment pick** | Config with **best median IS Sharpe** across WFO windows |
| **5. Static hold-out** | Data **after** WFO end is **never** used in WFO; used once for promotion |
| **6. Plateau** | Reject if validation Sharpe >2.5× neighbour configs (curve-fit warning) |
| **7. Promotion** | Static OOS: n≥40, exp_bps≥0, pf≥1 **and** WFO chain exp_bps≥0, WFE≥0.25 |
| **8. Optional ML** | Logistic **skip** gate on design only (`--ml`); **no** deep learning |

## What we do **not** do

- Neural networks on ~1k trades (overfit).
- Optimize window sizes or fitness after seeing equity.
- Retune when static OOS fails (“just one more grid”).
- Trade symbols with `promoted: false` when `InpEnforceSymbolProfile=true`.

## Commands

```powershell
# Refresh broker history (terminal logged in)
.\scripts\export_btcusd_h1.ps1
.\.venv\Scripts\python.exe scripts\export_rates.py --symbol GBPUSD --timeframe H1

# Train all H1 CSVs + walk-forward
.\scripts\run_fxmopol_symbol_train.ps1

# One symbol (faster)
.\.venv\Scripts\python.exe research\fxmopol_symbol_train.py --symbol GBPUSD

# Install manifest into MT5
.\scripts\install_ea.ps1
```

## EA

`SovereignFxmopolEA.mq5` + `fxmopol_symbol_profiles.json` under `MQL5/Files/Sovereign/`.

## Outputs

- `research/results/fxmopol_symbol_profiles.json` — trade gate per symbol
- `research/results/fxmopol_symbol_wfo_windows.json` — audit trail per window
- `research/results/fxmopol_symbol_train_manifest.json` — short summary
