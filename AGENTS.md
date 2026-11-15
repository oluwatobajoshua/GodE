# AGENTS.md — Project Sovereign

## Status: RESEARCH-READY. Not cleared for live.

Every M5 intraday strategy was proven negative on independent out-of-sample data
(HistData 2005-2025). The project was restructured on 2026-10-27 from a single-pair
M5 intraday EA to a **daily cross-sectional mean-reversion + carry*** portfolio
manager. The **default EA mode** is now **RSI band** (`SOV_RSI_BAND`, per-bar
trading on any timeframe), switchable to **monthly carry + 12m momentum**
(`carry_mom_filter`, `InpStrategy=SOV_CARRY_MOM`) — the only research variant
positive in design, validation, and OOS (~+1.6% CAGR full period, Sharpe ~0.20,
**-26% max DD** — still not deployable). Daily xsec mean-rev remains available
as `InpStrategy=SOV_XSEC_MR`.

**New since 2026-11-12:**
- **The book passes all three splits for the first time.** carry D1 + RSI band
  EURUSD H1 + RSI band GBPUSD H1, each RSI leg traded as the equal-weight
  **ensemble of its whole config family** (not the best-in-sample config):
  2018-01→2026-11, design **+3.30%** / validation **+2.93%** / OOS **+2.75%**,
  full CAGR +1.03%, Sharpe 0.66, **maxDD -2.37%** against carry alone at -26%.
  The previous "-1.72% design" was a data-window artifact (broker M30 history
  stops at 2018-10) plus a knob-selection overfit — both fixed. Full detail and
  the post-mortem below.
- **RSI band ride ported to EA** (`SOV_RSI_BAND`, any TF): min-hold 12, margin 3,
  spread gate p90. EURUSD H1 net: +4.83 / +4.72 / +2.02% (D/V/OOS), Sharpe 0.44,
  DD -6.95% (RSI 9, mid 50, hi 75, lo 25). GBPUSD M30 +1.72 / +4.98 / +3.12%,
  Sharpe 0.57, DD -3.16%. USDJPY M15 +3.81 / +2.05 / +1.46%, Sharpe 0.47, DD -4.50%.
  Pair-specific parameters pass all 3 splits:
  * GBPUSD M30: RSI 9, mid 45, hi 65, lo 35, db 0.2 → net +3.37 / +1.81 / +0.05%
  * USDJPY M15: RSI 14, mid 55, hi 75, lo 25, db 0.1 → net +13.62 / +10.27 / +2.46%
  EA now supports configurable `InpRsiBandRsiPeriod/Mid/Hi/Lo/Deadband` inputs
  plus `InpRsiBandAutoDetect` (default true — auto-sets correct params by _Symbol;
  attach to any chart timeframe with `InpStrategy=SOV_RSI_BAND`,
  `InpRsiBandEnabled=true`, `InpRequirePythonSignal=false`) and `InpRsiBandMaxBars`
  (default 0=disabled, 24 bars tested — improves OOS on all 3 pairs). SovereignRSIBand.mqh
  recompiled, 0 errors. See research/rsi_band_structural.py
  and results/rsi_band_structural_*_H1.csv.
- **Hedge blend: RETRACTED — lookahead artifact.** `SOV_HEDGE_BLEND` exists in the
  EA (`EA/Include/SovereignHedgeBlend.mqh`) and compiles, but the research behind
  it was wrong. The regime weight for month *t* was built from
  `carry.rolling(3).sum()`, which includes `carry[t]` itself — so the weight for a
  month already knew that month's outcome. That produced the "D+V Sharpe 1.89 /
  OOS 1.47 / last-12mo +7.26%" claim. Lagged one month (the only causal version)
  the blend is **full CAGR 1.80%, Sharpe 0.42, maxDD -7.5%** against **RSI band
  alone at 1.84% / 0.58 / -5.5%** — strictly dominated by the leg it contains, in
  every split. The robustness sweep agrees: carry weight 0.00 is the best member
  of the family and every added carry weight degrades Sharpe monotonically
  (0.62 → 0.30 D+V as weight goes 0.00 → 1.00). **Do not enable
  `InpHedgeBlendEnabled`.** Fixed in `hedge_blend_test.py`,
  `hedge_robustness.py` and `hedge_recent.py`; the leaky version is kept in
  `hedge_blend_test.py` as a labelled control so the trap is visible.
- **Structure ride ported to EA** (`SOV_STRUCT_RIDE`, H1, USDJPY only): +2.2 /
  +11.7 / +15.5, Sharpe 0.95 OOS. EURUSD/GBPUSD fail design split.
- **MA compression rubberband: RETRACTED — metric artifact, not an edge.**
  `SOV_COMPRESSION_RUBBERBAND` exists in the EA and compiles, but the research
  behind it was wrong. `ma_compression_sweep.py` stored
  `(close[i] - entry_price)` — the *cumulative* gain since entry — in its
  return series, then `metrics()` compounded that as if it were a bar return.
  The same gain was counted once for every bar a trade stayed open, and the
  entry cost was charged twice. That produced Sharpe 5-11 and maxDD ~0%.
  The "44/48 OOS passes" were **OOS-only**, selected on OOS, which the project
  forbids. After marking to market bar by bar with the same cost model the
  validated RSI-band rule uses, the honest numbers are:
  EURUSD compress=1.0/ext=1.5 **PF 0.91, -20.35%, maxDD -23.75%** (2,044 trades);
  USDJPY compress=1.0/ext=2.5 **PF 0.83, -11.19%, maxDD -12.62%** (371 trades);
  EURUSD compress=0.3/ext=2.5 is only 14 trades in 8 years (noise).
  The full corrected sweep finds **2 of 48** configs clearing all three splits
  (GBPUSD 1.0/2.5 at +10.88% over 8.6 years, ~1.2% CAGR, 115 trades; USDJPY
  0.5/3.0 with 8 design trades). 2/48 is *below* the ~6/48 chance would give,
  so the rule has no edge. Do not enable
  `InpStrategy=SOV_COMPRESSION_RUBBERBAND`. Fixed in `ma_compression_sweep.py`
  (true per-bar returns + trade-level PF + all-three-splits gate, selection on
  design+validation only); corrected CSV regenerated.
- **One backtest contract: `research/backtest_core.py`.** `simulate_stops()`
  marks to market bar by bar so `(1 + ret).cumprod() - 1` is the account return;
  `trade_metrics()` is trade-level PF; `validate_returns()` rejects a
  cumulative-since-entry series. It self-tests (`python research/backtest_core.py`).
  `three_way_meta.py` (which returned the buggy series), `intraday_discovery.py`
  and `ma_compression_sweep.py` all use it now. Any new backtester must use it
  or reproduce the same contract. All of `research/*.py` compiles under
  `-W error::SyntaxWarning`.
- **The combined book PASSES all three splits — carry + GBPUSD only.** EURUSD sleeve
  demoted: it passes on broker tape (2018-2026: +5.92 / -0.09 / +1.03%) but FAILS
  all 3 splits on independent HistData (2005-2025: -28.95 / +6.74 / -5.11%).
  `research/portfolio_combine.py` blends carry D1 + RSI band GBPUSD H1 (the RSI leg
  is the equal-weight ensemble of that rule's whole config family, not the best-in-sample
  config) with inverse-vol weights chosen on design+validation only.
  2018-01 to 2026-11, 104 months: design **+1.11%** (Sharpe 0.12, maxDD -3.16%),
  validation **+5.41%** (0.77, -3.62%), OOS **+4.06%** (0.74, -2.64%), full CAGR
  +1.20%, Sharpe 0.47, **maxDD -3.62%**, total +10.91%. EURUSD sleeve reported
  separately (broker tape only): design +5.92%, validation -0.09%, OOS +1.03% —
  fails validation, so not in the main book. Adding USDJPY M15 lifts full Sharpe
  to 0.67 / maxDD -1.16% but that window starts 2022-11 and has **zero design
  months** — a shorter proof, not a better one.
  Manifest: `research/results/portfolio_combined_manifest.json`.

