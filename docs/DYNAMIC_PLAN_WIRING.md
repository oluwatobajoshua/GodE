# Dynamic Plan Wiring & Position State Tracking

Status: implemented 2026-11-14. Display/analysis only by default; pyramiding
execution is opt-in (`InpPyramidExecEnabled=false`) and **not backtested**.

This note records (a) what was already in place, (b) the final wiring applied in
`SovereignGodsEyeEA.mq5`, `SovereignRSIBand.mqh`, and (c) the verification steps.

---

## 1. Position state aggregator (`UpdatePyramidState`)

`SovereignGodsEyeEA.mq5` owns three globals:

```mql5
int    g_pyramidLegs      = 0;      // open Sovereign legs on _Symbol
double g_pyramidNetProfit = 0.0;    // PROFIT + SWAP + COMMISSION, summed
bool   g_pyramidArmed     = true;   // profit gate for pyramiding
datetime g_lastDynBar     = 0;      // last bar the SOV_DYN_ markup was drawn
```

`UpdatePyramidState()` is called at the very top of `OnTick()` (immediately after
`g_python.Poll()` / `g_telemetry.Poll()`), so every strategy sees a current read:

- loops `PositionsTotal()`, skips anything that is not
  `POSITION_MAGIC == SOVEREIGN_MAGIC` and `POSITION_SYMBOL == _Symbol`;
- counts legs into `g_pyramidLegs`;
- sums `SovPositionNetProfit(ticket)` = `PROFIT + SWAP + COMMISSION`;
- sets `g_pyramidArmed = (g_pyramidLegs == 0 || g_pyramidNetProfit > 0.0)`.

`SovPositionNetProfit()` already lives in `SovereignError.mqh` and is the single
definition of "net profit" in the project.

The state is pushed into the strategy each tick:

```mql5
g_rsiBand.SetPyramidState(g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);
```

## 2. Visualizer activation (`DrawDynamicPlan`)

`PaintChart()` builds the read and renders it:

```mql5
SovereignPlanView plan;
g_plan.Build(plan, g_pyramidLegs, g_pyramidNetProfit, g_pyramidArmed);

//--- dynamic plan rendering (SOV_DYN_): redraw on a new bar only
if(InpAnalystEnabled && plan.barTime > 0 && plan.barTime != g_lastDynBar)
{
   g_lastDynBar = plan.barTime;
   g_paint.DrawDynamicPlan(plan);
   DrawAnalystMarkup(plan);   // MA paths + numbered leg markers
}
```

`DrawDynamicPlan` draws entry/stop/TP/scale-in lines, the shaded stop zone
(`OBJ_RECTANGLE`, `OBJPROP_BACK=true` + fill), fractal support/resistance and the
bias banner. `DrawAnalystMarkup` adds the slow/fast/mid MA polylines and the
`Leg 1/2/3` callouts at the real `POSITION_TIME` / `POSITION_PRICE_OPEN`.
Objects use the `SOV_DYN_` prefix and are removed in `OnDeinit` with
`ObjectsDeleteAll(0, "SOV_DYN_")`.

The new-bar guard prevents per-tick object churn while still redrawing as soon as
the plan changes.

## 3. Input bridging (`OnInit`)

| Input | Routed to |
|---|---|
| `InpMaxPyramidLayers` | `g_rsiBand.SetMaxPyramidLayers()` and `SetPyramidExec()` |
| `InpTrailAtrMultiplier` | `g_rsiBand.SetTrailAtrMult()` |
| `InpRsiExhaustionLevel` | `g_rsiBand.SetRsiExhaustionLevel()` |
| `InpPyramidExecEnabled` | `g_rsiBand.SetPyramidExec()` |
| `InpPyramidScaleStepAtr` | `g_rsiBand.SetPyramidExec()` (ATR step between legs) |
| `InpRsiBandStopAtr` | `g_rsiBand.SetStopAtr()` (was hardcoded 1.5 in `OpenPosition`) |
| `InpEmaHarvestTrailAtr` | `g_emaHarvest.Init(..., trailAtr)` |

