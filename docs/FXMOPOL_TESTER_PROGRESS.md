# FXMOPOL — Strategy Tester as progress bar

**Rule:** The MT5 Strategy Tester equity curve + trade review CSVs are how we judge progress (not gut feel).

## Always use `fxmopol.tpl`

Automated runs apply **`fxmopol.tpl`** (EMA21/50, SMMA100, RSI9 20/50/80):

```powershell
.\scripts\run_fxmopol_tester.ps1 -Profile fxmopol_gbp_playbook
```

- Copies template to `MQL5/Profiles/Templates` and `Profiles/Charts`
- Sets `Template=fxmopol.tpl` in the tester `.ini`
- **Visual mode on** by default (you see the chart stack during the test)

**Close all `terminal64.exe` windows before automated runs.** A second instance (or `--AllowRunning`) often writes a **stub** HTML report (empty Expert, Period M0, 0 bars, 0 trades). `run_backtest.py` now waits for a **complete** report only; use **View → Strategy Tester** with the staged `.ini` under your MT5 data folder (`Sovereign_<name>.ini`) if automation still fails.

## After each test — wins, losses, stay away

1. HTML report: `backtest/reports/<name>.htm` (equity graph)
2. JSON metrics: `backtest/reports/<name>.json`
3. **Trade autopsy:** `research/results/tester_trades_<name>.csv`
4. **Summary:** `research/results/tester_review_<name>.json`
5. **History log:** `backtest/tester_progress.jsonl` (one JSON line per run)
6. **Early fail:** `run_backtest.py` aborts fxmopol runs when `trade_outcomes.csv` shows
   ≥4 closes and net ≤ −15 (use `--no-early-fail` to wait for full test).
7. **Missed rides:** `research/fxmopol_missed_opportunities.py --report <htm>` — playbook
   wins with no nearby tester entry (late / filtered entries).

Re-run review only:

```powershell
.\.venv\Scripts\python.exe research\fxmopol_tester_review.py backtest\reports\fxmopol_gbp_playbook_GBPUSD_H1.htm
```

## How to read it

| Field | Meaning |
|-------|---------|
| `chart_state` / `chart_grade` | Good stack vs **CHOP_SMMA** / **WAIT** |
| `should_have_skipped` | Entry failed proximity/fan/chop filters — **do not repeat** |
| `exit_reason` | `stop_loss`, `take_profit`, `signal_exit` (EMA21/RSI) |
| `losses_from_bad_entries` | Losses we can cut with stay-away rules |

**Stay away (protect profit):**

- `CHOP_SMMA` or `chart_grade` = WAIT / NO_TRADE
- Not `near_ema21` at entry
- `min_fan_spread_atr` below threshold (compressed EMAs)
- After a **win streak**, reduce size or stop for the day if next bar is chop (see `profit_given_back` in review JSON)

## Manual Strategy Tester (your template)

1. Charts → Template → **fxmopol**
2. Attach `SovereignFxmopolEA` with the preset from `best_win_settings.json`
3. Ctrl+R → same symbol/period → run → **Graph** tab for equity

Progress = **net profit up**, **fewer `should_have_skipped` losses**, PF ≥ 1 on OOS window — not more trades.
