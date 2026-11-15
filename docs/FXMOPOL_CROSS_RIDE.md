# FXMOPOL cross-and-ride — uptrend, downtrend, sideway

## Three regimes (your chart words)

| Your term | Code | What it looks like | EA response |
|-----------|------|-------------------|-------------|
| **Up-trade / uptrend** | `UPTREND` | EMA21 **above** EMA50, close **above** EMA21, fan open, EMA21 sloping up | **Long only** — cross, reclaim, pullback adds |
| **Down-trend** | `DOWNTREND` | EMA21 **below** EMA50, close **below** EMA21, fan open, slope down | **Short only** |
| **Wide-way / sideway** | `SIDEWAY` | Price **weaving SMMA100**, **compressed** EMA fan, **2+ EMA21 crosses** in 4 bars | **No new trades**; close if open trade fights regime |

Python: `market_regime_at()`, `market_context_at()`. EA: `MarketContext()` when `InpUseRegimeFilter=true`.

### Quality (stay away vs don’t leave money on the table)

| Quality | Meaning | New entries | Open winners |
|---------|---------|-------------|--------------|
| **CHOPPY** | EMA21 ping-pong, bad whipsaw | **None** | Flat if possible |
| **SIDEWAY** | Range on SMMA, flat fan | **None** | **Hold** — pause, not always exit |
| **BAD_TREND** | Direction ok but thin fan / chop / RSI | **One** careful cross/reclaim only | Hold until chop or flip |
| **GOOD_TREND** | Stack + fan + RSI | Cross, reclaim, **pullback adds** | Ride to RSI 75 / extension / structure |

In **GOOD_TREND**, strict stay-away is relaxed for early EMA21 crosses so we don’t miss the move.

Inside a trend you still get **pullback** (tag EMA21), **retracement** (close under EMA21 but over EMA50 — hold), **correction** (lose EMA50 or trigger structure — exit all).

---

# FXMOPOL cross-and-ride — trend, pullback, retracement, correction

Project **FROZEN for live**. This spec drives `research/fxmopol_cross_ride.py` and
`fxmopol_chart_core.trend_phase_at()`.

## Definitions (H1, EMA21 / EMA50 / SMMA100)

| Phase | Bullish (macro up) | What you do |
|-------|-------------------|-------------|
| **CHOP** | Price weaving SMMA100, no fan | Flat — no new risk |
| **TREND** | Close above EMA21, EMA21 > EMA50, riding away from EMA21 | Hold winners; initial reclaim entries |
| **PULLBACK** | Still above EMA21; low **tags** EMA21 (~0.35×ATR) | **Add** on retest (re-enter in direction) |
| **RETRACEMENT** | Close **below EMA21** but still **above EMA50** | **Hold** — reds can test EMA21 without exit |
| **CORRECTION** | Close below EMA50 **or** red closes below **trigger candle open** | **Close all** legs in that direction |

Bearish phases mirror (close below fan, pullback = retest from below, etc.).

## Trade mechanics

1. **Trigger entry:** After ≥2 closes on wrong side of EMA21, **reclaim** bar (green up / red down)
   closes through EMA21 → SL = trigger wick (low/high).
2. **Do not exit** on a single close back through EMA21 if still **retracement** (above EMA50).
3. **Structure exit:** red below trigger **open** (long) — your “the trend is actually breaking”.
4. **Bulk exit:** RSI 75/25, EMA extension, engulf after far counter candles.
5. **Pyramid:** Only in **PULLBACK** phase, not during retracement or correction.

## Code map

- `macro_trend_dir()` — stack or EMA21 vs EMA50 + close vs EMA50
- `trend_phase_at()` — CHOP / TREND / PULLBACK / RETRACEMENT / CORRECTION
- `allow_trend_entry()` / `allow_pullback_add()` / `correction_exit()`

## Research outputs

```powershell
.\scripts\run_fxmopol_cross_ride.ps1
```

- `research/results/fxmopol_cross_ride_summary.csv` — by symbol × split
- `cross_follow_through_*.csv` — forward returns after EMA21 cross **with macro trend aligned**
- `*_monthly.csv` per split

## EA

Full phase machine is **research-first**. `SovereignFxmopol.mqh` uses partial cross/ride logic;
port correction + trigger-open exits after OOS proof.
