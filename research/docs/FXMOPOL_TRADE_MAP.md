# FXMOPOL trade map — EURUSD H1 playbook replay

Rules: EMA21 + EMA50 + SMMA100 stack, RSI 14 levels 20/50/80.
Full trade log: `research/results/fxmopol_playbook_trades_EURUSD.csv`.

## Example week 2025-11-02 → 2025-11-08 (automated playbook)

**Week total (automated):** -40.9 pips on 5 round-trips.

| Entry (UTC) | Exit (UTC) | Side | Entry | Exit | Pips | Entry why | Exit why |
|-------------|------------|------|-------|------|------|-----------|----------|
| 2025-11-03 01:00:00 | 2025-11-03 16:00:00 | SHORT | 1.15269 | 1.15306 | -3.7 | entry_stop_hunt_reject | exit_close_above_ema21 |
| 2025-11-04 05:00:00 | 2025-11-04 06:00:00 | SHORT | 1.15124 | 1.15232 | -10.8 | entry_retest_ema21 | exit_close_above_ema21 |
| 2025-11-04 13:00:00 | 2025-11-05 12:00:00 | SHORT | 1.14922 | 1.14909 | 1.3 | entry_stop_hunt_reject | exit_close_above_ema21 |
| 2025-11-05 18:00:00 | 2025-11-05 20:00:00 | SHORT | 1.14815 | 1.14883 | -6.8 | entry_retest_ema21 | exit_close_above_ema21 |
| 2025-11-07 20:00:00 | 2025-11-09 23:00:00 | LONG | 1.15642 | 1.15433 | -20.9 | entry_pullback_ema21 | exit_close_below_ema21 |

## Your chart — discretionary map (same week)

- **Nov 2-3** — **FLAT** @ ~1.1180 chop through SMMA100: No stack — skip
- **Late Nov 3 / early Nov 4** — **ENTER LONG** @ 1.1195-1.1210: Stack bullish, RSI>50, pullback EMA21
- **Near 1.1266 peak** — **EXIT LONG** @ 1.124-1.126: Take profit or close below EMA21
- **Mid Nov 5** — **ENTER SHORT** @ 1.120-1.121: Below all MAs, retest EMA21 from below
- **Flush ~1.1136, RSI~26** — **EXIT SHORT** @ 1.114-1.115: RSI oversold / cover

## Full history summary

- Total trades: **3974**
- Win rate: **27.2%**
- Sum pips (no compounding): **-923.4**

Monthly P&L: run `publish_results` output in `playbook_EURUSD_mid50_monthly.csv`.