# Why the Strategy Tester graph was down (and what we fixed)

## Online best practice (summary)

Sources: [MQL5 walk-forward / overfitting](https://www.mql5.com/en/blogs/post/755929), [Finversa EA testing](https://www.finversa.net/guide/ea-testing), [1kPips backtest guide](https://1kpips.com/en/blog/how-to-backtest-mt5-eas-the-right-way).

| Practice | Our use |
|----------|---------|
| Realistic spread/commission | Tester model 1; research uses ~3 bps |
| Walk-forward / hold-out | `fxmopol_symbol_train.py` — not full-history curve-fit |
| Few parameters, plateau check | Small grid in WFO |
| **Live logic = research logic** | **Was broken; fixes below** |
| Chop / weak-trend filter | `StayAwayOk`, 5-bar stack, SMMA chop |

Industry note: **adding RSI/EMA filters does not create edge** if entries are wrong ([ForexNews24 RSI+EMA backtest](https://forexnews24.com/en/blog/backtesting/rsi-and-ema-trend-filter-backtest/)). Filters only help when the **base entries** match the tested rule.

## Root cause (our data, not guesswork)

`research/fxmopol_tester_review.py` on GBP playbook H1 (Jan–Jul 2025):

| Slice | Trades | Net profit |
|-------|--------|------------|
| All tester trades | 12 | **−12.49** |
| Should have skipped (CHOP / WAIT / bad prox) | 9 | **−27.69** |
| Rule-aligned only | 3 | **+15.20** |

**8 losing trades** were tagged `should_have_skipped`. The equity curve fell because the **EA took trades the Python playbook would reject**, not because the three good `STACK_LONG` / `GOOD` trades failed.

Stop losses (−50 on 6 SL hits) vs research exits (EMA21 / RSI) also diverged — tight broker SL closed trades before signal exits.

## Fixes shipped in code

1. **Entry alignment** (`SovereignFxmopol.mqh`): playbook no longer uses `VisualTrendStack` as a side setter; 5-bar regime + pullback/hunt only; EMA crosses require chop/proximity; `mktCtx >= 3` for non-cross; `PlaybookEntryHardGate()`.
2. **Signal-first exits**: `InpPlaybookSignalExitOnly` — wide emergency SL, far TP, manage via EMA21/RSI/extension (like `fxmopol_playbook.py`).
3. **Tester profile** (`run_backtest.py` `fxmopol_gbp_playbook`): `InpEnforceSymbolProfile=false`, signal-exit mode, 3.5 ATR emergency stop.
4. **Training gate** unchanged: symbols still need WFO promotion for live auto-trade.

## Re-test for the Strategy Tester graph

1. Close all MT5 windows.
2. `.\scripts\install_ea.ps1` (recompile `SovereignFxmopolEA`).
3. `.\scripts\run_fxmopol_tester.ps1 -Profile fxmopol_gbp_playbook -From 2025.01.01 -To 2025.07.01 -NoVisual`
4. Open `backtest\reports\fxmopol_gbp_playbook_GBPUSD_H1.htm` — balance/equity chart at top.
5. Review: `.\.venv\Scripts\python.exe research\fxmopol_tester_review.py backtest\reports\fxmopol_gbp_playbook_GBPUSD_H1.htm`

## Honest expectation

Research **full-sample OOS** for this playbook on GBP/EUR was still **negative** on HistData — we do **not** promise a permanently rising graph. After alignment you should see **fewer trades** and a curve closer to research; long-horizon deployability still requires `promoted: true` in `fxmopol_symbol_profiles.json`.

Counterfactual script:

```powershell
.\.venv\Scripts\python.exe research\fxmopol_profit_diagnosis.py
```
