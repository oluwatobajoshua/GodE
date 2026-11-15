# Institutional-Grade Design Document — Sovereign EA

## Status
DRAFT — implements hedge-fund-grade risk, execution, and discretion layers into the existing single-symbol RSI band + carry router.

## Gap Analysis (vs hedge funds / top traders)

| Capability | Current State | Gap |
|---|---|---|
| Portfolio-level risk | Per-symbol only | No VaR, correlation, concentration limits |
| Position sizing | Fixed % risk | No Kelly / fractional-Kelly, no payoff-aware sizing |
| Regime detection | MA compression only (unused strategy) | No vol/trend regime classification for sizing |
| Execution | Immediate market order | No TWAP/VWAP slicing, no slippage model, no iceberg |
| Human discretion | None | No file-based veto/approve, no emergency flat |
| Validation | Backtest only | No walk-forward / Monte Carlo in EA (Python side only) |

## What to Implement

### 1. Portfolio Risk Engine (`SovereignPortfolioRisk.mqh`)
- **Historical VaR**: rolling 252-bar returns per symbol, portfolio VaR at 95%/99% confidence
- **Correlation matrix**: pairwise return correlation across all open positions
- **Concentration limits**: max notional per currency pair, max correlated exposure cluster
- **Kelly sizing**: f* = (win_rate × avg_win - loss_rate × avg_loss) / avg_win, capped at fractional-Kelly (default 0.25 × f*)
- **Circuit breakers**: daily loss %, weekly loss %, max drawdown from peak — halt new entries, optionally flatten

### 2. Smart Execution Engine (`SovereignSmartExecution.mqh`)
- **TWAP slicing**: split order into N child orders over T minutes, each at VWAP-target price
- **Slippage model**: estimate expected slippage from spread + volatility, widen limit prices accordingly
- **Iceberg**: display only a fraction of order size; replenish as fills occur
- **Adaptive**: slow down slicing when spread widens or volatility spikes

### 3. Human Discretion / Veto Layer (`SovereignDiscretion.mqh`)
- **File-based override**: `MQL5/Files/Sovereign/discretion.csv` — one line per symbol: `SYMBOL,ACTION,REASON,EXPIRY` where ACTION ∈ {VETO, APPROVE, FLATTEN, SIZE_CAP}
- **Emergency flat-all**: close all Sovereign positions instantly
- **Size cap override**: force max lot per symbol regardless of risk math

### 4. Enhanced Regime Detection (extend `SovereignRegimeFilter.mqh`)
- **Volatility regime**: low/normal/high based on ATR percentile — reduce size in high vol, skip in extreme
- **Trend regime**: trending/ranging based on ADX + EMA alignment — mean-rev only in ranging, momentum only in trending
- **Session regime**: London/NY/Asia — different sizing per session

## How Modules Wire Together

```
OnTick → allowEntries gate → PortfolioRisk.check() → 
  RegimeFilter.classify() → sizeMult → 
  KellySizer.compute() → finalLot → 
  SmartExecution.slice() → child orders →
  Discretion.vetoCheck() → block or pass → OrderSend
```

## Files

| File | Purpose |
|---|---|
| `docs/INSTITUTIONAL_DESIGN.md` | This design doc |
| `EA/Include/SovereignPortfolioRisk.mqh` | Portfolio VaR, correlation, Kelly, circuit breakers |
| `EA/Include/SovereignSmartExecution.mqh` | TWAP slicing, slippage, iceberg |
| `EA/Include/SovereignDiscretion.mqh` | Human veto/approve/flatten via CSV |
| `EA/SovereignGodsEyeEA.mq5` | Wire modules into OnTick/OnNewBar |
| `AGENTS.md` | Document new inputs, compile status |

## Constraints

- MQL5 only (no external Python calls for execution decisions — Python is telemetry/oversight only)
- All file I/O under `MQL5/Files/Sovereign/`
- No comments added to code (project convention)
- 3-space indentation, `#property strict`
- Compile with 0 errors (warnings OK if benign deprecation)
