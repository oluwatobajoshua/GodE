# FXMOPOL chart snapshot — 2026-11-08

Saved baseline for fine-tuning. Primary charts: **USDJPY H1** (mobile), **EURUSD H1** (desktop example Nov 2–6).

## Canonical indicator stack (user settings)

| Line | Indicator | Period | Method | Apply to | Chart colour |
|------|-----------|--------|--------|----------|--------------|
| Fast | Moving Average | **21** | **Exponential** | Close | Aqua / light blue |
| Medium | Moving Average | **50** | **Exponential** | Close | Gold / yellow |
| Slow | Moving Average | **100** | **Smoothed (SMMA)** | Close | White |
| Momentum | RSI | **9** | Close | Levels **20 / 50 / 80** |
| Structure | Fractals | default | Bill Williams | all TFs | Up/down arrows |

**RSI levels (fixed):** 20 oversold, **50 bias line** (long only when RSI > 50 in uptrend; short when RSI < 50 in downtrend), 80 overbought.

**Order in uptrend:** Close > EMA21 > EMA50 > SMMA100. Downtrend: reverse.

> USDJPY mobile screenshots also showed EMA **100** (blue) instead of 50 — treat **50** as default medium; use `InpSlowEma=100` to match JPY template.

## Trader playbook — rules (discretion → code target)

### Regime (when to look for trades)

- **Long regime:** H1 **close above all three MAs**, stacked 21 > 50 > 100.
- **Short regime:** H1 **close below all three MAs**, stacked 21 < 50 < 100.
- **Flat:** Price weaving through **white SMMA100** (chop near ~1.1180 on EUR example) — **no chase**.

### Entry (with arrow / criminal confluence)

- **Long:** In long regime, enter on **(a)** first pullback to **EMA21** that **holds** (bullish close, up-arrow bar), or **(b)** **stop-hunt** (long lower wick) that **reclaims** EMA21 while still **above SMMA100**, RSI **> 50** and **< 80**.
- **Short:** In short regime, enter on **(a)** retest of **EMA21 from below** (bearish close, down-arrow bar), or **(b)** breakdown candle after stack flips, RSI **< 50** and **> 20**.

### Exit (profit protection)

- **Long exit:** First H1 **close below EMA21** (trail logic) **or** TP at prior swing high / manual level **or** RSI tags **80** then rolls over (optional scale).
- **Short exit:** First H1 **close above EMA21** **or** TP at extension when RSI **< 30** (e.g. ~26 on EUR chart) **or** measured target.

### Risk

- SL: below hunt low (long) or above hunt high (short), typically **1× ATR(14)** minimum.

---

## Chart note — EURUSD H1 late breakdown (RSI ~26, ~1.1197)

**Can we still be profitable here?** With your rules, at this **exact** bar:

| Question | Answer |
|----------|--------|
| **New short?** | **No** — price already extended; RSI **≤ 30** is **cover zone**, not fresh short zone (levels 20/50/80). |
| **New long?** | **Not yet** — need **close back above EMA21** + stack forming + RSI **> 50**. |
| **If short from ~1.1220 breakdown?** | **Yes — take profit here** (~1.1197). That is the profitable action. |
| **Fractal down arrow at the low?** | Warning of **bounce risk**, not a signal to add size. |

HistData prices differ by year from broker screenshots (~1.12 vs ~1.08 in Oct 2024 file); **logic is the same**: late + oversold = **exit winners**, don’t chase.

**Certified edge:** still **GodsEye monthly carry** (`best_win_settings.json`). EUR H1 playbook is **not OOS-certified**.

---

## Worked example — EURUSD H1 (2–6 Nov)

| Phase | Price context | Trader action |
|-------|---------------|---------------|
| **Nov 2–3** | Range ~**1.1180**, crosses white SMMA | **Stay flat** — stack not aligned |
| **Long window** | Late **3 Nov / early 4 Nov**: break, **stays above** all MAs, up arrows into **~1.1266** | **Enter long** ~**1.1195–1.1210** on EMA21 pullback or first clean hold above stack; RSI crosses **above 50** |
| **Long exit** | Peak **~1.1266** then loss of momentum | **Exit** ~**1.124–1.126** (near high) **or** on first **close below EMA21** before dump |
| **Short window** | **Mid 5 Nov**: **below** aqua/gold/white, down arrows | **Enter short** ~**1.120–1.121** on retest of EMA21 from below after stack bearish |
| **Short exit** | Flush to **~1.1136**, RSI **~26** (oversold) | **Cover** ~**1.114–1.115** (RSI < 30 / extension) **or** on **close above EMA21** |

**Illustrative long P&L (mid entries):** ~1.120 → ~1.125 ≈ **+50 pips** on one Nov 4 leg.  
**Illustrative short P&L:** ~1.121 → ~1.114 ≈ **+70 pips** on Nov 5–6 leg.  
*(Not a backtest guarantee — example from chart narrative for rule coding.)*

---

## Worked example — USDJPY H1 (4–6 Nov, mobile)

- Regime: hours **above** EMA21 / EMA100 / SMMA100 after hunt from **~157.24**.
- **Long entry:** criminal / stop-hunt **reclaim** above brown SMMA, RSI **> 50** (~67 on screenshot).
- **Target / decision:** **158.500** horizontal.
- **Exit:** fail **close below EMA21** or rejection at **158.50**.

Set `InpManualLevel=158.500` on JPY.

---

## Price action implementation status (2026-11-08)

