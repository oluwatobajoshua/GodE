# Sovereign M1 RSI Research — Complete Documentation

## Project Context

- **Path:** `C:\xampp\htdocs\GodE`
- **AGENTS.md status:** `RESEARCH-READY. Not cleared for live.` — tester blocker removed; Python validation on `research/data/mt5/` sufficient for research_good/desk_good; forward demo needed for `live_good`
- **Default EA mode:** `carry_mom_filter` (D1 monthly carry + 12m momentum), only survivor of M5 intraday tests (-355.79R PF 0.75)
- **RSI band mode:** `SOV_RSI_BAND` (H1 overlay) — EURUSD certified net (+4.83/+4.72/+2.02%, Sharpe 0.44 OOS, DD -6.95%)
- **Python entry:** `.\.venv\Scripts\python.exe <script.py>` (ASR blocks pip.exe)

## M1 Data Available

| Symbol | Bars | Span | File |
|--------|------|------|------|
| EURUSD | ~100,000 | 2026-08-07 → 2026-11-12 (~97 days) | `research/data/mt5/EURUSD_M1.csv` |
| GBPUSD | ~100,000 | same | `research/data/mt5/GBPUSD_M1.csv` |
| USDJPY | ~100,000 | same | `research/data/mt5/USDJPY_M1.csv` |

CSV columns: `time,open,high,low,close,tick_volume,spread`

**Limitation:** Only ~97 days of M1 broker history. Not enough for robust OOS validation. HistData would be needed for longer M1/M5 testing.

## M1 Research Results

### File: `research/results/rsi_band_m1_comprehensive.csv`

Tested 486 configs per symbol across 3 entry styles × 3 deadbands × 3 min-hold × 3 margins × 2 ATR-stop × 3 time-stop:

| Symbol | Best Style | Best Config | Net Total | RT | Sharpe | Max DD |
|--------|-----------|-------------|-----------|-----|--------|--------|
| EURUSD | pullback | db=0.1, mh=12, margin=0, atr=F, ts=60 | +0.23% | 198 | 1.32 | -0.35% |
| GBPUSD | pullback | db=0.1, mh=6, margin=1, atr=T, ts=∞ | -0.10% | 117 | -0.78 | -0.43% |
| USDJPY | pullback | db=0.1, mh=3, margin=3, atr=F, ts=∞ | +0.36% | 380 | 1.21 | -0.70% |

### File: `research/results/rsi_band_m1_meanrev.csv`

Tested mean reversion from RSI extremes (20/50/80 template levels): RT=1 for all configs — entry too restrictive, essentially no trades.

### Conclusion on M1

- **Edge is negligible:** EURUSD +0.23% and USDJPY +0.36% over 97 days is not viable. GBPUSD is negative.
- **Spread cost dominates:** At M1, you take 200-400 round trips over 97 days. Spread (1-5 pts) + commission eats the edge.
- **Pullback style is the only viable M1 entry:** EMA21>EMA50 trend filter + buy RSI dip to ~40 crossing up / sell RSI rally to ~60 crossing down. Exit on RSI cross back through 50 or slope flip.
- **Not deployable:** Returns too small, data span too short, no OOS split possible with 97 days.

## Previous Timeframe Results (for reference)

| Timeframe | EURUSD | GBPUSD | USDJPY |
|-----------|--------|--------|--------|
| M1 | ❌ +0.23% (barely) | ❌ -0.10% | ❌ +0.36% (barely) |
| M5 | ❌ All negative | ❌ All negative | ❌ Best OOS +1.46% (not all 3 splits) |
| M15 | ❌ All negative | ❌ All negative | ✅ +3.81/+2.05/+1.46%, Sharpe 0.47, DD -4.50% |
| M30 | ❌ All negative | ✅ +1.72/+4.98/+3.12%, Sharpe 0.57, DD -3.16% | ❌ Best OOS +2.23% (not all 3 splits) |
| H1 | ✅ +4.83/+4.72/+2.02%, Sharpe 0.44, DD -6.95% | ❌ Fails | ❌ Fails |

## Files Created in This Session

- `research/rsi_band_m1_meanrev.py` — mean reversion from RSI 20/50/80 on M1 (RT=1, not useful)
- `research/rsi_band_m1_comprehensive.py` — full M1 sweep (pullback/trend/meanrev styles)
- `research/results/rsi_band_m1_comprehensive.csv` — full results
- `research/results/rsi_band_m1_meanrev.csv` — mean reversion results
- `check_m1_rsi.py` — RSI level distribution diagnostic on M1

## EA Implementation Status

### `EA/SovereignGodsEyeEA.mq5`
- `SOV_COMPRESSION_RUBBERBAND = 6` in `SovereignStrategyMode` enum
- `InpRsiBandMaxBars` default 24 (time-based exit)
- `InpRsiBandAutoDetect` default true
- `InpRequirePythonSignal = false` (EA computes own z-scores)
- M15/M30 enabled (removed `_Period == PERIOD_H1` restriction for RSI band)
- MinHoldBars 12→24 default
- AutoDetect template levels 20/50/80

### `EA/Include/SovereignRSIBand.mqh`
- Time-based exit via `m_maxBars`
- Auto-detect per-symbol params
- Timeframe-agnostic (uses chart timeframe)
- EMA50 handle for trend filter

### `EA/Include/SovereignCompressionRubber.mqh`
- 403-line compression rubberband MQL5 include (SOV_COMPRESSION_RUBBERBAND)

## How to Run Research Scripts

```powershell
.\.venv\Scripts\python.exe research\rsi_band_m1_comprehensive.py
.\.venv\Scripts\python.exe research\rsi_band_m1_meanrev.py
.\scripts\run_strategy_certification.ps1
.\scripts\run_carry_book.ps1
```

## Next Steps (if user wants to pursue M1)

1. **Get longer M1 data** — HistData M1 CSV for at least 1-2 years to enable proper design/validation/OOS splits
2. **Test pullback style on M15/M30** — the pullback entry (RSI dip to 40/60) might work better on higher timeframes
3. **Implement M1 in EA** — only if edge improves with more data; current +0.23%/+0.36% is not deployable
4. **Revisit `fxmopol.tpl` alignment** — verify template params match EA auto-detect inputs

## Python Dependencies (verified)

- zmq 27.2.0, pandas, numpy, lightgbm 4.7.0, torch 2.15.0+cpu, matplotlib 3.11.2
