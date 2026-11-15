# Loss Audit — where the money actually goes

**Date:** 2026-11-08
**Question:** the EA loses money. Is the loss the *signal*, the *cost*, or an
*execution* bug? Each has a different fix, and only one of them is worth doing.

**Verdict:** cost is **86–102%** of the realized loss (89% on the breakout, more
than 100% on mean reversion), but the residual signal edge is approximately
**zero**. Removing all friction does not produce a profit — it produces
break-even. There is no parameter fix. One loss source (rule misalignment) was a
real bug and is already fixed.

---

## 1. Method

Two independent measurements, because neither alone is conclusive.

**A. Gross-vs-cost re-pricing (independent data, 15 years).**
`research/m5_independent.py` stores every M5 candidate with `r_multiple` already
net of a 12-point round-trip cost and the stop distance `sl_dist_pts`. Because the
simulator subtracts cost once from gross R, the decomposition is exact:

```
gross_r    = r_multiple + 12 / sl_dist_pts        # mid-to-mid, zero friction
net_r(c)   = gross_r - c / sl_dist_pts
```

Break-even cost is then `sum(gross_r) / sum(1/sl_dist_pts)` — the spread at which
the strategy makes exactly zero. Script: `research/cost_vs_signal.py`.
Output: `research/results/cost_vs_signal.csv`.

**B. Tester report decomposition.**
`research/loss_audit.py` rebuilds every round trip from the MT5 HTML report
(volume, execution prices, commission, swap) and re-prices the realized result at
any spread. Output: `research/results/loss_audit_*.csv`.

Data: HistData EURUSD M5, 2010-10 to 2025-12, 1,184,265 bars, no duplicate
timestamps, no bad OHLC. Tester: Deriv-Demo, 10,000 USD, 1% risk, model 1.

---

## 2. Result A — the entry has no edge to protect

EURUSD M5, 2011–2025, independent HistData.

| Setup | n | Gross avg R (cost = 0) | **Break-even cost** | Net avg R @ 12 pts | Net total R |
|---|---:|---:|---:|---:|---:|
| Hour-7 breakout | 1,536 | −0.026 | **−1.52 pts** | −0.232 | −355.8 |
| Breakout, all hours | 20,425 | −0.095 | **−5.00 pts** | −0.324 | −6,610.1 |
| Mean reversion | 55,399 | **+0.007** | **+0.29 pts** | −0.267 | −14,809.1 |

Read the break-even cost column:

- **Hour-7 breakout: −1.52 points.** Negative. The entry loses money even at
  zero spread. You would need a broker that *pays you* 1.5 points per round trip.
- **All-hours breakout: −5.00 points.** Actively wrong. More trades make it worse.
- **Mean reversion: +0.29 points.** The edge is real but tiny. Retail EURUSD is
  8–12 points. **The strategy needs a spread 30–40× tighter than retail to break
  even.** It collects 0.007R and pays 0.24R to collect it.

Mean reversion by split, showing the edge decays to nothing:

| Split | n | Gross avg R | Break-even cost |
|---|---:|---:|---:|
| Design 2011–2014 | 13,988 | +0.032 | +1.65 pts |
| Validation 2015–2019 | 18,473 | +0.006 | +0.26 pts |
| OOS 2020–2025 | 22,938 | −0.008 | **−0.32 pts** |

The gross edge is positive in design, collapses to noise in validation, and is
**negative out of sample**. That is the signature of a fitted artifact, not a
durable edge.

Hour-7 breakout by year: **8 of 16 years positive gross, 2 of 16 positive net.**
No stable regime.

### What this means

Cost is the *proximate* cause: at 12 points the hour-7 setup loses −356R, at zero
points it loses −40R. So **89% of the loss is friction.** But the remaining −40R
is the signal, and it is indistinguishable from zero. Fixing the spread moves the
strategy from "loses a lot" to "loses nothing." It cannot move it to "wins."

---

## 3. Result B — the tester understates the bleed

Legacy EA reports (`backtest/reports/london_breakout_*.htm`), 10,000 USD, 1% risk.

| Run | Trades | Net (reported) | Gross at mid | Spread @ 12 pts | Spread share of gross |
|---|---:|---:|---:|---:|---:|
| 2025 full year | 47 | +66.95 | +474.71 | 407.76 | **85.9%** |
| 2026 Jan–Mar | 17 | −4.94 | +188.86 | 193.80 | **102.6%** |
| 2026 Apr–Oct (BE1) | 25 | +331.14 | +651.30 | 320.16 | 49.2% |

Spread sensitivity, 2025 run (exact re-pricing, USD per point = 33.98):

| Spread | 0 | 4 | 6 | 8 | 12 | 20 |
|---|---:|---:|---:|---:|---:|---:|
| Net USD | +474.71 | +338.79 | +270.83 | +202.87 | **+66.95** | −204.89 |

The 2025 "holdout" is +0.67% at 12 points and −2.05% at 20 points. The entire
result lives inside the spread assumption. That is not a strategy, it is a
coin-flip straddling the cost line.