| Chart rule | EA (`SovereignFxmopol.mqh`) | Research (`fxmopol_chart_core.py` / `fxmopol_playbook.py`) |
|------------|---------------------------|-------------------------------------------------------------|
| Stack regime ≥ M bars | `StackRegimeOk`, `InpStackRegimeBars` | `regime_streak`, `REGIME_BARS` |
| Stop-hunt wick ≥ 2× body | `WickHuntReclaim` | `stop_hunt_reclaim` |
| Hunt + close > EMA21 + SMMA100 | `WickHuntReclaimStack` | `stop_hunt_reclaim_stack` |
| Pullback / retest entries | `PlaybookPullbackLong` / `PlaybookRetestShort` | `pullback_long` / `retest_short` |
| Fractal + hunt (optional) | `PlaybookChartEntry` + `InpUseFractals` | `playbook_chart_entry` + `USE_FRACTAL_CONFLUENCE` |
| Full PA mode (not body-only Criminal) | `InpUseFullPriceAction` | `playbook_chart_entry` |
| Exit EMA21 break (playbook) | `OnTick` 2-bar break | `TREND_RIDE_EXIT` |
| Exit RSI ≥ 80 / cover ≤ 30 | `m_rsiCoverLevel`, `InpRsiCoverLevel` | `RSI_COVER_LEVEL` |
| Exit RSI 80 rollover | `rsi[1]>=80 && rsi[0]<75` | `exit_rsi80_rollover` |
| Exit manual level (e.g. 158.5) | `LevelTpHit`, `InpUseLevelTp` | `MANUAL_LEVELS` |
| EMA50 medium on EUR | `InpSlowEma=50` default | `ema_mid=50` |
| Good sessions (new entries only) | `InpGoodSessionsOnly` → 07–11 & 13–17 | `PRIME_SESSION_WINDOWS` / `session_allows_new_entry` |

**Server time:** MT5 uses broker bar hour; HistData H1 is GMT. Align offset before live use.

Parity check: `python research/fxmopol_price_action_parity.py` → `fxmopol_price_action_parity.json`.

**Still not certified:** full-sample OOS promotion on EUR playbook; streak preset ≠ full playbook table. Re-score after changes:

```powershell
.\scripts\run_fxmopol_playbook.ps1
.\scripts\install_ea.ps1
```

---

## Saved monthly results (research CSVs)

| Sleeve | File |
|--------|------|
| Carry (promoted) | `research/results/carry_mom_filter_monthly.csv` |
| Stack tune | `research/results/stack_tune_default_monthly.csv` |
| EMA streak | `research/results/ema_streak_ride_monthly.csv` |
| Bundle | `research/results/monthly_returns.csv` |

### Last 12 months — carry_mom_filter (%)

| Month | Return % |
|-------|----------|
| 2025-10 | +2.89 |
| 2025-11 | +3.41 |
| 2025-12 | +3.88 |
| 2026-01 | +3.67 |
| 2026-02 | +1.62 |
| 2026-03 | −2.36 |
| 2026-04 | +3.67 |
| 2026-05 | +2.16 |
| 2026-06 | −3.25 |
| 2026-07 | +0.70 |
| 2026-08 | +4.80 |
| 2026-09 | −4.82 |

**12m sum:** ~+18.4% · **Win months:** 9/12

---

## EMA distance filters (enter near 21, exit on stretch)

| Filter | Rule (ATR = H1 ATR14) | Purpose |
|--------|------------------------|---------|
| **Entry proximity** | \|close−EMA21\| ≤ **0.40** or wick touch ≤ **0.55** | Buy/sell **close to the 21** like your chart |
| **EMA fan** | min(21−50, 50−SMMA100) ≥ **0.12** ATR each | Skip compressed/chop stacks |
| **EMA21 slope** | 3-bar slope ≥ **0.02** ATR | Trend still alive |
| **Chase cap** | Close not > **2.8** ATR above SMMA100 | No late chase |
| **SMMA chop** | Near white line + tight fan → **no entry** | Jul 2020 tangle |
| **Extension exit** | After **≥4** bars & **≥0.35** ATR profit: exit if \|close−EMA21\| ≥ **1.15** ATR | Take profit when candle **stretches** off 21 without panic-closing winners |

Python: `fxmopol_chart_core.passes_entry_filters` · EA: `InpUseProxFilters=true` on `SovereignFxmopolEA`.

---

## EA mapping

- **MT5 template:** `templates/fxmopol.tpl` (EMA21 gold, EMA50 aqua, SMMA100 white, RSI9, fractals).
- **Default preset:** `InpPreset=PRESET_EUR_CHART_ONLY` — matches your chart, **no auto entries** (EUR OOS negative).
- **Winning intraday presets:** `PRESET_USDJPY_STREAK` (OOS+), `PRESET_GBP_PLAYBOOK` (OOS+ proximity only).
- **Chart:** H1 `SovereignFxmopolEA`, `InpVisualMobileSetup=true`.
- **MAs:** `InpEmaPeriod=21`, `InpSlowEma=50`, `InpSmmaPeriod=100`.
- **RSI:** `InpRsiPeriod=9`; levels **50 / 20 / 80**.
- **JPY level:** `InpManualLevel=158.500`.

```powershell
.\scripts\save_research_snapshot.ps1
```

Manifest: `research/results/snapshot_manifest.json`

Assets: workspace `assets/*image*` and WhatsApp chart captures.