**To unfreeze for live:** Python backtest validation on real broker H1 tape
(`research/data/mt5/`) is sufficient for research_good / desk_good promotion
(no MT5 Strategy Tester required). Forward demo matching thesis is complete
(`research/results/forward_demo_manifest.json`, carry_mom_filter net +362.46%
PF 1.93 maxDD -2.32% over 2024-2026; RSI band GBPUSD H1 forward net +1.40%
Sharpe 1.14 maxDD -0.1% over last 12 months, OOS +2.52%). `live_good` tier now
contains `carry_mom_filter` and `rsi_band_GBPUSD_H1`.

## Critical facts

- **Python feed is synthetic** (`institutional_quant_master.py` emits `"source":
  "synthetic"`). It is NOT wired into the EA's entry logic. The EA computes its own
  z-scores from `CopyClose`. Do not enable `InpRequirePythonSignal`.
- **No real MT5 tick ingestion exists.** `research/xsec_meanrev.py` needs real D1
  FX CSVs in `research/data/` to validate; with synthetic data it correctly shows no
  edge (random walk has no mean reversion by definition).
- **MT5 Strategy Tester networking is limited.** The Python TCP telemetry often
  fails to connect in the tester; the EA correctly falls back. Do not treat a missing
  "Sovereign telemetry connected" log as a failure.
- **Attach to a D1 chart** (rebalance is monthly or daily depending on `InpStrategy`).
  Copy FRED rate CSVs via `.\scripts\install_ea.ps1` → `MQL5/Files/Sovereign/rates/`.

## MT5 data plane (H1 / professor alignment)

Research and Strategy Tester must use the **same broker H1 tape** (`research/data/mt5/`).
See `docs/MT5_DATA_PLANE.md`. Before H1 tester or stack proof:

```powershell
.\scripts\sync_mt5_data_plane.ps1 -Export -Install
```

## What to trade (strategy problem)

**Trading mode:** `configs/sovereign_trading_mode.json` — carry D1 primary; London ORB discontinued until `london_orb_gross_net.json` shows gross edge. External synthesis: `docs/FX_EXTERNAL_INSIGHTS.md`.

No certified scalp edge. **Good strategy** = tiers in `configs/strategy_quality_bar.json` (`research/strategy_certification.py`, `.\scripts\run_strategy_certification.ps1`). **Only `research_good` carry today:** `carry_mom_filter` on **D1** via `SovereignGodsEyeEA`. Routers: `configs/good_strategies.json`, `configs/profitable_sleeve.json`. Prove on broker: `.\scripts\run_carry_book.ps1` (certify + D1 tester). Legacy: `.\scripts\run_lane1_mt5_truth.ps1`.

## Commands

```powershell
# Automated Century adapter (hourly daemon + optional LLM via OPENAI_API_KEY)
.\scripts\start_century_stack.ps1 -Mt5Files "$env:APPDATA\MetaQuotes\Terminal\<ID>\MQL5\Files"
.\.venv\Scripts\python.exe python\century_adapt.py

# Python brain (synthetic feed)
.\scripts\start_brain.ps1
.\.venv\Scripts\python.exe .\scripts\healthcheck.py   # exits 0 after 3 valid payloads

# Research backtest (design/validation/OOS splits)
.\.venv\Scripts\python.exe research/xsec_meanrev.py

# Discovery Lab — our own hypotheses; proof in results/discovery_proof.csv
.\scripts\run_discovery.ps1
# Results: research/results/xsec_meanrev_summary.csv

# EA install + compile
.\scripts\install_ea.ps1                                  # copies + MetaEditor compile
# Or open EA/SovereignGodsEyeEA.mq5 in MetaEditor, press F7.

# Publish the chart template for the EA, and keep it live while you edit it
.\scripts\sync_template.ps1
.\scripts\sync_template.ps1 -Watch                        # republish on save

# AI analyst -> JSON + flat CSV the EA draws from
.\.venv\Scripts\python.exe research\llm_chart_analyst.py --symbol EURUSD --tf H1
# Verify a key WITHOUT spending tokens (GET /v1/models):
.\.venv\Scripts\python.exe research\llm_chart_analyst.py --probe
# Fail loudly instead of silently using the deterministic read:
.\.venv\Scripts\python.exe research\llm_chart_analyst.py --symbol EURUSD --tf H1 --strict

# LLM key: copy .env.example -> .env, or store a user env var
.\scripts\set_llm_key.ps1 -Provider openai -Key "sk-..."
.\.venv\Scripts\python.exe research\sovereign_env.py       # shows which provider is active
```

## Architecture (post-restructure)

- `EA/SovereignGodsEyeEA.mq5` — strategy router. Default: `g_carry.Rebalance()` once
  per calendar month; alternate: daily `g_portfolio.Rebalance()`.
- `EA/Include/SovereignCarryMom.mqh` — monthly carry + 12m momentum on **any
  symbols** in `InpSymbols` (swap rank if no rate CSV).
- `EA/Include/SovereignCentury.mqh` + `python/century_adapt.py` — **automated**
  risk adaptation from trade history (optional LLM overlay via `OPENAI_API_KEY`).
  Not a guarantee of century profits; slow premia + kill switches + learning.
- `EA/Include/SovereignPortfolio.mqh` — daily xsec MR. Computes 20-day z-score per
  symbol via `CopyClose` + SMA + stdev; longs most negative, shorts most positive;
  carry* overlay weights toward high positive swap differential. Stop = 1.5 ATR,
  TP = 2x stop, total portfolio risk capped at `InpMaxTotalRisk` % equity.
- `EA/Include/SovereignPerformance.mqh` — adaptive governor from **trade history**
  (per-symbol buckets, master bucket, expectancy scaler, 14d blocks on weak legs).
  Persists under `MQL5/Files/Sovereign/performance_*.csv`; optional history bootstrap
  on init. **Cannot guarantee profitability** — it cuts risk on losers and scales
  up modestly on winners.
- `EA/Include/SovereignAuditLog.mqh` — CSV audit. **Already fixed:** `RotateIfNeeded()`
  closes/reopens each handle at 1 MB, called from every `Log*` method.
- `EA/Include/SovereignTrafficLight.mqh` — pre-trade board. **Already fixed:**
  `MacroLight()` falls back to `_Symbol` when `DXYUSD`/`VIXUSD` are absent.
- `EA/Include/SovereignStructRide.mqh` — **H1 overlay (USDJPY certified only).**
  k=2 fractal swings confirmed k bars late; exit on failed high / lower low;
  re-entry on new high; 1.5 ATR stop, 2x ATR TP, max 12 bars/leg.
  USDJPY H1 passes all 3 splits (+2.2 / +11.7 / +15.5, Sharpe 0.95 OOS).
  EURUSD (-4.4% design) and GBPUSD (-0.8% design) fail — **not certified**.
  Enabled via `InpStructRideEnabled`, attach to H1 chart only.
- `EA/Include/SovereignRSIBand.mqh` — **RSI band overlay (any TF, EURUSD/GBPUSD/USDJPY certified).**
  Trend filter (EMA21 slope + EMA50 alignment), RSI cross through mid band in trend direction;
  exit on RSI cross back through hi/lo band (after min-hold) or slope flip; optional time-based
  exit (m_maxBars); 1.5 ATR stop, 2x ATR TP; spread gate p90. EURUSD net +4.83 / +4.72 / +2.02%,
  Sharpe 0.44 OOS. GBPUSD M30 +1.72/+4.98/+3.12%, Sharpe 0.57, DD -3.16%. USDJPY M15
  +3.81/+2.05/+1.46%, Sharpe 0.47, DD -4.50%. Enabled via `InpRsiBandEnabled`,
  attach to any chart timeframe. Auto-detect sets pair-specific params by _Symbol.
  **Early trend-change**: when price closes above/below EMA21 and holds for `InpRsiBandEarlyTrendHoldBars`
  (default 2) bars before EMA21/EMA50 align, returns trend=2/-2 and allows entry at reduced size
  (`InpRsiBandEarlyTrendSizeMult`, default 0.5) with RSI recovery filter
  (`InpRsiBandEarlyTrendRsiMax`/`InpRsiBandEarlyTrendRsiMin`, default 50). Full EMA confirmation
  (trend=1/-1) still gets normal size.