## 4. Profit-gated pyramiding (execution, opt-in)

`CSovereignRSIBand` now supports real multi-leg scale-ins, gated three ways:

1. `m_pyramidExec` must be true (`InpPyramidExecEnabled`);
2. `m_legCount < m_maxPyramidLayers`;
3. `CanPyramidInProfit(magic, symbol)` must be true — every open leg net > 0.

A leg is added when price has moved `InpPyramidScaleStepAtr * ATR` beyond the most
recent leg in the trade direction and the trend still agrees. Scale-in legs are
sized at `m_scaleInSizeMult` (0.5) of the initial size so three legs are ~2x the
first-leg risk, not 3x.

`ClosePosition()` now closes **every** matching leg (tickets are collected first,
then closed), and the sleeve is only considered flat when all legs close.
`CountLegs()` reconciles internal state to reality (a leg may hit its own broker
SL/TP).

## 5. Verification checklist

- [x] `UpdatePyramidState()` declared before `OnTick()` and called at its top.
- [x] `g_pyramidArmed` initialised to `true` (matches "no legs -> armed").
- [x] `g_paint.DrawDynamicPlan(plan)` called immediately after `g_plan.Build(...)`.
- [x] `SOV_DYN_` objects cleaned in `OnDeinit`.
- [x] `InpRsiBandStopAtr` added and routed to `SetStopAtr()`.
- [x] `InpMaxPyramidLayers` / `InpTrailAtrMultiplier` routed in `OnInit()`.
- [x] `CanPyramidInProfit()` checked before every scale-in.
- [x] Compile: MetaEditor64, 0 errors (see `EA/compile_mt5.log`).

## 6. Caveats (must stay visible)

- Pyramiding execution is **UNVALIDATED**. No backtest includes scale-ins. Keep
  `InpPyramidExecEnabled=false` for any live/demo run until it is measured with
  `research/backtest_core.py`.
- The trailing stop updates the internal `m_stopPrice` only; it does **not**
  call `PositionModify`, so the broker SL is not moved. A gap can therefore pass
  the "stop" before the next bar closes.
- The 2026-11-14 overlays (EMA200 macro lock, 1.5-ATR trail, RSI<30 exhaustion)
  are still ON by default and are still **UNVALIDATED**.

## 7. Template alignment (`fxmopol.tpl`)

The chart template carries three Moving Averages and one RSI:

| Template object | Setting |
|---|---|
| Moving Average #1 | period **21** |
| Moving Average #2 | period **50** |
| Moving Average #3 | period **100** |
| Relative Strength Index | period **9** |
| Fractals | present (upper/lower buffers) |

The analyst now reads those at runtime instead of trusting a duplicated input:

- `OnInit` loads `g_template` **before** `g_plan.Init(...)`.
- When the template is loaded and carries >= 3 MAs, the three periods are read,
  **sorted ascending** (smallest = fast, middle = mid, largest = slow) and passed
  to `g_plan.Init(...)`; `RsiPeriod()` likewise overrides `InpAnalystRsiPeriod`.
- Fallbacks stay `InpAnalystFastMaPeriod=21`, `InpAnalystMidMaPeriod=50`,
  `InpAnalystSlowMaPeriod=100`, `InpAnalystRsiPeriod=9` (the slow default was
  corrected from 200 -> 100 to match the template).
- `iFractals` is always created in `CSovereignDynamicPlan::Init` and feeds
  `swingHigh` / `swingLow` through `LastFractal()`.
- The resolved values are logged: `Analyst: aligned to template MA 21/50/100 RSI 9`.

## 8. Zero-hardcoding audit

Every drawn price is derived at runtime. In `CSovereignDynamicPlan::Build()`:

