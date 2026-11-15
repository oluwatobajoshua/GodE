# Sovereign Prophet — multi-timeframe next-move research

**North star:** fuse the full ecosystem and predict the **next bar direction** on **M5, H1, H4, D1** with
walk-forward proof. The EA reads `prophet_forecasts.json` in milliseconds after ingest — research decides
whether the signal survives **design → validation → OOS** monthly tables.

## Research loop (not marketing)

1. **Labels** — causal forward return on bar `t` (enter next open; label from `close[t+1]`).
2. **Features** — everything the professor knows at `t`: TA stack, vol, session, carry, VIX, COT (lagged),
   event distance, cross-section rank, FXMacro risk (marked non-PIT where needed).
3. **Models** — start logistic ridge per TF; promote only if OOS monthly expectancy improves.
4. **Deploy** — winning weights → `research/results/prophet_manifest.json` + live `prophet_forecasts.json`.

## Data required (research inventory)

| Need | Path | MTF use |
|------|------|--------|
| Bars | `research/data/{SYM}_{M5,H1,D1}.csv` | All horizons |
| Rates / VIX | `research/data/rates/` | Slow features |
| COT | `research/data/cot/` | Positioning |
| Events | `research/data/fxmacrodata/calendar_*.csv` | Event windows |
| Cross-asset | `DXYUSD_M5.csv`, `XAUUSD_M5.csv` | Regime |

Gaps to close for maximal predictability (institutional feeds):

- FX options risk reversal / skew
- Bank FX flow / CLS volume
- EM credit / EUR HY vs IG
- Tick L2 / order book (micro predictor for “split second”)

## Commands

```powershell
.\scripts\run_prophet_research.ps1
.\scripts\run_market_ingest.ps1   # merges latest prophet_forecasts.json when present
```

Outputs: `research/results/prophet_multitf_summary.csv`, `prophet_manifest.json`, `prophet_*_monthly.csv`.