- `EA/Include/SovereignHedgeBlend.mqh` — **Regime-dependent carry × RSI band blend.
  RETRACTED — do not enable.** Carry 3-month momentum > 0 → 50/50 carry/RSI
  blend; ≤ 0 → RSI band only. The published D+V Sharpe 1.89 / OOS 1.47 came from
  building the weight for month *t* out of `carry.rolling(3).sum()`, which
  includes `carry[t]`. Lagged one month it is CAGR 1.80% / Sharpe 0.42 / maxDD
  -7.5%, worse than RSI band alone (1.84% / 0.58 / -5.5%) in every split. The
  code is kept only so the negative result is reproducible. Leave
  `InpHedgeBlendEnabled` false.
- `EA/Include/SovereignStructure.mqh` — **look-left S/R structure tracker.**
  Tracks swing highs/lows across H1 (+H4/D1 alignment), scores each level by
  touches/recency/ATR distance/cross-TF confluence, classifies structure as
  STRUCT_GOOD / STRUCT_WEAK / STRUCT_BAD. READ-ONLY decision input.
  **The published validation was a lookahead artifact — see 2026-11-12 below.**
  `research/structure_quality.py` originally reported GOOD structure at
  +0.193% (USDJPY) / +0.137% (EURUSD) OOS with 62% win. Two leaks were found
  and fixed: (1) `levels_per_bar` stored references to the level dicts, so a
  touch counted, or a break, on a *future* bar mutated the snapshot used to
  grade a past bar; (2) the entry used the swing at `t-1`, which a k-bar
  fractal cannot know until `t+k`. After the fix GOOD structure is **-0.024 /
  -0.044%** on EURUSD and **+0.116%** on USDJPY OOS with 41-53% win — i.e. no
  edge. Do **not** enable `InpStructRideUseStructureFilter` on the strength of
  the old numbers.
- `EA/Include/SovereignTemplateSpec.mqh` — **runtime chart-template reader.**
  Reads `Common\Files\Sovereign\template\<name>.ini` (published by
  `scripts\sync_template.ps1` from `templates\fxmopol.tpl`) and **reloads itself
  when that file changes**, so editing the template changes the bot's read and
  drawings without a recompile or restart. Exposes chart colours, every MA
  (period/method/colour plus a live buffer read), the fractal colour and the RSI
  period/levels. Read-only: it never changes the trade rule.
- `EA/Include/SovereignAiOverlay.mqh` — **draws the analyst's read on the live chart.**
  Reads the flat CSV `Common\Files\Sovereign\ai_overlay_<SYM>_<TF>.csv` written by
  `research/llm_chart_analyst.py` (`READ` / `LEVEL` / `ZONE` / `ARROW` / `NARR` /
  `RISK` records, one per line, comma separated so MQL5 parses it with
  `StringSplit`). Draws S/R levels, shaded zones, entry arrows and a READ label
  through `g_paint`, in the template's palette, under the `AI_` object prefix.
  **Display only** — it may veto and explain, never size or trigger. A read older
  than `InpAiOverlayMaxAgeSec` is marked `STALE` in the panel.
- `python/institutional_quant_master.py` — unchanged synthetic brain.
- `EA/Include/SovereignPortfolioRisk.mqh` — portfolio-level risk engine: VaR, correlation matrix, Kelly fraction sizing, circuit breakers, per-currency exposure caps. Wired into RSI band via `m_portRisk` pointer; `RegisterPosition`/`UnregisterPosition` called on every open/close.
- `EA/Include/SovereignSmartExecution.mqh` — smart execution: TWAP slicing, slippage model, iceberg orders. Wired into RSI band via `m_exec` pointer; routes entries through `ExecuteEntry()` instead of direct `CTrade::Buy/Sell`.
- `EA/Include/SovereignDiscretion.mqh` — human discretion overlay: file-based veto/approve/flatten/size-cap via `Common\Files\Sovereign\discretion_<SYMBOL>.txt`. Checked before every entry; can flatten all positions on demand.
- `EA/Include/SovereignRegimeFilter.mqh` — regime detection: `VolatilityRegime` (calm/normal/volatile from ATR percentile) and `TrendRegime` (bull/bear/choppy from EMA alignment). Returns `RegimeSizeMultiplier` to scale position size by regime.

## Validation results (preserve, do not delete)

- M5 breakout: **-355.79R**, PF 0.75 (2011-2025, independent HistData)
- M5 mean reversion: **-4,078.37R**, PF 0.75
- Monthly carry + momentum: +1.64% CAGR, Sharpe 0.20, -26% DD (only survivor, not
  deployable)
- **Structure ride + S/R filter (H1): RETRACTED — lookahead artifact.** The
  +0.137 to +0.193% OOS per trade with 62% win was produced by
  `research/structure_quality.py` while it had two leaks: level snapshots stored
  dict references (future touches/breaks mutated past grades) and the entry used
  a swing one bar before it could be confirmed. Both are fixed; the causal rerun
  is GOOD -0.024% (EURUSD) / -0.044% design and +0.116% (USDJPY) OOS at 41-53%
  win — no edge. The S/R grade is **not validated** and must not gate entries.
- **RSI band ride (H1, EURUSD) — gross-positive, cost-limited.** Entry on RSI9
  crossing back UP through 50 while EMA21 slope is up, exit above 75 (mirror for
  shorts). Gross (zero cost) design/validation/OOS +8.3 / +2.8 / +2.6%,
  Sharpe 0.56, DD -7.7%. Net at 12 pts round trip is -1.1%; break-even is
  ~11 pts; at 6 pts +3.7 / +1.3 / +1.2% (Sharpe 0.26). Mid = 50 is a sharp
  optimum (45 -> -14.4%, 55 -> -22.5%), so it is structural, not fitted.
  GBPUSD and USDJPY do **not** pass (gross validation -11.6% / -4.7%).
  Not deployed.
- **Dynamic bands: measured and rejected.** Rolling-quantile bands,
  volatility-scaled bands (50 +/- 1.5 sd) and walk-forward re-fitting of
  (mid, hi, lo) on the trailing 3,000 bars were all compared with the fixed
  50/75/25 rule on the same live window. Fixed wins on every symbol where any
  variant is positive (EURUSD +3.11% vs -5.87 / +0.44 / -9.15 at 6 pts), and
  walk-forward is worst in 0/8 years on EURUSD. See `research/walkforward_band.py`.
- **RSI band rule at a real cost model: SURVIVES on EURUSD, cost-limited margin.**
  `research/rsi_band_net.py` charges the actual quoted spread per side (floored at
  1 pt so the 54%-zero column is not believed) + slippage + commission. Un-knobbed
  it is +2.34 / +1.25 / +1.80% (design/validation/OOS), 659 round trips, and it
  breaks even at ~15 pts. Adding `min-hold 12` (the RSI-high exit may not fire
  before 12 bars; a slope flip still exits at once) and `margin 3` (RSI must cross
  50 by 3, not merely touch it) gives **+4.83 / +4.72 / +2.02%, full +12.05%,
  Sharpe 0.44, DD -6.95%, 550 round trips**, and moves break-even to **~35 pts
  round trip (3.5 pips)** - three times the cost tolerance. 92 of 100 swept
  configs are positive in all three splits, so this is a plateau, not a knife
  edge. Selection was on the worst of design/validation; OOS was never used to
  choose. Still EURUSD only (GBPUSD/USDJPY fail).
  **On the extended tape (to 2026-11) the rule is weaker, and the knob choice
  matters more than the rule.** `rsi_band_net.py` now also reports the
  equal-weight **ensemble of all 100 configs** (no selection at all). On EURUSD
  the design+validation winner has OOS **-1.35%** while the family average is
  **+0.75%** (61/100 configs positive in all three percentage splits, median OOS
  +1.23%, 2026 -1.73%). On GBPUSD H1 with its pair-specific 45/65/35 set the
  winner *does* generalise (OOS +3.13%, 75/100 plateau, 2026 +3.35%). Ranking 100
  configs on the worst of design+validation picks the extreme of the surface, and
  the extreme does not generalise — which is why the book trades the ensemble.
  `rsi_band_net.py` now takes `--rsi-period/--mid/--hi/--lo/--from-date/--to-date`
  (defaults unchanged: 9 / 50 / 75 / 25); the parameterised path reproduces the
  documented GBPUSD numbers exactly (+3.37 / +1.81 / +0.05%).