| Field | Source |
|---|---|
| `entry` | `view.bid` (short) / `view.ask` (long) |
| `stop` | `entry +/- stopDist`, `stopDist = atr * m_stopAtr` |
| `tp` | `entry -/+ tpDist`, `tpDist = atr * m_tpAtr` |
| `zoneTop` / `zoneBot` | `stop` vs. fractal `ceiling` / `floorPx` |
| `scale2` / `scale3` | `entry +/- n * stepDist`, `stepDist = atr * m_scaleStepAtr` |
| `swingHigh` / `swingLow` | `iFractals` upper/lower buffers |
| `atr`, `rsi`, `slowMa`, `fastMa`, `midMa` | `iATR`, `iRSI`, `iMA` buffers |

No literal price appears in `SovereignDynamicPlan.mqh` or
`SovereignChartVisualizer.mqh` (grep for `77500|74800|70000|81000` returns
nothing). The legacy BTCUSD levels remain only as `DEPRECATED` inputs behind
`InpLegacySimPlan=false` and are never drawn by the dynamic engine.

## 9. Audit of the external review (claims vs. code)

Every claim was checked against the source. Two were wrong.

| Claim | Verdict |
|---|---|
| MA 21 / 50 / 100 in the template | **TRUE** (`period=21/50/100`) |
| RSI period 9 | **TRUE** (`period=9`) |
| Fractals present, 5-bar swing detection | **TRUE** (`iFractals`, fixed 2L+2R in MT5) |
| Regime lock `px < slowMa && fastMa < midMa` | **TRUE** (`SovereignDynamicPlan.mqh:393-394`) |
| `scale2`/`scale3` from ATR steps, not fixed prices | **TRUE** (`entry +/- n*stepDist`) |
| `zoneTop`/`zoneBot` from fractal + ATR | **TRUE** |
| `OBJ_HLINE` / `OBJ_RECTANGLE` (back) / `OBJ_TEXT` | **TRUE** |
| Banner shows rsi / MAspread / ATR / legs / net / guard | **TRUE** (`DrawDynamicPlan:872`) |
| Profit gate blocks scale-in unless every leg net > 0 | **TRUE** |
| "Slow filter defaults to 200 in the constructor" | **FALSE** — corrected to 100 this session |
| "RSI levels 20 / 50 / 80 drive execution" | **FALSE** — see below |

### The RSI-band discrepancy (real, and deliberately not "fixed")

`fxmopol.tpl` draws RSI levels **20 / 50 / 80**. The validated RSI-band
strategy trades **hi/lo = 75/25** on EURUSD (65/35 on GBPUSD, 75/25 on USDJPY).
These are different numbers and **must stay different**:

- 20/80 is the template's *visual* band.
- 75/25 is the *backtested* exit band (`research/rsi_band_net.py`). Changing the
  execution band to 80/20 would silently alter a validated rule.

So the EA now shows both in the panel:

```
RSI bands  template=20/80  exec hi/lo=75/25  (drawn = template)
```

grey when they agree, **orange when they differ** — so the operator can never
mistake the drawn band for the traded band.

### The MA-method mismatch (found and fixed)

The template is not just periods:

| Template MA | period | method |
|---|---|---|
| #1 | 21 | `method=1` (EMA) |
| #2 | 50 | `method=1` (EMA) |
| #3 | 100 | `method=2` (**SMMA**) |

The code previously hardcoded `MODE_EMA` for fast and mid and used the input
`InpAnalystSlowMaMethod=1` (EMA) for slow — so the white slow line drawn by the
EA would **not** sit where the template's white line sits. `Init()` now takes
`fastMethod`/`midMethod` and the EA passes all three methods read from the
template (`MaMethod(0..2)`), carried through the same period sort. Startup log:
`Analyst: aligned to template MA 21(m1)/50(m1)/100(m2) RSI 9`.

### Visual parity added

`DrawDynamicPlan` now also shades the **profit zone** (`SOV_DYN_PLAN_TPZONE`,
entry -> TP) alongside the red stop zone, matching the plan schematic.
