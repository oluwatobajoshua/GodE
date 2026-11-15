# FXMOPOL decision rules — history-built Q&A

Full indicator stack on H1: **EMA21, EMA50, SMMA100, RSI(9) 20/50/80, fractals, ATR(14)**.

## Your three questions

| Question | Where answered |
|----------|----------------|
| **Why enter now?** | `research/results/fxmopol_decision_advice_EURUSD.csv` → `enter_advice` |
| **Why hold or close?** | `fxmopol_decision_states_EURUSD.csv` → `hold_long_advice` / `hold_short_advice` |
| **Will I win if I enter now?** | `fxmopol_decision_matrix_EURUSD.csv` → `winrate`, `exp_bps` by `chart_grade` + split |

**Rule:** Only trust `chart_grade=GOOD` rows for entries. **CHOP_SMMA** = flat (matches screenshots).

## Chart states (what looks good vs not)

| State | Meaning | Trade? |
|-------|---------|--------|
| `STACK_LONG` + grade **GOOD** | Fan aligned, RSI 45–80 | Long setups only |
| `STACK_SHORT` + grade **GOOD** | Bear fan, RSI 20–55 | Short setups only |
| `CHOP_SMMA` | Price tangling white SMMA | **No trade** |
| `TRANSITION_*` | Stack forming, not 5-bar streak | Wait |
| grade **WAIT** | Right stack but RSI wrong (e.g. short at RSI 26) | **No new entry** / cover |

## Entry rules (catalog)

- **entry_pullback_ema21** — Long regime: touch/hold EMA21, RSI (50,80).
- **entry_stop_hunt_reclaim** — Long regime: hunt wick reclaim, RSI (50,80).
- **entry_fractal_hunt_long** — Long regime: fractal low + hunt reclaim.
- **entry_rsi50_cross_long** — Long regime: RSI crosses up through 50.
- **entry_stack_flip_long** — First stacked long bar after chop (transition).
- **entry_retest_ema21** — Short regime: reject EMA21 from below, RSI (20,50).
- **entry_stop_hunt_reject** — Short regime: hunt reject, RSI (20,50).
- **entry_fractal_hunt_short** — Short regime: fractal high + hunt reject.
- **entry_rsi50_cross_short** — Short regime: RSI crosses down through 50.

## Exit & management rules

- **exit_ema21_break** — Long: close below EMA21. Short: close above EMA21.
- **exit_rsi_extreme** — Long: RSI >= 80. Short: RSI <= 20.
- **exit_rsi_roll_long** — Long: RSI was >72 and now <68.
- **exit_rsi_roll_short** — Short: RSI was <28 and now >32.
- **exit_regime_lost** — Stack alignment lost for 2 consecutive bars.
- **exit_time_24bars** — Time stop at 24 H1 bars.
- **exit_atr_trail_1p5** — Initial stop 1.5×ATR; trail to breakeven after 1×ATR profit.
- **exit_scale_rsi70_long** — Long partial logic proxy: exit when RSI >= 75.

## Time in each state (% of H1 bars)

| chart_state | bars | pct_time |
| --- | --- | --- |
| CHOP_SMMA | 4569 | 3.5 |
| FLAT_NEUTRAL | 45387 | 35.0 |
| STACK_LONG | 27850 | 21.5 |
| STACK_SHORT | 27743 | 21.4 |
| TRANSITION_BEAR | 11899 | 9.2 |
| TRANSITION_BULL | 12049 | 9.3 |
| WARMUP | 264 | 0.2 |

## Best combos (OOS, GOOD grade, n≥15)

| entry | exit | state | winrate | exp_bps | n |
|-------|------|-------|---------|---------|---|
| entry_rsi50_cross_long | exit_rsi_roll_long | STACK_LONG | 63.5 | 6.98 | 74 |
| entry_pullback_ema21 | exit_rsi_roll_long | STACK_LONG | 56.6 | 6.88 | 122 |
| entry_rsi50_cross_long | exit_rsi_extreme | STACK_LONG | 70.3 | 6.49 | 74 |
| entry_fractal_hunt_short | exit_rsi_roll_short | STACK_SHORT | 62.5 | 5.89 | 16 |
| entry_stop_hunt_reclaim | exit_rsi_roll_long | STACK_LONG | 63.8 | 5.29 | 94 |
| entry_pullback_ema21 | exit_rsi_extreme | STACK_LONG | 66.4 | 4.58 | 122 |
| entry_pullback_ema21 | exit_regime_lost | STACK_LONG | 39.3 | 4.16 | 122 |
| entry_pullback_ema21 | exit_scale_rsi70_long | STACK_LONG | 70.5 | 3.96 | 122 |
| entry_rsi50_cross_long | exit_scale_rsi70_long | STACK_LONG | 70.3 | 3.61 | 74 |
| entry_rsi50_cross_long | exit_time_24bars | STACK_LONG | 58.1 | 3.38 | 74 |
| entry_stop_hunt_reclaim | exit_regime_lost | STACK_LONG | 33.0 | 2.39 | 94 |
| entry_rsi50_cross_long | exit_regime_lost | STACK_LONG | 43.2 | 2.21 | 74 |
| entry_stop_hunt_reclaim | exit_rsi_extreme | STACK_LONG | 67.0 | 1.59 | 94 |
| entry_rsi50_cross_long | exit_ema21_break | STACK_LONG | 36.5 | 1.4 | 74 |
| entry_pullback_ema21 | exit_time_24bars | STACK_LONG | 49.2 | 1.24 | 122 |

Regenerate: `research/fxmopol_decision_lab.py`