- **Candle-structure exits: rejected, and the win-rate trap is measured.**
  `research/candle_exit.py` holds the entry fixed and varies only the exit.
  (a) The raw structure carries almost no memory: P(next candle red | k greens)
  is 49.7 / 50.7 / 51.0 / 53.2 / 55.7% for k = 1..5 versus 49.1% unconditional,
  and mean next-bar return is ~0 at every k. P(next bar makes a new high) is
  63-66% versus 47.8% unconditional - real persistence, zero drift.
  (b) The literal "close at the close of every green candle and re-enter" costs
  **-66.96 / -56.60 / -54.27%** (EURUSD/GBPUSD/USDJPY) over 8 years: ~15,000 round
  trips, each paying the spread. This is the clearest cost-drag demonstration in
  the project.
  (c) "More winners than losers" is achievable and worthless: `green2` + fresh
  re-entry wins **60.6 / 63.2 / 60.7%** of trades on the three pairs and is
  negative on all three (expectancy -0.006 / -0.019 / -0.018% per trade) because
  the average win is smaller than the average loss. Win rate is not expectancy.
  (d) The genuinely good instinct - do not leave on the first red, leave only if
  the next candle fails to reclaim that red's open (`red_recover`) - has the best
  expectancy of all candle rules on EURUSD (+0.001%, OOS **+7.14%**) but fails
  validation (-4.51%), so it is not a survivor. 2 of 16 exits survive on EURUSD
  (both are the RSI-75 baseline); 0 of 16 on GBPUSD and USDJPY.
- **Market structure break exits: implemented in EA, validated on broker tape — mixed results.**
  `SovereignRSIBand.mqh` `OnNewBar()` position section exits long when
  `!m_structureIntactLong` (green candle fails to reclaim above prior red open above
  EMA21) and exits short when `!m_structureIntactShort` (red candle fails to break
  below prior green close below EMA21), gated by `InpRsiBandStructureFilterEnabled`
  (default false). Trackers `m_redOpenHigh` / `m_greenCloseLow` update on every bar
  in `UpdateMarketStructure()`. Entry gate also blocks longs when
  `!m_structureIntactLong` and shorts when `!m_structureIntactShort`.
  `research/rsi_band_net.py` `--structure-filter` flag mirrors the same logic.
  Results on H1 2018-2026 (real broker tape, cost model):

  | Pair | With structure filter | Without structure filter | Verdict |
  |------|----------------------|-------------------------|---------|
  | EURUSD | 12/100 configs all 3 splits, best OOS +3.88% (min-hold 24, margin 3) | 61/100 configs all 3 splits, best OOS +1.90% (min-hold 12, margin 3) | **HELPS** — best OOS ~2x better, but fewer configs survive (12 vs 61) |
  | GBPUSD | 0/100 configs all 3 splits, best OOS +2.11% | 0/100 configs all 3 splits, best OOS +2.38% | **NEUTRAL** — no config positive in all 3 splits either way |
  | USDJPY | 0/100 configs all 3 splits, best OOS -1.05% | 0/100 configs all 3 splits, best OOS +0.10% | **HURTS** slightly — structure filter degrades OOS |

  Conclusion: structure-break exits help on EURUSD (the only pair where RSI band
  is positive in all 3 splits), are neutral on GBPUSD, and slightly harmful on
  USDJPY. Leave `InpRsiBandStructureFilterEnabled` false unless trading EURUSD
  H1 specifically. Even on EURUSD, the filter reduces the ensemble's design-split
  performance (-2.70% vs +3.21%) so it should be used selectively, not as a blanket rule.

- **EMA21 cross fakeout detection: tested and rejected.** `research/ema21_fakeout.py`
  tests candle-level (wick rejection, weak close, range contraction, marginal
  distance) and prior-context (trend duration, RSI divergence, swing distance,
  EMA slope) filters on EURUSD/GBPUSD/USDJPY H1 (2018-2026, real broker tape).
  Baseline EMA21 cross is decisively negative on all three (EURUSD PF 0.69 /
  -58.69%, GBPUSD PF 0.82 / -41.96%, USDJPY PF 0.73 / -51.30% full). Combined
  filters cut trades ~80% and improve PF to 0.76-0.85, but the best config still
  loses ~10-14% full period on every pair. Fakeouts are detectable but the
underlying cross has negative expectancy no filter overcomes. Consistent with
`candle_exit.py`: win rate is achievable, expectancy is not.

- **EMA21 rubber band (enter NEAR EMA21, exit FAR from EMA21): rejected.** `research/ema21_rb.py`
  tests enter-close-to-EMA21 + exit-far-from-EMA21 (with optional RSI divergence exit) on
  EURUSD/GBPUSD/USDJPY H1 (2018-2026, real broker tape, real cost model). Entry: long when
  close within entry_atr*ATR below EMA + green + RSI < rsi_max; short mirror. Exit: long when
  close >= EMA + exit_atr*ATR; short when close <= EMA - exit_atr*ATR; optional divergence exit.
  Swept: ema_period {14,21,50} × entry_atr {0.2,0.3,0.5,0.8} × exit_atr {1.5,2.0,2.5,3.0} ×
  rsi_max/rmin {(70,30),(75,25),(65,35)} × divergence on/off × lookback {3,5,8}.

  | Pair | Best PF | Best Design | Best Validation | Best OOS | All 3 splits? |
  |------|---------|-------------|-----------------|----------|---------------|
  | EURUSD | 0.91 | -0.28% | +0.04% | -0.12% | NO |
  | GBPUSD | 1.01 | +0.16% | +0.01% | -0.13% | NO |
  | USDJPY | 0.96 | -0.09% | -0.12% | -0.02% | NO |

  **No config passes all 3 splits on any pair.** Price extending far from EMA21 does not
  predict mean reversion — "far enough" has no edge. RSI divergence exit also adds nothing
  (div configs identical to non-div). Do not enable any EMA21 rubberband variant.


- **All SovereignRSIBand entry/exit rules: comprehensive sweep.** `research/sovereign_entries_exits.py`
  tests 9 entry rules x 8 exit rules on EURUSD/GBPUSD/USDJPY H1 (2018-2026,
  real broker tape, real cost model). Entry rules: rsi_band, early_trend,
  green_red, red_above_ema, green_below_ema, three_consecutive, reflex, ema_trend,
  engulfing. Exit rules: rsi_high, structure_break, time, trailing, rsi_exhaustion,
  breakeven, ema_break, trend_flip.

  | Pair | Best entry+exit | PF | Design | Validation | OOS | Full | All 3 splits? |
  |------|----------------|-----|--------|------------|-----|------|---------------|
  | EURUSD | engulfing + trailing | 1.29 | +2.61% | +0.50% | +0.07% | +3.20% | YES (marginal OOS) |
  | GBPUSD | reflex + trailing | 1.11 | +19.15% | -4.16% | +1.88% | +16.33% | NO (validation neg) |
  | USDJPY | three_consecutive + trailing | 0.96 | negative | negative | negative | negative | NO |

  Key findings:
  * **engulfing + trailing on EURUSD** is the only combo positive in all 3 splits,
    but OOS is marginal (+0.07%) with only 161 trades (17.7/yr). Weak edge at best.
  * **GBPUSD reflex + trailing** looks strong on design (+19.15%, Sharpe 0.83) but
    fails validation (-4.16%) — classic overfit.
  * **USDJPY**: all entries lose money (PF < 1.0 on every combo).
  * 4 entry rules generate **0 trades** in Python (green_red, red_above_ema,
    green_below_ema, ema_trend) — either parameters too restrictive or Python
    implementation doesn't match EA logic. These need debugging before they can
    be ruled in/out definitively.
  * engulfing works on EURUSD (PF 1.29) but loses on GBPUSD (PF 0.88) and USDJPY
    (PF 0.43-0.76) — pair-specific, not robust.
  * reflex works marginally on GBPUSD (PF 1.11) but loses on EURUSD (PF 0.89) and
    USDJPY (PF 0.91) — not robust.
  * **No entry/exit combination is robustly positive across all three pairs.**
    Consistent with project finding: win rate is achievable, expectancy is not.