**The tester reports 0.00 commission on every deal.** Deriv is spread-only, so 12
points is the correct total there. On an ECN account (~1 point spread + $7/lot
round turn = ~8 points total) the 2025 result improves to about +203 USD — still
under 2.1%, still inside the noise band of a 47-trade sample.

**Broker choice is the single largest lever available, and it buys ~2% on a good
year, not a business.**

---

## 4. Result C — the one real bug (already fixed)

`docs/FXMOPOL_PROFIT_DIAGNOSIS.md` found that the FXMOPOL EA's down-sloping
tester graph was **rule misalignment**, not signal or cost:

| Slice | Trades | Net |
|---|---:|---:|
| All tester trades | 12 | −12.49 |
| Should have been skipped (CHOP / WAIT / bad proximity) | 9 | **−27.69** |
| Rule-aligned only | 3 | **+15.20** |

The EA took trades the research playbook would reject. That is a genuine
execution defect and it was fixed (entry gating, signal-first exits, D1 alignment
gate). Post-fix the GBP playbook run is 3 trades, +6.83, zero bad entries.

That is a real fix — but it does not create edge. It shrinks the sample to the
point where the result means nothing. **Rule alignment stops you leaking; it does
not make you money.**

---

## 5. What is actually fixable

| Loss source | Share of loss | Fixable? | How |
|---|---:|---|---|
| Rule misalignment / bad entries | bug-class | **Yes — done** | entry gating, D1 gate |
| Spread + commission | 86–102% | Partly | cheaper broker (~8 pts), lower turnover |
| Swap drag on intraday | small | Yes | hold less, or trade carry instead |
| **Signal has no edge** | the rest | **No** | not by tuning, stops, or filters |
| Over-trading a zero edge | amplifying | Yes | trade less (which shrinks the sample) |

The trap: every item above that *can* be fixed pushes the strategy toward zero,
and the one thing that would make it positive — the signal — cannot be fixed
because there is nothing there to amplify.

---

## 5a. The carry EA was placing zero orders — three real bugs

While auditing the default strategy the tester produced **0 trades over 6 years**.
That was not the market; it was the EA. Three defects, all in
`EA/Include/SovereignCarryMom.mqh`:

| # | Bug | Effect |
|---|---|---|
| 1 | Carry direction inverted: longs took the *lowest* rate differential | Shorting the carry premium the research was trying to earn |
| 2 | Sizing was notional-based (`$10k × 0.35% = $35`) | 0.00027 lots, floored to 0, every leg `continue`d → no orders |
| 3 | `PickExtreme()` re-picked the same rejected leg each iteration | If the top-carry leg failed the momentum filter, the book stayed empty forever |

Plus two environment faults that made the failure silent:

| # | Fault | Effect |
|---|---|---|
| 4 | EA reads data with `FILE_COMMON`; `install_ea.ps1` wrote to `MQL5\Files` | Every rate/VIX/century/evidence file invisible → carry silently fell back to swap |
| 5 | Rebalance fired on the first tick of the month (rollover) | Spreads 52–187 pts vs `InpMaxSpread=12`, and `retcode 10018 market closed` |

Fixes applied: carry now longs the highest differential and shorts the lowest
(matches `research/carry.py`); sizing is risk-based to the ATR stop
(`LotsForRisk` using `SYMBOL_TRADE_TICK_VALUE`, with a guarded min-lot fallback);
selection ranks the whole universe once and walks past legs that fail the
momentum filter; `install_ea.ps1` mirrors data to `Common\Files`; and the monthly
book is computed **once** and then held, with execution retried hourly inside the
first 5 days of the month so it lands in a tradable session.

Post-fix (2020-01 to 2020-05, EURUSD D1, EURUSD chart): 4 legs opened on
2020-01-02 00:00 (GBPUSD, AUDUSD, USDCAD, NZDUSD), book frozen, no churn on retry.

**This does not overturn the verdict.** It removes a defect that made the EA
untestable; the surviving edge is still the weak carry/momentum premium in §6.3.

---

## 6. Verdict

1. **Cost is the executioner, not the crime.** At mid, the intraday entries are
   worth approximately nothing (−0.026R and +0.007R per trade over 15 years and
   55k trades). Retail friction turns "nothing" into −356R and −14,809R.
2. **No parameter change fixes a zero-edge entry.** This is arithmetic, not
   opinion: break-even cost is −1.52 points and +0.29 points. Both are below the
   floor of retail execution.
3. **The only surviving strategy is carry**, and it is a risk premium, not a
   signal: +1.64% CAGR, Sharpe 0.20, −26% max DD, and a 5.3-year drawdown. It
   earns interest, not alpha.
4. **Do not deploy.** The project freeze stands. This audit explains the loss; it
   does not overturn the verdict.

### Reproduce

```powershell
.\.venv\Scripts\python.exe research\cost_vs_signal.py
.\.venv\Scripts\python.exe research\loss_audit.py backtest\reports\london_breakout_2025_sp12.htm --spread 12
.\.venv\Scripts\python.exe research\loss_audit.py backtest\reports\*.htm --spread 12
```