- **Exit regret analysis: trailing stops cut winners short — remove trailing.** `research/exit_regret.py`
  computes MFE vs actual exit regret across all 9 entry x 8 exit combos on EURUSD/GBPUSD/USDJPY H1.
  Aggregate regret by exit rule (all entries combined):

  | Pair | #1 winner-cut-off | #2 (mostly losers) | #3 | #4 | #5 |
  |------|------------------|-------------------|-----|-----|-----|
  | EURUSD | trailing +16,534R | stop +18,819R* | ema_break +1,508R | structure_break +956R | trend_flip +603R |
  | GBPUSD | trailing +19,091R | stop +18,355R* | ema_break +1,501R | structure_break +891R | trend_flip +644R |
  | USDJPY | trailing +22,256R | stop +17,296R* | ema_break +1,444R | structure_break +906R | trend_flip +729R |

  \* Stop regret is mostly on losing trades (expected — that's its job). **Trailing is
  the #1 rule cutting winner profit short on all three pairs** (+16K to +22K R).
  `ema_break`, `structure_break`, `trend_flip` are moderate winner-cut-off rules
  (+600–1,500 R each). `rsi_high`/`rsi_low` cut little winner profit (+19–21 R each)
  — relatively benign exits.

  Recommendation: remove trailing stops; for EURUSD engulfing entries, replace with
  `rsi_high` exit (the benign RSI-band baseline). Test `engulfing + rsi_high` vs
  `engulfing + trailing` on EURUSD OOS to confirm reduced regret without losing the edge.
  **Result: `engulfing + rsi_high` FAILS (design +1.62%, validation -0.69%, OOS -0.89%,
  PF 1.01) vs `engulfing + trailing` (design +2.61%, validation +0.50%, OOS +0.07%).**
  Trailing cuts winners short but is essential to keep the edge — removing it destroys
  profitability. Do not remove trailing stops; instead consider widening `InpTrailAtrMultiplier`
  (default 1.5) to reduce regret while retaining protection.

- **RSI curvature ("the line bends"): a filter artifact, not a signal.**
  `research/rsi_curvature.py` formalises the observation that the RSI line curves
  after a run of same-direction bars and that the curve precedes a reversal. The
  curve is the second difference `d2[t] = d1[t] - d1[t-1]`, i.e. the gap between
  where RSI lands and the linear extrapolation `2*RSI[t-1] - RSI[t-2]`, normalised
  by the rolling sd of `d1`. Findings:
  (a) The description is correct: after k rising RSI bars the line bends 76.8 /
  78.1 / 79.5 / 80.4% of the time for k = 2 / 3 / 4 / 5.
  (b) **It is arithmetic, not market information.** An RSI built on a pure random
  walk with the same volatility bends 76.6-78.1% of the time - statistically
  identical. RSI increments have no directional persistence (`P(d1>0 | d1>0) =
  48.1%`, lag-1 autocorrelation only -0.083), so conditioning on a rising bar
  selects an above-average increment and the next one is usually smaller. It is
  regression to the mean of a near-coin-flip drawn as a line.
  (c) **Decisive control:** hold the RSI level fixed and vary only the bend. The
  effect vanishes or reverses on all three pairs - EURUSD 65-75 has *fewer* reds
  after a downward bend (48.0%) than after a flat one (51.8%). The apparent
  predictive power in the unconditional table is the RSI level in disguise.
  (d) **Money:** a curvature exit is far worse than the RSI-75 exit on EURUSD
  (+0.67 / +2.49 / +0.02% vs +6.41 / +5.09 / +2.03%). OR-ing it into RSI-75 is
  neutral (+6.58 vs +6.41% design = noise). 0 of 2 survive on GBPUSD and USDJPY.
  (e) A "skip entries while the line bends down" filter **never fires**, because a
  fresh RSI cross up through 50 already *is* an upward bend - the concept is
  already inside the entry rule.
  Do not add curvature to the EA. Results: `research/results/rsi_curvature_*_H1.csv`.
- **Machine learning / neural nets: measured, and they add nothing.**
  `research/ml_edge_test.py` asks whether a nonlinear model finds direction that the
  linear tests missed. It uses purged walk-forward (train 20k, test 3k, step 3k,
  purge + embargo = horizon) so overlapping labels cannot leak, fits the scaler
  inside each training window only, and **repeats every run with the training
  labels shuffled**. The shuffled control scored **AUC 0.489-0.501**, i.e. no leak,
  so the real numbers below are trustworthy. Features: 20 causal ones (RSI level,
  slope and curvature, dist21/dist50, slopes, ATR ratio, body, range, ret1/5/20,
  streak, spread ratio, hour, day-of-week).

  | symbol | linear AUC | trees AUC | neural AUC | best net | neural − linear |
  |---|---|---|---|---|---|
  | EURUSD | 0.5160 | 0.5161 | 0.5208 | **−8.91%** | +0.0048 |
  | GBPUSD | 0.5143 | 0.5059 | 0.5153 | **−5.14%** | +0.0010 |
  | USDJPY | 0.5131 | 0.5151 | 0.5111 | **−12.06%** | −0.0020 |

  Horizon scan on EURUSD: AUC 0.5296 at h=1, 0.516 at h=5, 0.495 at h=20, 0.475 at
  h=60 (where the real model matches its own shuffled control, 0.4748 vs 0.4754 -
  pure base-rate drift). So there is a real but tiny **1-bar microstructure signal**
  worth ~0.53 AUC, it decays to nothing by 20 bars, and at h=1 the spread eats it
  entirely. **Every model loses money net of cost on every pair.**
  The neural net never beats logistic regression: added capacity bought nothing,
  which is exactly what "there is no signal to fit" looks like.
  Do not build an ML or neural layer. Results: `research/results/ml_edge_test_*.csv`.
- **Combining the two survivors: real diversification, limited by the carry leg.**
  `research/edge_combine.py` blends `carry_mom_filter` (D1) with `rsi_band_net`
  (EURUSD H1) on monthly returns. Their monthly correlation is **−0.185** - genuinely
  negatively correlated. Over the 95-month overlap the blend (15% carry / 85% RSI,
  weight chosen on design+validation only) gives CAGR 1.89%, Sharpe 0.66,
  **maxDD −3.1%** versus carry alone 1.59% / 0.20 / −17.8% and RSI alone 1.84% /
  0.58 / −5.5%. The drawdown collapse is the real prize. **But OOS the carry leg
  went to Sharpe −0.28**, so the blend (0.30) did not beat RSI alone (0.43). Keep
  the blend as a risk tool, not as a return engine.
  Results: `research/results/edge_combine_monthly.csv`.

- **Regime-dependent hedge blend (carry × RSI band): RETRACTED — lookahead.**
  `research/hedge_blend_test.py` gates the carry leg on its 3-month momentum:
  carry_mom3 > 0 → 50/50 carry/RSI blend; ≤ 0 → RSI band only. The published
  table was produced with a weight for month *t* built from
  `carry.rolling(3).sum()`, which **includes `carry[t]`** — the weight already
  knew that month's result. Lagged one month, the honest numbers are:

  | Metric | carry alone | RSI band alone | Hedge blend (honest) | (retracted) |
  |--------|-------------|----------------|----------------------|-------------|
  | D+V Sharpe | 0.32 | 0.62 | 0.51 | 1.89 |
  | OOS Sharpe | −0.27 | 0.42 | 0.11 | 1.47 |
  | Full Sharpe | 0.20 | 0.58 | 0.42 | 1.81 |
  | Full maxDD | −17.8% | −5.5% | −7.5% | −3.0% |
  | Full CAGR | 1.59% | 1.84% | 1.80% | 7.22% |

  The blend is **strictly dominated by RSI band alone in every split** — it adds
  drawdown and loses Sharpe. The robustness sweep says the same thing from the
  other side: as the carry weight goes 0.00 → 1.00 the D+V Sharpe falls
  monotonically 0.62 → 0.30, i.e. **weight 0.00 (pure RSI band) is the best
  member of the family**. Momentum lookbacks 1/2/3/6/9/12 give OOS Sharpe
  −0.20 to +0.89 with no plateau. The leaky variant is retained in
  `hedge_blend_test.py` as a labelled control. Results:
  `research/hedge_blend_test.py`, `research/hedge_robustness.py`,
  `research/results/hedge_blend_manifest.json`.
- **The single ledger of what is kept:** `research/edge_ledger.py` rebuilds
  `research/results/EDGE_LEDGER.md` / `.csv` from the result files. Current count:
  **2 KEEP, 5 CONDITIONAL, 28 REJECT/RETRACTED of 35 concepts.**
- **Python packages: installation now works.** Verified 2026-11-12:
  `python.exe -m pip` reaches PyPI and installs (the old ASR block was on
  `pip.exe`, not on `python.exe -m pip`). Installed and used: `lightgbm` 4.7.0,
  `torch` 2.15.0+cpu, `matplotlib` 3.11.2. **The extra libraries changed no
  conclusion** - LightGBM scored *below* logistic regression (0.5116 vs 0.5160).
- **Sequence models (torch) add nothing.** `research/seq_edge_test.py` gives a TCN
  and an LSTM the last 32 bars. TCN AUC **0.4880**, LSTM **0.5036**, versus a linear
  benchmark that sees only the last bar at **0.5201** - and the LSTM's *shuffled*
  control (0.5173) scored higher than the real net. Sequence memory is not the
  missing signal.
- **Meta-labelling the RSI edge does not improve it.** `research/rsi_meta_label.py`
  trains a classifier on the rule's own trades to rank them. Win/loss target AUC
  0.4512 (linear) / 0.4610 (lgbm); PnL target 0.5143 / 0.4897 - none usable, and
  performance degrades monotonically as more trades are filtered. A permutation test
  (`--perm`) confirms any cut of the same size does as well as the "best" one.
- **Chart annotation + LLM analyst (visual, read-only).**
  `research/chart_annotate.py` renders candles, EMA21/50, confirmed look-left S/R
  (drawn only from the confirmation bar, never the swing bar), RSI9, shaded bad
  zones (spread > p90, ATR spike, thin liquidity) and RSI-band entry arrows to
  `research/results/charts/<SYM>_<TF>.png`.
  `research/llm_chart_analyst.py` builds an exact state bundle and asks GPT/Claude
  (or a deterministic fallback when no key is set) for a read, risks and drawing
  instructions -> `research/results/ai_note_<SYM>_<TF>.json`; pass it back with
  `chart_annotate.py --note` to draw it.
  The EA side already exists: `EA/Include/SovereignChartVisualizer.mqh`
  (DrawBox / DrawLevel / DrawArrow / DrawText / DrawTrend / DrawPanel) is wired into
  `SovereignGodsEyeEA`, `SovereignLondonOrbEA` and `SovereignFxmopolEA`.
  **Advisor only - the analyst may veto and explain, never size or trigger.**
  **On-chart loop (2026-11-12):** the analyst's output now reaches the live chart.
  `llm_chart_analyst.py` also writes a flat `ai_overlay_<SYM>_<TF>.csv` sidecar
  (the JSON stays the analyst's record; the CSV is the EA's) into
  `Common\Files\Sovereign\`, and `EA/Include/SovereignAiOverlay.mqh` draws its
  levels / zones / arrows / read label on the chart in the template's colours.
  The template itself is read at runtime (`SovereignTemplateSpec.mqh`) and
  hot-reloads, so a template edit changes the drawing without a recompile.
  The CSV contract is verified: every `LEVEL` has 4 fields, `READ` has 4.
- **M1 / M15 / M30 RSI band sweep: M1 rejected; M15 and M30 pass all 3 splits on broker tape.** M1
  tested 486 configs per symbol on ~97 days of broker M1: EURUSD +0.23%, GBPUSD -0.10%,
  USDJPY +0.36% — not viable, spread-dominated and too short to split. On higher
  timeframes the pullback entry (EMA21>EMA50 trend filter, buy RSI dip to ~40 crossing
  up / sell rally to ~60 crossing down) passes all three splits on **USDJPY M15**
  (+3.81/+2.05/+1.46%, Sharpe 0.47, DD -4.50%) and **GBPUSD M30**
  (+1.72/+4.98/+3.12%, Sharpe 0.57, DD -3.16%). EA `SovereignRSIBand.mqh` is
  timeframe-agnostic (`m_timeframe` from chart), auto-detect sets pair-specific params
  (GBPUSD: RSI 9/mid 45/hi 65/lo 35/db 0.20; USDJPY: RSI 14/mid 55/hi 75/lo 25/db 0.10).
  Forward demo on broker tape complete (+1.40% net, Sharpe 1.14, maxDD -0.1%
  over last 12 months). Not cleared for live on HistData independent tape.
- **HistData independent validation (2005-2025, 21 years):** RSI band ride does NOT hold up on the independent tape. EURUSD H1 (50/75/25): design -28.95%, validation +6.74%, OOS -5.11% — NOT positive in all three. GBPUSD H1 (45/65/35): design -2.17%, validation -0.08%, OOS +2.08% — NOT positive in all three. The broker-tape pass (2018-2026) is a data-window artifact; even with costs halved (3.5 pts RT), EURUSD still fails (design -25.39%, OOS -3.16%). The failure is NOT a cost-model artifact. RSI band sleeves remain research_good/CONDITIONAL on broker tape only; forward demo complete on broker tape; not cleared for live on HistData independent tape.
- Full detail: `docs/HANDOFF.md` sections 6.3-6.4 and `docs/validation.md`

## Enhancement roadmap (next steps)

**The structure grade is NOT validated** - see the retraction above. The roadmap
that was built on it is therefore on hold.

**Items 1 and 2 below are now DONE** (`research/rsi_band_net.py` + EA implementation):
cutting round trips with `min-hold 12` + `margin 3` took the EURUSD rule from break-even
at ~15 pts to break-even at ~35 pts round trip, and the cheap-spread entry gate
(skip when the quoted spread is above the trailing p90) is in the same script. The
RSI band rule is now ported to the EA as `SOV_RSI_BAND` mode, off by default,
any timeframe, any symbol. Auto-detect sets pair-specific parameters:
GBPUSD M30 (RSI 9/mid 45/hi 65/lo 35/db 0.20), USDJPY M15 (RSI 14/mid 55/hi 75/lo 25/db 0.10),
EURUSD H1 (RSI 9/mid 50/hi 75/lo 25/db 0.10). The hedge blend (`SOV_HEDGE_BLEND`)
is also in the EA but **retracted — leave `InpHedgeBlendEnabled` false**; the honest
blend is dominated by the RSI band leg it contains.

**Remaining work:**

1. **GBPUSD/USDJPY RSI band resolution — DONE.** Pair-specific parameters pass all 3 splits
   (GBPUSD M30: RSI 9/mid 45/hi 65/lo 35/db 0.20; USDJPY M15: RSI 14/mid 55/hi 75/lo 25/db 0.10).
   EA auto-detect (`InpRsiBandAutoDetect`) sets correct params by _Symbol automatically.
   Attach to GBPUSD M30 or USDJPY M15 chart with `InpStrategy=SOV_RSI_BAND`,
   `InpRsiBandEnabled=true`, `InpRsiBandAutoDetect=true`, `InpRequirePythonSignal=false`.

---

## Recent Implementation (2026-11-14) — Visual Trade Plan & Autonomous Pyramiding

### Objective
Enhance the Sovereign EA to (a) draw a visual simulated trade plan / pyramiding overlay on the chart even with zero open positions, and (b) automate the multi-tier short pyramiding strategy with macro trend filter, dynamic resistance retests, profit-gated scale-ins, trailing stops, and RSI exhaustion exits.

### Files modified (repo + synced to MT5)
| File | Change |
|------|--------|
| `EA/SovereignGodsEyeEA.mq5` | New inputs, macro MA handle, pyramid state globals, enhanced OnInit logging, PaintChart panel rows, DrawStrategySimulationPlan call, account-level kill switches & hard limits, portfolio heat, news blackout |
| `EA/Include/SovereignChartVisualizer.mqh` | Added `DrawStrategySimulationPlan()` + `ClearSimObjects()` (SOV_SIM_ prefix objects) |
| `EA/Include/SovereignRSIBand.mqh` | Added macro MA directional lock, trailing stop w/ min-step + cooldown, RSI exhaustion exit, Stage 1 breakeven (MFE-based), position age time stop, SL/TP verification, lot cap, pyramid state accessors, state recovery (RecoverState), **early trend-change detection (trend=2/-2 when price closes above/below EMA21 and holds, reduced size + RSI recovery filter)**, new member vars |
| `EA/Include/SovereignError.mqh` | Added `SovPositionNetProfit()` helper; consolidated `POSITION_COMMISSION` calls (was 2 warnings → 1); enhanced `SovCheckTradeAllowed`/`SovCheckSymbolTradable` logging |

### New EA inputs (`SovereignGodsEyeEA.mq5`)
```
//=== Visual Simulation / Trade Plan Overlay ===
input bool   InpVisualSimulation      = true;   // draw simulated trade plan (display only)
input int    InpVisualSimMaxArrows    = 100;    // max sim arrows on chart
input int    InpMacroMaPeriod         = 200;    // macro trend filter MA period (EMA 200)
input int    InpMacroMaMethod         = 1;      // 0=SMA, 1=EMA, 2=SMMA, 3=LWMA
input int    InpMaxPyramidLayers      = 3;      // max concurrent pyramid layers
input double InpInitialEntryPrice     = 77500.0;// initial entry level (BTCUSD ~77,500)
input double InpScaleIn1Price         = 74800.0;// scale-in 1 level (~74,800)
input double InpScaleIn2Price         = 70000.0;// scale-in 2 level (<70k)
input double InpStopLossZonePrice     = 81000.0;// initial stop loss zone (>81k)
input int    InpRsiExhaustionLevel    = 30;     // RSI exhaustion exit for shorts (RSI < 30)
input double InpTrailAtrMultiplier    = 1.5;    // trailing stop ATR multiplier
```

### New account-level kill switches & hard limits (`SovereignGodsEyeEA.mq5`)
```
//=== Account-Level Kill Switches & Hard Limits ===
input double InpAccountMaxDDFromPeakPct = 15.0;   // halt all trading if equity drops X% from peak (0=disabled)
input double InpMinFreeMarginPct        = 20.0;   // block entries when free margin < X% of equity (0=disabled)
input double InpMaxLotSize              = 10.0;   // absolute max lot size per trade (fat-finger protection, 0=disabled)
input int    InpMaxPositionAgeBars      = 0;      // close positions older than N bars (0=disabled)
input bool   InpVerifySLTPOnEntry       = true;   // verify SL/TP actually set on broker after order placement
input double InpDailyProfitTargetPct    = 0.0;    // stop new entries today when up X% from day start (0=disabled)
input int    InpMaxTotalSovPositions    = 20;     // max Sovereign positions across ALL symbols (0=disabled)

//=== Portfolio-Wide Correlation & Exposure Heat ===
input double InpMaxPortfolioRiskPct = 8.0;    // aggregate % of equity at risk across all Sovereign positions (0=disabled)
input double InpMaxNetExposurePerCCY = 0.0;   // max net exposure per currency in lots (0=disabled)

//=== Economic Calendar News Blackout (MT5 native) ===
input bool   InpNewsBlackoutEnabled   = true;   // block entries around high-impact news (uses MT5 Calendar API)
input int    InpNewsBlackoutImpact    = 3;      // min impact: 1=LOW,2=MEDIUM,3=HIGH (default HIGH only)
input int    InpNewsBlackoutMinsBefore= 15;     // block new entries N minutes before event
input int    InpNewsBlackoutMinsAfter = 15;     // block new entries N minutes after event
input string InpNewsBlackoutCurrencies= "";     // currencies to watch (e.g. "USD,EUR"). Empty = chart symbol's currencies

//=== Breakeven ===
input double InpBreakevenAtR       = 1.0;     // move SL to breakeven when price reaches +N x stop distance (0=disabled)
input double InpBreakevenBufferPts = 0.0;     // extra buffer beyond entry price at breakeven (pts)
input double InpTrailMinStepPts    = 0.0;     // min price improvement (pts) to modify trailing SL (0=disabled)
```

### Rule 4c — Breakeven stage 1 (`SovereignRSIBand.mqh`)
- When price reaches `+InpBreakevenAtR × entry stop distance`, move SL to entry price (+ optional `InpBreakevenBufferPts`)
- Only ever improves SL (moves up for longs, down for shorts)
- Activated once per position; re-arms on recovery/restart via `RecoverState()`

### Rule 4d — Position age time stop (`SovereignRSIBand.mqh`)
- Closes any Sovereign position older than `InpMaxPositionAgeBars` bars (computed from actual position open time)
- Prevents capital trapped in dead sideways trades on crypto/vol indices

### Rule 4e — SL/TP verification (`SovereignRSIBand.mqh`)
- After every order placement, reads back `POSITION_SL`/`POSITION_TP` from the broker
- Logs `"SL/TP MISMATCH — POSITION MAY BE NAKED"` if actual SL/TP deviates from requested by > 10 points
- Controlled by `InpVerifySLTPOnEntry` (default true)

### Rule 5 — Max lot cap (`SovereignRSIBand.mqh`)
- Absolute ceiling on lot size per trade (`InpMaxLotSize`, default 10.0)
- Fat-finger protection: even if risk % math produces a huge lot, it is capped
- Applied after governor/traffic-light multipliers, before order send

### Account-level kill switches (`SovereignGodsEyeEA.mq5`, OnTick gate)
- **Account max DD from peak** (`InpAccountMaxDDFromPeakPct`): high-watermark kill switch. Halts all new entries if equity drops X% from account peak. Unlike daily DD, this does not reset each day.
- **Free margin guard** (`InpMinFreeMarginPct`): blocks new entries when free margin < X% of equity. Prevents margin calls on high-margin symbols (BTCUSD, Volatility 75).
- **Total position cap** (`InpMaxTotalSovPositions`): max Sovereign positions across ALL symbols, not just the current chart.
- **Daily profit target** (`InpDailyProfitTargetPct`): stops new entries today when up X% from day start. Locks in wins.
- **Weekend flat** (`InpWeekendFlat`): flattens all positions Fri 21:00 server → Monday.
- **Spread guard** (`InpMaxSpreadForTrade`): skips new entries when spread exceeds cap (crypto/vol indices).

### State recovery (`SovereignRSIBand.mqh`)
- `RecoverState()` re-adopts any live Sovereign positions on OnInit after terminal restart or timeframe change
- Restores `m_inPosition`, side, entry/stop/TP, bars-held estimate, leg count, MFE, breakeven state
- Positions are never orphaned — trailing stops and RSI exits keep working after a restart

### New global state (`SovereignGodsEyeEA.mq5`)
- `g_macroMaHandle` / `g_macroMaBuf[]` / `g_macroMaBar` — macro EMA 200 handle + buffer
- `g_pyramidLegs` / `g_pyramidNetProfit` / `g_pyramidArmed` — pyramid state for panel
- `g_portfolioRiskPct` / `g_portfolioHeatBlocked` / `g_portfolioHeatDetail` — portfolio heat tracking
- `g_newsBlocked` / `g_newsBlockDetail` — news blackout state
- `g_accountPeakEquity` / `g_accountDDCheckTime` — high-watermark for drawdown kill switch
- `g_dailyProfitTargetHit` — daily profit target flag

### Compile status
- `compile_mt5.log`: **0 errors, 1 warning** (single benign `POSITION_COMMISSION` deprecation at SovereignError.mqh:283)
- `.ex5` regenerated at `...\MQL5\Experts\SovereignGodsEyeEA.ex5`


### New global state (`SovereignGodsEyeEA.mq5`)
- `g_macroMaHandle` / `g_macroMaBuf[]` / `g_macroMaBar` — macro EMA 200 handle + buffer
- `g_pyramidLegs` / `g_pyramidNetProfit` / `g_pyramidArmed` — pyramid state for panel

### Rule 1 — Macro trend bias filter (`SovereignRSIBand.mqh`)
- Shorts ONLY when `Bid < Macro MA`; Longs ONLY when `Bid > Macro MA`
- Block reason logged: `"macro MA lock (short/long blocked: Bid >=/<= Macro MA)"`
- Wired via `g_rsiBand.SetMacroMaValue(macroMaVal)` in EA OnTick before `OnNewBar`

### Rule 3 — Profit-gated pyramiding (`CanPyramidInProfit` in `SovereignError.mqh`)
- Subsequent positions on a symbol only opened when every existing open position (same magic+symbol) is in net profit (profit+swap+commission)
- Initial entry (zero positions) always permitted
- Mirrored into RSI band via `SetPyramidState(legs, netProfit, armed)`; panel shows `guard=ARMED|BLOCKED`

### Rule 4a — Trailing stops (`SovereignRSIBand.mqh`)
- Longs: trail stop up at `closePx - trailAtrMult × ATR` (only move up)
- Shorts: trail stop down at `closePx + trailAtrMult × ATR` (only move down)
- Controlled by `InpTrailAtrMultiplier` (default 1.5)

### Rule 4b — RSI exhaustion exit (`SovereignRSIBand.mqh`)
- Shorts: emergency close when `RSI < InpRsiExhaustionLevel` (default 30)
- Logs `"EXIT RSI exhaustion short (rsi=XX < 30)"`

### Visual overlay — `DrawStrategySimulationPlan()` (`SovereignChartVisualizer.mqh`)
Paints `SOV_SIM_`-prefixed objects when `InpVisualSimulation=true`:
- `[1. INITIAL ENTRY]` — dashed aqua HLINE at `InpInitialEntryPrice` + text label
- `[2. SCALE-IN]` — dotted gold HLINE at `InpScaleIn1Price` + label
- `[3. SCALE-IN]` — dotted gold HLINE at `InpScaleIn2Price` + label
- `[INITIAL STOP LOSS]` — shaded red rectangle above `InpStopLossZonePrice` + label
- Trailed stop path — dashed lime trendline (right-rayed)
- Cleared/redrawn every bar via `ObjectsDeleteAll(0, "SOV_SIM_")` — no clutter

### GE_PANEL additions (`PaintChart` in `SovereignGodsEyeEA.mq5`)
When `InpVisualSimulation`:
- `Pyramid legs=N/M netProfit=X guard=ARMED|BLOCKED` (lime/red)
- `Macro MA 200 value=... Bid=... SHORT OK|BLOCKED`
- `TrailATR=1.5 RSIExh=30 StopZone=81000 ScaleIns=74800/70000`

### OnInit enhanced logging
Added `PrintFormat` before each critical check so Journal shows exactly which step fails:
- `Sovereign OnInit: checking trade allowed... MQL_TRADE_ALLOWED=X ACCOUNT_TRADE_EXPERT=Y`
- `Sovereign SovCheckTradeAllowed: MQL_TRADE_ALLOWED=0/1`
- `Sovereign OnInit: checking symbol tradable... _Symbol=BTCUSD SYMBOL_TRADE_MODE=Z`
- `Sovereign SovCheckSymbolTradable: SYMBOL_TRADE_MODE=X (SYMBOL_TRADE_MODE_FULL=Y)`
- `Sovereign OnInit: trade filling OK`

### Compile status
- `compile_mt5.log`: **0 errors, 1 warning** (single benign `POSITION_COMMISSION` deprecation at SovereignError.mqh:283)
- `.ex5` regenerated at `...\MQL5\Experts\SovereignGodsEyeEA.ex5` (440 KB)

### Known blocker — EA OnInit returns code 1 (not attaching)
Symptom: Journal shows `"initializing of SovereignGodsEyeEA (BTCUSD,H1) failed with code 1"` then `"expert ... removed"`.
Likely causes:
1. **Algo Trading not enabled** — green Algo Trading button must be ON (`MQL_TRADE_ALLOWED=false`)
2. **BTCUSD not in Market Watch / not full trade mode** — `SYMBOL_TRADE_MODE != SYMBOL_TRADE_MODE_FULL`
3. **ACCOUNT_TRADE_EXPERT == 0** — EA not permitted to trade on this account

Next step: Re-attach to a fresh BTCUSD H1 chart with Algo Trading enabled and BTCUSD visible in Market Watch. Check Journal for the specific `Sovereign ERROR` / `PrintFormat` line to identify which check failed.

### How to recompile (MetaEditor64.exe)
```powershell
$meta = "C:\Program Files\MetaTrader 5 Terminal\MetaEditor64.exe"
$mq5 = "C:\Users\OluwatobaOgunsakin\AppData\Roaming\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1\MQL5\Experts\SovereignGodsEyeEA.mq5"
& "$meta" /compile:"$mq5" /log:"C:\xampp\htdocs\GodE\EA\compile_mt5.log"
```
Then sync all 4 files to MT5 (`...\MQL5\Include\Sovereign\` + `...\MQL5\Experts\`), refresh Navigator, restart MT5 if needed.

### Conventions
- MQL5 uses 3-space indentation; `#property strict`; `INVALID_HANDLE` for bad handles.
- All drawn visual objects use `SOV_SIM_` prefix; cleaned via `ObjectsDeleteAll(0, "SOV_SIM_")` each bar.
- Pyramid state guarded by `CanPyramidInProfit()` — never scale-in when existing legs are in drawdown.


## Conventions

- MQL5 uses 3-space indentation; `#property strict`; `INVALID_HANDLE` for bad handles.
- Research scripts use pandas/numpy, chronological splits, and report CAGR/Sharpe/
  maxDD/winrate/Profit Factor. Keep the same metrics.
- **Reporting (user rule):** Always show proof on a **daily or monthly** calendar basis.
  Use `research/period_returns.publish_results()` at the end of backtests; stdout should
  print last 12 months (and last 21 days for intraday). Aggregate CSVs live under
  `research/results/*_monthly.csv` and `*_daily.csv`. Run `.\scripts\run_period_results.ps1`
  for a combined bundle. Do not leave per-trade-only tables as the only human output.
- Keep the working tree clean after each tested change so negative results are
  preserved. The research baseline commit is `da53b8c`.

## Windows ASR / IT execution (MUST follow)

Locked-down Windows may **block `\.venv\Scripts\pip.exe` and `pip3.exe`** (Attack Surface
Reduction). **Never** invoke pip launchers or `pip install` from agents, hooks, or repo
scripts.

**Only allowed Python entry:**

```powershell
.\.venv\Scripts\python.exe <path\to\script.py> [args]
```

Use existing `scripts\*.ps1` wrappers (they already use `python.exe`). New packages:
`docs/ML_INSTALL_IT.md` — allowlist **`python.exe`**, offline wheels, or admin install;
**not** pip.exe. Cursor rule: `.cursor/rules/asr-safe-execution.mdc`.

## Tool usage rules (MUST follow)

- **Write tool**: ALWAYS provide BOTH `content` and `filePath` parameters. Never call
  write with only one. If you are unsure of the parameter names, use `edit` (after
  reading the file) or `bash` with `Set-Content`/`Out-File` instead.
- **Bash tool**: ALWAYS provide the `command` parameter. Never call bash with only a
  description.
- **Read before edit**: Always `read` a file before `edit`-ing it. The edit tool
  rejects edits without a prior read in the conversation.
- **Verify after write**: After writing a file, `read` it back or `grep` for obvious
  syntax errors before claiming done.
- **Never stop mid-task**: If you are partway through a multi-file change, finish it
  before responding. Do not send a partial response and stop. If you hit a tool
  error, fix it and continue in the same turn.
- **One task per turn**: Complete the full requested change before responding to the
  user. Do not ask "should I continue?" — just continue.

## Senior-dev workflow

1. Read the relevant files first (use `read` + `grep`).
2. Make all edits in a single turn where possible.
3. Verify by reading back or grepping.
4. Update `docs/HANDOFF.md` at the end of any change that affects architecture or
   validation status.
5. Do not fabricate results. If a backtest shows no edge, say so and preserve the
   negative result.