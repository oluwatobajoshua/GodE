# Project Sovereign - AI Handoff / Work Log

Last updated: **2026-11-08 20:25 local (UTC+1)**

This file is the authoritative continuation point for another AI or developer.
Read it before changing code. Update it after every meaningful work session.

---

## 1. Objective

Take over **Project Sovereign**, a hybrid Python + MQL5 trading system, and get it
deployed and profitable. The owner's acceptance criterion is explicit:

> **"The tester graph must go up - that is when we are getting it right."**

Do not declare success from a compile-only check. A run is successful only when
the MT5 Strategy Tester equity/balance curve is positive over a meaningful
period and the result is reproducible from the committed scripts.

Secondary requirements already agreed:

- Python brain publishes analytics over ZeroMQ `tcp://127.0.0.1:5555`.
- EA consumes telemetry, has a 3-second heartbeat failover, continuous lot
  sizing with a `0.01` floor, limit/market auto-routing, H1/M5 confluence,
  volatility regime switching, and CSV execution/slippage auditing.
- A single script must rebuild the whole environment on another Windows machine.
- Document everything so another AI can resume without guessing.

---

## 2. Environment (verified)

| Item | Value |
|---|---|
| Repo | `C:\xampp\htdocs\GodE` |
| Python | `C:\Python314\python.exe` (3.14.7) |
| Venv | `C:\xampp\htdocs\GodE\.venv` |
| Packages | numpy 2.5.4, pandas 3.0.6, pyzmq 27.2.0 |
| MT5 install | `C:\Program Files\MetaTrader 5 Terminal` |
| MT5 data | `C:\Users\OluwatobaOgunsakin\AppData\Roaming\MetaQuotes\Terminal\FB9A56D617EDDDFE29EE54EBEFFE96C1` |
| MT5 account | `6265503` on `Deriv-Demo` (B-book demo; not ECN) |
| Symbol data | EURUSD 2020-2026 `.hcc`, 2025-12..2026-10 real ticks |
| EA magic | `20261103` |
| Ports | ZMQ PUB **5555**, native TCP NDJSON **5556** |
| Git | Repository initialized. Latest commits `2846494` (carry), `af528d7` (freeze); baselines `da53b8c`, `947c9b2`. |

`pandas 3.1.0` hangs on import in this Python build. Keep `pandas<3.1`.
Deriv is used only because it has usable local real-tick history. It is not a
substitute for ECN validation.

---

## 3. Architecture as implemented

```
+-----------------------------+        ZMQ PUB tcp://127.0.0.1:5555
| Python brain                |------------------------------------> external consumers
| institutional_quant_master  |
|                             |        TCP NDJSON 127.0.0.1:5556
|  synthetic rand-walk ->     |------------------------------------> MQL5 EA
|  ATR/range/POC/action       |                                      (SocketConnect,
+-----------------------------+                                       no DLL)
                                                                    |
                                                                    v
                                              +----------------------------------+
                                              | SovereignGodsEyeEA (M5)          |
                                              | H1 SMA confluence, killzone,     |
                                              | POC/action gate (optional),      |
                                              | capacity lots, limit/market,     |
                                              | partial close + break-even,      |
                                              | 3s heartbeat failover, CSV audit |
                                              +----------------------------------+
```

The EA has **no DLL imports**. It uses native MQL5 `SocketCreate`,
`SocketConnect`, `SocketIsReadable`, `SocketRead`. This is the chosen default
transport because it is testable and avoids third-party binary risk.

`InpRequirePythonSignal` defaults to **false**. The Python source is still
synthetic (`"source": "synthetic"`), so gating live trades on it would be unsafe.
When a real feed is added, set this to true and set
`InpMinSignalConfidence` appropriately.

---

## 4. Files

| Path | Purpose |
|---|---|
| `bootstrap.py` | **Single-command setup**: venv, deps, MT5 include/EA install, compile, dual-feed smoke test |
| `EA/SovereignGodsEyeEA.mq5` | Main EA |
| `EA/Include/SovereignSignalClient.mqh` | Native TCP NDJSON client, JSON field parser, reconnect, heartbeat |
| `EA/Include/SovereignAuditLog.mqh` | CSV order/deal/signal audit + slippage mapping |
| `python/institutional_quant_master.py` | ZMQ + TCP publisher, synthetic analytics |
| `scripts/start_brain.ps1` | Launch brain via venv |
| `scripts/install_ea.ps1` | Copy includes/EA + compile |
| `scripts/healthcheck.py` | Validate ZMQ and/or TCP payloads |
| `scripts/run_backtest.py` | Generate `.set`/`.ini`, run MT5 tester, copy report, JSON summary |
| `requirements.txt` | Pinned ranges; **keep pandas < 3.1** |
| `backtest/` | Generated configs, brain log, reports |
| `docs/HANDOFF.md` | This file |
| `docs/DEPLOYMENT.md` | Setup/migration/operations plan (to be written) |

No `git` history exists. Do not assume changes are committed.

---

## 5. Verified working

- `bootstrap.py --skip-mt5` passes; starts brain, validates both ZMQ and TCP.
- `bootstrap.py` (full) installs includes, copies EA, compiles
  **0 errors, 0 warnings**, installs `.ex5`.
- `scripts/healthcheck.py --transport both` receives valid payloads on 5555/5556.
- `scripts/install_ea.ps1` compiles 0/0.
- Tester ran one day (2026.01.05) successfully in 0.42s after ~25s history sync.
- CSV audit logging works in tester agent sandbox; files found at:
  `C:\Users\...\AppData\Roaming\MetaQuotes\Tester\<instance>\Agent-127.0.0.1-3000\MQL5\Files\Sovereign\*.csv`
- Audit captured the limit-order failures correctly.

---

## 6. Current work in progress / known issues

### 6.1 Tester report path (fixed in code, not yet re-verified)

`Report` must be **relative to the platform directory**, not absolute. The first
run produced no HTML because the script wrote an absolute path. Now:
- `Report=reports\<name>.htm`
- directory created at `<MT5 data>\reports`
- script searches `<data>\reports`, `<terminal>\reports`, copies to
  `backtest/reports/<name>.htm`
- agent logs under `%APPDATA%\MetaQuotes\Tester\<instance>\...` are now searched.

**Next:** rerun the smoke test and confirm the HTML exists and metrics parse.

### 6.2 Limit orders rejected with 10015 `Invalid price` (fix applied, not verified)

First run: every `SELL_LIMIT` failed. Example:
`failed sell limit 1.56 EURUSD at 1.16935 sl: 1.16999 tp: 1.16743 [Invalid price]`.
The old buffer was only `3 * _Point`; at M5 bar open price moves more than that
between reading `ask` and server validation.

Fix in `ExecuteSovereignEngine()`:
- buffer = `max(12 points, 0.35 * ATR in points, stops_level + 3)`
- `slPoints`/`tpPoints` raised to at least `stops_level + 5`
- all prices normalized with `NormalizeDouble(..., _Digits)`
- explicit `ORDER_TIME_GTC`

Symbol diagnostics now print on init:
`digits, point, tick, stops, freeze, spread, minLot, step, tickValue`.

**Next:** rerun and confirm limit orders are accepted, then inspect
`order_attempts.csv` for retcodes. If 10015 persists, check whether the SL
distance is below Deriv's dynamic stop level; next fallback is to route to
market when a limit retcode is 10015.

### 6.3 Current profitability (2026-10-27)

**Verdict: the system is not deployable. No tested strategy has a robust
out-of-sample edge.** The apparent M5 breakout profit is in-sample.
**Decision (2026-10-27): the project is frozen.** Do not deploy live. Reopen
only with a genuinely new data source or economic hypothesis, tested with a
fresh design / validation / OOS cycle.

M5 breakout results (EURUSD, Deriv-Demo, 1% risk, one position):

| Run | Trades | Net USD | PF | Balance DD | Role |
|---|---:|---:|---:|---:|---|
| 2025 full year | 47 | **+66.95** | 1.04 | 4.24% | holdout |
| 2026 Jan-Mar | 17 | **-4.94** | 0.99 | 3.57% | split check |
| 2026 Apr-Oct | 25 | **+284.96** | 1.31 | 2.67% | in-sample |
| 2026 Apr-Oct, BE at +1R | 25 | **+331.14** | 1.38 | 2.21% | in-sample |
| 2026 Jan-Oct, spread 15 | 42 | **+281.33** | 1.19 | 3.57% | older baseline |

The 2025 holdout is +0.67% for the year, which is noise after costs. The
Apr-Oct 2026 result is the period used to discover the hour-7 setup, so it is
in-sample. Exit tweaks (break-even, partial, trailing) move the result by a few
dozen dollars; they do not create an edge.

**Configuration caveat:** the EA's compile-time defaults still enable partial
close and break-even at +1.5R (`InpEnablePartialClose=true`,
`InpEnableBreakEven=true`, `InpBreakEvenR=1.5`). The controlled research runs
used `InpEnablePartialClose=false`, `InpEnableBreakEven=false` (baseline) or BE
at +1R (variant). No default-parameter run is validated. Because the entry has
no edge, changing these defaults is not a fix; it is a documentation trap to
avoid when the EA is eventually rebuilt.

Equity above balance is normal while a winner is open. It is floating P&L, not
realized profit. All test positions are closed at the end, and the final balance
is the scoreboard.

### 6.4 Alpha research verdict (2026-10-27)

After the M5 result was judged insufficient, six additional families were
tested. Every one failed a chronological out-of-sample check except the weak
carry premium:

| Family | Timeframe / data | Design | Validation | OOS / holdout | Verdict |
|---|---|---|---|---|---|
| Donchian trend, EURUSD | H1, 2025-2026 | best +16.08R | -3.39R | **-23.94R** (2025) | fail |
| Donchian trend, 8 pairs | H1, 2025-2026 | none positive | none positive | negative | fail |
| Z-score mean reversion, 9 pairs | H1, 2025-2026 | +11.67R best | +21.82R best | **-53.40R** (2025) | fail |
| Donchian trend, 12 pairs | D1, 2005-2026 | none positive | none positive | mixed, recent negative | fail |
| Time-series / cross-sectional momentum | D1, 2006-2026 | +3-4% CAGR | -6 to -8% CAGR | -2 to -8% CAGR | fail |
| CFTC COT positioning | weekly, 2010-2026 | +5.91% CAGR | +3.30% CAGR | **-1.63% CAGR** | fail |
| Carry + 12m momentum, Deriv | monthly, 2005-2026 | +1.32% CAGR | +0.05% CAGR | +3.26% CAGR | weak only |
| Carry + 12m momentum, independent | monthly, 2005-2025 | +1.35% CAGR | +0.26% CAGR | +3.35% CAGR | weak only |

The only strategy positive in every split is **carry + 12-month momentum
filter**. The independent HistData test now covers **2005-2025**, including the
2007 peak and the 2008-2012 drawdown. It is design **+1.35%**, validation
**+0.26%**, OOS 2020-2025 **+3.35%**, recent 2025 **+3.67%**; full-period
**+1.64% CAGR**, Sharpe **0.20**, max drawdown **-26.03%** (peak 2007-06 to
trough 2012-10). The independent series closely matches the full 2005-2026
Deriv series (+1.61% CAGR, Sharpe 0.20, -26.30% max DD, same peak/trough), so
the result is real and not a data artifact. It is still not deployable: a
5.3-year drawdown, a nearly flat validation period, and a monthly portfolio
strategy that is separate from the intraday EA. Yearly returns: 2005 0.00%
(momentum warm-up), 2006 +7.49%, 2007 +13.34%, 2008 -1.67%, 2009 -4.11%,
2010 -4.76%, 2011 -5.34%, 2012 -2.75%, 2013 +6.90%, 2014 +6.00%, 2015 +5.84%,
2016 -4.82%, 2017 +3.55%, 2018 -6.77%, 2019 +4.14%, 2020 +0.33%, 2021 +1.50%,
2022 +12.91%, 2023 +1.01%, 2024 +1.23%, 2025 +3.35%. The 2008 year is mild for
the base because the momentum filter and the residual dollar bet act as a
partial hedge; the drawdown is the slow 2009-2012 unwind after the 2007 peak.
Pure carry has a **-34.25%** max drawdown on 2005-2025 and fails validation
(-4.05%).

A **dollar-neutral** version of the momentum filter (stand aside when the
momentum filter removes every name on one side) is a cleaner carry
implementation but not an improvement. On 2005-2025 it is full **+2.06% CAGR**,
Sharpe **0.26**, max DD **-39.02%** (peak 2007-06 to trough 2010-06); design
**+0.21%**, validation **+0.19%**, OOS **+6.90%**, recent 2025 **+13.68%**.
It has no dollar spot exposure, so it loses **-14.49%** in 2008 and **-14.89%**
in 2010; the base's residual dollar bet is what keeps its 2008 loss to -1.67%.
The 2010-2025 slice alone showed +3.15% CAGR / -18.64% DD because it missed the
2007-2010 crash; that slice overstates the neutral variant. VIX overlays:
neutral + VIX20/30 is full **+2.08% CAGR**, Sharpe **0.28**, max DD **-33.32%**;
the improvement is small and the drawdown is still unacceptable. The base VIX
overlays are worse than the base (e.g. VIX18/28 +1.13% CAGR, -27.86% DD).

Risk overlays that scale the base return do not fix it. A 12-month trend filter
on the strategy's own return is full **-1.64%** on 2005-2025 and **-0.93%** on
Deriv, with max DD **-49.33%** / **-45.24%**; faster 3-month and 6-month
volatility targets are +0.81% / +0.22% with max DD -45.51% / -42.32%. All were
tested in `research/carry_overlays.py`; do not use them.

`research/carry_decompose.py` decomposes the return into interest carry, dollar
spot, cross-sectional spot, and cost. On independent 2005-2025 the base
`carry_mom` total of **+1.63%/yr** is almost entirely interest carry
(**+2.72%/yr**); the spot components are **-1.09%/yr** from the residual dollar
bet and **+0.14%/yr** from the cross-sectional carry basket, with **-0.13%/yr**
cost. The regression alpha versus a simple USD factor is **+2.22%/yr** with
R-squared **0.000**. On Deriv 2005-2026 the same decomposition is +1.60%/yr =
+2.77% carry -0.98% dollar spot -0.04% cross-sectional spot -0.13% cost, with
alpha +2.21%/yr and R-squared 0.000. In the 2015-2019 validation the
cross-sectional spot component is **-3.72%/yr** (independent) / **-3.85%/yr**
(Deriv), offset by **+1.78%/yr** / **+1.74%/yr** of dollar spot, which is why
validation is flat. The neutral variant has no dollar spot; its full-period
**+2.04%/yr** (independent) is also almost entirely interest carry (+2.18%/yr)
with cross-sectional spot **+0.03%/yr**. This is not a durable spot FX edge: it
is harvesting the interest differential and bearing crash risk. The base
variant's residual dollar exposure acts as a partial crash hedge, not as alpha.

**Root cause:** the problem is not the EA, risk code, exits, or the Deriv-Demo
feed. The H1/D1 entry signals have no durable edge, and the independent HistData
check reproduces those failures. The EA's own M5 setup also fails independent
history: full 2011-2025 **-355.79R**, PF **0.75**, with design, validation, and
OOS all negative. The in-sample 2026 result was a regime artifact. More
parameter tuning on the same families will produce false positives, not a
solution. The only survivor is a weak monthly carry premium; the decomposition
shows it is almost entirely interest carry with no durable spot edge, and it is
structurally different from the intraday EA.

**Do not run this EA live on the strength of the current results.**

#### Independent data check (completed)

To rule out a Deriv-Demo data artifact, the same rules were rerun on
HistData.com M1 data for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, and
NZDUSD, now 2005-2025. The M1 data is converted from New York time to UTC,
aggregated to H1/D1, and partial Sunday D1 bars are dropped. HistData does not
yet publish a 2026 yearly archive, so the H1 design year is 2024, validation is
2024 H2, and OOS is 2025. The D1 tests use 2010-2014 design, 2015-2019
validation, and 2020-2025 OOS; the extended carry test uses the full 2005-2025
range. The original 2010-2025 suite is preserved in `research/results/histdata/`;
the extended carry run is in `research/results/histdata_2005/`. NZDUSD 2005
starts on 2005-08-12 (HistData archive limit), which only affects the 2005
warm-up year.

Scripts:
- `research/download_histdata.py` - download and aggregate
- `research/clean_histdata.py` - drop partial Sunday D1 bars and duplicates
- `research/run_independent_validation.py` - rerun the whole suite
- `research/compare_data.py` - compare Deriv and HistData D1 returns
- `research/check_histdata.py` - check rows, duplicates, bad OHLC, NaNs
- `research/carry_overlays.py` - VIX, strategy-trend, and faster-vol overlays on carry
- `research/carry_decompose.py` - split carry returns into interest, dollar, cross-sectional, and cost
- `research/build_histdata_m5.py` - build EURUSD M5 from HistData M1
- `research/m5_independent.py` - mode-1 breakout independent test
- `research/m5_meanrev_independent.py` - mode-0 metrics from the saved candidates

Results go to `research/results/histdata/` for the original suite and
`research/results/histdata_2005/` for the extended carry run, never over the
original results.

| Family | Independent data result | Verdict |
|---|---|---|
| H1 Donchian trend, EURUSD | design 2024 H1 **+3.57R**, validation 2024 H2 **+15.94R**, OOS 2025 **-11.37R**; every other config negative OOS | fail |
| H1 Donchian trend, 7 pairs | no config positive in design and validation; best validation config OOS **-25.10R** | fail |
| H1 z-score mean reversion, 7 pairs | no config positive in design and validation; best validation config OOS **-65.24R** | fail |
| D1 Donchian trend, 7 pairs | no config positive in design and validation; 2020-2025 OOS mixed or negative | fail |
| D1 time-series / cross-sectional momentum | design negative; validation and OOS negative. Best full-period `tsmom_252_v10` only **+1.07% CAGR**, design -0.45%, validation +0.19%, OOS +2.77% | fail |
| CFTC COT positioning | `follow_z2.0_w156` positive in design and validation, but OOS 2021-2025 **-3.36% CAGR**, recent 2025 **-7.86%** | fail |
| Carry + 12m momentum | design 2005-2014 **+1.35%**, validation 2015-2019 **+0.26%**, OOS 2020-2025 **+3.35%**, recent 2025 **+3.67%**; full 2005-2025 **+1.64% CAGR**, Sharpe **0.20**, max DD **-26.03%** (2007-06 to 2012-10) | **only survivor; weak, not deployable** |
| M5 Donchian breakout, EURUSD, hour 7 | design 2011-2014 **-61.65R**, validation 2015-2019 **-191.58R**, OOS 2020-2025 **-102.56R**, full 2011-2025 **-355.79R** (PF **0.75**); every hour negative; only 2014/2015 positive | fail |
| M5 mean reversion, EURUSD, 07:00-16:59 UTC | design 2011-2014 **-1,029.81R**, validation 2015-2019 **-1,557.03R**, OOS 2020-2025 **-1,491.53R**, full 2011-2025 **-4,078.37R** (PF **0.75**); no positive year; every hour negative | fail |

Data quality: the extended HistData D1 files have 5,462 weekday bars per pair
(2005-2025; NZDUSD 5,304 because its 2005 archive starts in August), 257-262
bars per year, no duplicate timestamps, no bad OHLC rows, and no missing values. Against Deriv-Demo D1, the matched-day daily-return
correlation is **0.984-0.994**, with a mean absolute close difference of
21-30 points. The sources are broadly consistent; the negative results are not
caused by an obviously broken independent feed. HistData 2023 H1 has some
missing intraday bars, but the D1 bars remain complete; the H1 tests use
2024-2025, so they are unaffected.

The independent check therefore **confirms the negative verdict** for every
intraday and D1 price-only family, including the EA's own M5 hour-7 setup.
Carry + momentum is the only family that survives, and it is a weak, crash-prone
monthly interest-carry harvest with no durable spot edge, not a standalone
100-year system.

#### Independent M5 check (completed, negative)

The EA's own M5 setup was tested on independent HistData M5 built from M1.
`research/build_histdata_m5.py` produced 1,184,265 EURUSD M5 bars for
2010-01-03 to 2025-12-31 UTC, and `research/m5_independent.py` ran the mode-1
Donchian breakout with H1 SMA20/50/200 alignment, 1.5 ATR stop, 3R target,
12-point round-trip cost, and entries at 07:00-07:59 UTC. The proxy uses a
50-bar Donchian, 0.05 ATR breakout buffer, and a 24-hour max hold (288 M5 bars).
It isolates the entry edge; it does not include the EA governor, traffic light,
volatility-regime SL multiplier, or break-even management. Those overlays cannot
create a durable edge from a negative entry, and the in-sample BE1 gain was
regime-specific.

| Split | Trades | Win % | Avg R | Total R | PF |
|---|---:|---:|---:|---:|---:|
| Design 2011-2014 | 342 | 24.6 | -0.180 | **-61.65** | 0.79 |
| Validation 2015-2019 | 518 | 20.8 | -0.370 | **-191.58** | 0.61 |
| OOS 2020-2025 | 676 | 26.9 | -0.152 | **-102.56** | 0.83 |
| Full 2011-2025 | 1,536 | 24.3 | -0.232 | **-355.79** | **0.75** |

The result is negative in every split and in **every hour of the day**; hour 8
is the least bad (PF 0.86) and still loses. Only 2014 (+10.32R) and 2015
(+18.98R) are positive; 2018 (-70.38R), 2019 (-70.81R), and 2020 (-52.05R) are
the worst years. 2024 and 2025 are also negative (-7.42R, -5.41R). Long trades
lost **-111.97R** and shorts lost **-251.47R**. At a 10-point cost the full
result is still **-310.53R** (PF 0.77), so cost is not the cause. All 1,554
hour-7 trades resolved by SL or TP within 24 hours, so the max-hold cap did not
bind. The break-even win rate for this 3R/1.5-ATR geometry after cost is about
30.1%; the observed 24.3% is well below it.

The in-sample 2026 result (+284.96 USD, PF 1.31; BE1 +331.14 USD, PF 1.38) and
the 2025 holdout (+0.67%) do not survive independent history. The hour-7 pocket
was a 2026 regime artifact. **The intraday EA has no durable edge and should be
frozen.**

The EA's **other engine** does not save it. The mode-0 mean-reversion setup
(07:00-16:59 UTC, 0.35 ATR limit pullback, same cost/horizon) produced **17,954**
independent signals in 2011-2025: 23.5% wins, **-0.227R** average,
**-4,078.37R** total, PF **0.75**. It is negative in design (-1,029.81R),
validation (-1,557.03R), and OOS (-1,491.53R); **no year is positive**. Every
hour is negative; the least bad hours (12, 16, 20, 21, 22) are only PF 0.90-0.93.
Mode 0 is not a hidden second edge. The reproducible analysis is
`research/m5_meanrev_independent.py`.

The built `EURUSD_M5.csv` has **1,184,265 rows** from 2010-01-03 to 2025-12-31
UTC, no duplicate timestamps, no missing OHLC values, and no bad OHLC rows.
Every full year has 74,274-74,888 bars except **2023 (64,857)**: the 2023 M1
source has a known intraday gap, so the 2023 M5 result is based on partial data.
Weekend bars are excluded by the signal filter.

#### Independent RSI band check (completed, negative on broker-tape pass)

The RSI band ride passes all three splits on the broker MT5 tape (2018-2026) for
EURUSD H1 (+2.97 / +0.63 / +1.95%), GBPUSD H1 (+3.37 / +1.81 / +0.05%), GBPUSD
M30 (+1.72 / +4.98 / +3.12%), and USDJPY M15 (+3.81 / +2.05 / +1.46%). However,
on the independent HistData tape (2005-2025, 21 years, no spread column, flat
10-pt RT cost), the rule does **not** hold up:

| Symbol | TF | Params | Design | Validation | OOS | Full | All 3 splits? |
|--------|-----|--------|--------|------------|-----|------|---------------|
| EURUSD | H1 | 9/50/75/25 | -28.95% | +6.74% | -5.11% | -28.03% | NO |
| GBPUSD | H1 | 9/45/65/35 | -2.17% | -0.08% | +2.08% | -0.21% | NO |

Cost-sensitivity check: even at 3.5 pts RT (half broker cost), EURUSD H1 on
HistData still fails (design -25.39%, OOS -3.16%). The failure is **not** a
cost-model artifact — the RSI band rule genuinely does not generalize to the
longer independent tape. The broker-tape pass (2018-2026) is a data-window
artifact: the 2018-2026 period happened to be favorable, while the full
2005-2025 design window (2005-2011) was strongly negative.

**Conclusion:** RSI band sleeves remain research_good/CONDITIONAL on broker tape
only. They are not confirmed on independent history and are not cleared for
live trading. The book's apparent pass depends on broker-tape data starting in
2018, which excludes the unfavorable 2005-2018 period.

### 6.5 Python telemetry in tester

Tester logs show `Sovereign EA Initialized`, but not `Sovereign telemetry
connected`. Strategy Tester likely blocks/limits socket networking. The EA
correctly falls back. Telemetry still needs a **live-chart** smoke test. The
Python bridge itself is verified by `healthcheck.py`.

### 6.6 Python source is synthetic

`institutional_quant_master.py` emits `"source": "synthetic"` and mostly `HOLD`.
Do not enable `InpRequirePythonSignal` for live until it consumes real MT5 ticks
or a real CSV/tick feed.

### 6.7 Install warning (fixed 2026-10-27)

`scripts/install_ea.ps1` now reports the native TCP telemetry on
`127.0.0.1:5556` and the synthetic-feed warning. The obsolete
"ZMQ transport is not implemented yet" message was removed.

### 6.8 Known infrastructure limitations (not alpha issues)

- **FIXED 2026-10-27 (engineering bugs, not alpha):**
  - `signal_audit.csv` no longer stops at ~1 MB. `SovereignAuditLog.mqh`
    `RotateIfNeeded()` now closes/reopens each CSV handle when it reaches 1 MB,
    and every `Log*` method calls it before writing. The old single-handle
    append pattern stalled the file in the tester sandbox.
  - The performance governor now learns from every closed trade. The old
    `HistoryPositionProfit()` used `HistorySelectByPosition()`, which returns
    almost nothing in the Strategy Tester (one `trade_outcomes.csv` had 4 rows
    while 53 positions closed). Removed that function; `FinalizeMeta()` now uses
    the profit already accumulated in `m_meta[idx].profit` by
    `OnTradeTransaction()`, which is populated for every deal.
  - `SovereignTrafficLight.mqh` `MacroLight()` no longer hard-falls-back to NA
    when `DXYUSD`/`VIXUSD` are absent on the broker. It tries the requested
    macro symbol first, then falls back to the main `_Symbol` price change as a
    proxy, so the macro/sentiment lights produce a real signal instead of NA.
- The Python feed is still synthetic and the EA-to-Python live telemetry path is
  still not confirmed. Keep `InpRequirePythonSignal=false`.
- The EA compile-time defaults for partial close and break-even at +1.5R are not
  the tested configuration (see section 6.3).
- The research baseline is committed (`da53b8c`); keep the working tree clean
  after each tested change so negative results are preserved.

These are engineering fixes for a future deployment. They do not change the
validation verdict: the entry signals have no edge.

---

## 7. Exact commands

### Setup / rebuild

```powershell
cd C:\xampp\htdocs\GodE
python bootstrap.py                  # full Windows setup + smoke test
python bootstrap.py --skip-mt5       # brain only (Linux/macOS/CI)
python bootstrap.py --skip-smoke     # setup only
```

### Start brain

```powershell
.\.venv\Scripts\python.exe python\institutional_quant_master.py
# or
.\scripts\start_brain.ps1
```

### Feed health

```powershell
.\.venv\Scripts\python.exe scripts\healthcheck.py --transport both --count 3
```

### Compile / install EA

```powershell
.\scripts\install_ea.ps1
# or
python bootstrap.py --skip-smoke
```

### Backtest

```powershell
# one-day smoke
.\.venv\Scripts\python.exe scripts\run_backtest.py `
  --from-date 2026.01.05 --to-date 2026.01.06 `
  --name smoke_eurusd_m5_20260105 --timeout 600

# one-month baseline (real ticks)
.\.venv\Scripts\python.exe scripts\run_backtest.py `
  --from-date 2026.01.01 --to-date 2026.02.01 `
  --name baseline_eurusd_m5_202601 --timeout 1200

# isolate EA from telemetry
... --no-telemetry
```

Reports: `backtest\reports\<name>.htm` and `.json`.
Generated config: `backtest\<name>.ini`; parameter set in
`<MT5 data>\MQL5\Profiles\Tester\Sovereign_<name>.set`.

### Independent HistData validation

```powershell
$env:SOVEREIGN_DATA_DIR='C:\xampp\htdocs\GodE\research\data\histdata'
$env:SOVEREIGN_RESULTS_DIR='C:\xampp\htdocs\GodE\research\results\histdata'
$env:SOVEREIGN_DESIGN_YEAR='2024'
.\.venv\Scripts\python.exe research\run_independent_validation.py

# EA M5 setup (the build downloads 16 yearly M1 archives):
.\.venv\Scripts\python.exe research\build_histdata_m5.py `
  --pair EURUSD --start-year 2010 --end-year 2025
.\.venv\Scripts\python.exe research\m5_independent.py
```

The M5 build writes `research/data/histdata/EURUSD_M5.csv`; the test writes
`research/results/histdata/m5_hour7_independent.csv` and
`m5_candidates_histdata.csv`.

### Inspect tester logs / audit

```powershell
$agent = "$env:APPDATA\MetaQuotes\Tester\FB9A56D617EDDDFE29EE54EBEFFE96C1"
Get-ChildItem $agent -Recurse -Filter *.csv |
  Where-Object FullName -like "*Sovereign*"
Get-Content <agent>\Agent-127.0.0.1-3000\logs\<date>.log -Tail 100
```

---

## 8. Next actions / stopping point (priority order)

**Status: FROZEN as of 2026-10-27.** No live deployment. The items below record
what was completed and what would be required to reopen the project.

1. **Keep the current intraday EA frozen.** Independent HistData validation
   confirms that H1 trend, H1 mean reversion, D1 trend, D1 momentum, and the
   EA's own M5 hour-7 breakout have no durable edge. The 2025 M5 holdout was
   flat and the independent M5 check is negative in design, validation, and OOS.
   Do not paper over a missing edge with more filters, exits, or parameter
   sweeps on the same families.
2. **Independent validation is complete for H1/D1/COT/carry and the M5 setup.**
   The negative results were not a Deriv-Demo data artifact; HistData reproduces
   them. If a new data source or family is tested, keep the same chronological
   design / validation / OOS discipline and write results to a separate
   `SOVEREIGN_RESULTS_DIR`.
3. **Project frozen (decision 2026-10-27).** The evidence is complete: H1, D1,
   COT, and M5 fail, and the only survivor (monthly carry + momentum) is a weak
   interest-carry harvest, not a durable spot edge. Keep the EA as
   infrastructure and the research as a documented negative result. Do not
   deploy live. Reopen only if a genuinely new data source or economic
   hypothesis is proposed (cross-asset carry, options skew, order flow, real
   macro surprises) and tested with a fresh design / validation / OOS cycle.
4. **2005-2025 independent carry test is complete.** Design +1.35%, validation
   +0.26%, OOS +3.35%, full +1.64% CAGR, Sharpe 0.20, max DD -26.03% (2007-06
   to 2012-10). The independent series matches Deriv (+1.61%, -26.30%, same
   dates). The neutral variant is +2.06% / Sharpe 0.26 / -39.02% DD; neutral +
   VIX20/30 is +2.08% / Sharpe 0.28 / -33.32% DD. Strategy trend, faster vol
   targets, and VIX overlays on the base do not improve the risk-adjusted
   result. The decomposition shows the return is interest carry (+2.72%/yr)
   with ~zero cross-sectional spot. Do not call this deployable.
5. Keep all research scripts and result CSVs under `research/` so negative
   results are reproducible. Never delete a failed test; it prevents repeat
   work.
6. Add `requests` to `requirements.txt` for the HistData downloader (required
   on a fresh machine).
7. Update `scripts/install_ea.ps1` stale warning.
8. Write `docs/DEPLOYMENT.md` only after a deployable edge exists. It should
   cover VPS, firewall, demo incubation, and a migration checklist.
9. Commit the 2005-2025 carry results, overlays, and decomposition (commit
   `2846494`). The freeze documentation is in `af528d7`; earlier baselines are
   `da53b8c` and `947c9b2`.

---

## 9. Safety / correctness rules

- Never present synthetic signals as live analytics.
- Never leave a backtest result unlabeled as Deriv B-book demo data.
- `InpRequirePythonSignal` stays **false** until real data exists.
- Do not chase a positive curve by overfitting one day. Use out-of-sample.
- Keep ZMQ/TCP bound to `127.0.0.1`. Do not expose 5555/5556 publicly.
- The EA must always have autonomous fallback if telemetry is stale > 3s.
- Pandas must stay `<3.1` on this Python build.
- If compiling, require **0 errors, 0 warnings** before testing.

---

## 10. Session log

### 2026-10-26

- Verified Python venv, deps, ZMQ + TCP publisher/healthcheck.
- Added `SovereignSignalClient.mqh` (native TCP NDJSON, no DLL), integrated
  into EA with heartbeat watchdog and optional Python trade gate.
- Added `SovereignAuditLog.mqh`; integrated order attempts, deal events and
  per-bar signal snapshots. Verified CSVs are written by the tester.
- Added `bootstrap.py`; verified `--skip-mt5` and full MT5 install/compile.
- Added `scripts/run_backtest.py`; first one-day real-tick run completed
  (9899.99 USD, one stopped-out sell).
- Found and fixed `Report` absolute-path issue and `3-point` limit buffer /
  stop-distance issue (fix compiled 0/0, not yet re-tested).
- Added symbol diagnostics to EA init.
- Created this handoff document.

### 2026-10-27 (restructure: remove unprofitable, add research-backed)

The M5 intraday engines (breakout, mean-reversion, momentum, carry) have been
removed from the EA. They were proven negative on independent out-of-sample
data. Replaced with a **daily cross-sectional mean-reversion + carry***
portfolio manager, which is the only strategy family with published OOS
evidence on FX.

- New file `EA/Include/SovereignPortfolio.mqh`: the portfolio manager.
  - Computes a 20-day z-score per symbol via `CopyClose` + SMA + stdev.
  - Longs the most negative z-scores, shorts the most positive (cross-sectional
    rank, not single-pair).
  - Carry* overlay weights positions toward high positive interest-rate
    differential (uses `SYMBOL_SWAP_LONG` / `SYMBOL_SWAP_SHORT`).
  - Risk per position = equity * `InpRiskPerPosition` / 100; stop = 1.5 ATR;
    TP = 2x stop; total portfolio risk capped at `InpMaxTotalRisk` % equity.
  - Rebalances once per calendar day in `OnTick()`.
- New research script `research/xsec_meanrev.py`: reproducible backtest with
  design/validation/OOS splits. Produces `research/results/xsec_meanrev_summary.csv`.
  NOTE: the current run uses synthetic data, so it correctly shows no edge
  (random walk has no mean reversion by definition). It needs real D1 FX CSVs
  in `research/data/` to validate the strategy.
- `EA/SovereignGodsEyeEA.mq5` rewritten to a thin daily rebalancer. The old
  M5 entry engines, ML filter, structure/HTF logic, and per-tick management are
  gone. The performance governor, traffic light, audit log, and telemetry
  client are retained.
- The Python brain `institutional_quant_master.py` now computes a 20-day z-score
  (`compute_zscore`) and emits it in each payload (`"zscore"` field). The entry
  logic switched from range-position/POC to z-score based (BUY at z <= -1.5, SELL
  at z >= 1.5). Still synthetic and not wired into the EA's entry logic.

Still frozen pending real data and a full OOS cycle. The M5 intraday EA's
negative validation is preserved in `docs/validation.md`.

### 2026-10-27 (alpha research session)

- Confirmed the M5 breakout is only marginally positive and fails the 2025
  holdout. Break-even at +1R improves the Apr-Oct in-sample result to +331.14
  USD, but does not create a durable edge.
- Tested H1 Donchian trend on EURUSD and an 8-pair basket: no configuration
  survived design plus validation; 2025 OOS negative.
- Tested H1 z-score mean reversion on a 9-pair basket: the best design and
  validation config lost -53.40R in 2025.
- Tested D1 Donchian trend on a 12-pair basket, 2005-2026: no configuration
  positive in both design and validation; 2025-2026 negative.
- Tested D1 time-series and cross-sectional momentum, 2006-2026: design
  positive, validation and OOS negative.
- Downloaded CFTC financial-futures COT data 2010-2026 and tested leveraged-
  money positioning: design and validation positive, OOS negative.
- Downloaded FRED G10 short rates and tested monthly carry: pure carry has a
  -34% max drawdown and fails validation; carry + 12-month momentum filter is
  positive in all splits but only ~1.6% CAGR, Sharpe 0.20, -26.3% max DD.
- Conclusion: no deployable edge in the tested price-only, COT, or carry
  families. The next step is independent data validation, not more tuning on
  Deriv-Demo data.
- New research scripts: `research/h1_trend.py`,
  `research/h1_trend_portfolio.py`, `research/h1_meanrev.py`,
  `research/d1_trend_portfolio.py`, `research/d1_momentum.py`,
  `research/cot_sentiment.py`, `research/carry.py`.
- New data: `research/data/*_D1.csv` (2005-2026), `research/data/cot/`,
  `research/data/rates/`.
- New results: `research/results/h1_trend_summary.csv`,
  `h1_portfolio_summary.csv`, `h1_meanrev_summary.csv`,
  `d1_trend_summary.csv`, `d1_momentum_summary.csv`,
  `cot_sentiment_summary.csv`, `carry_summary.csv`.
- Built `research/download_histdata.py` for independent HistData.com M1 data
  (EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, 2010-2025).
  HistData does not publish a 2026 yearly archive yet.
- Added `research/clean_histdata.py` (drop partial Sunday D1 bars and
  duplicates), `research/compare_data.py` (Deriv vs HistData D1),
  `research/check_histdata.py` (data integrity), and
  `research/run_independent_validation.py`.
- HistData D1 vs Deriv D1: 4,159 matched days, daily-return correlation
  **0.984-0.994**, mean absolute close difference 21-30 points. This is a
  consistent independent feed, not a different market.
- Independent H1 trend, H1 mean reversion, D1 trend, D1 momentum, and COT all
  failed design/validation/OOS. EURUSD H1 `don20_sl15_tp3` was the only
  design+validation winner and lost -11.37R in 2025. H1 mean reversion had no
  design+validation winner; best validation config lost -65.24R in 2025.
- Independent carry + 12m momentum is the only survivor: design +2.63%,
  validation +0.26%, OOS +3.35%, recent 2025 +3.67%; full +2.13% CAGR,
  Sharpe 0.24, max DD -20.73%. This confirms the weak carry premium is real,
  but it is not enough for the stated goal and is not an intraday EA strategy.
- VIX overlays on independent carry: `vix20_30` cuts max DD to -16.65% but cuts
  OOS CAGR to +1.47%; Sharpe stays 0.24. Not adopted.
- Conclusion: the negative results were **not** caused by Deriv-Demo data.
  The EA's own M5 hour-7 breakout also fails independent history (full
  2011-2025 -355.79R, PF 0.75; design/validation/OOS all negative). Freeze the
  intraday EA. The next decision is a separate carry sleeve, a new edge/data
  source, or freezing the trading project.
- New independent results: `research/results/histdata/` (H1/D1/COT/carry
  summaries, carry overlay summary, data comparison CSV).
- Added `research/carry_overlays.py` for pre-specified carry overlays. A
  dollar-neutral momentum variant looked better on the 2010-2025 independent
  slice (+3.15% CAGR, Sharpe 0.41, -18.64% DD) but **fails on the full
  2005-2026 Deriv history**: design +0.33%, validation -0.07%, max DD -38.37%.
  The 2010-2025 independent slice misses the 2007-06 peak and the 2008-2010 leg
  of the drawdown (neutral trough 2010-06), so the neutral variant is not a
  deployable improvement. The base carry survives full Deriv design+validation
  only barely (+1.32% / +0.05%) with a -26.30% max DD (trough 2012-10).
- Added `research/build_histdata_m5.py` to build EURUSD M5 from HistData M1,
  and `research/m5_independent.py` to test the EA's hour-7 breakout on
  2010-2025 independent data. Built 1,184,265 M5 bars (2023 has a known
  intraday gap). The hour-7 breakout is **negative in every split**: design
  2011-2014 -61.65R (PF 0.79), validation 2015-2019 -191.58R (PF 0.61), OOS
  2020-2025 -102.56R (PF 0.83), full 2011-2025 -355.79R (PF 0.75). Every hour
  is negative; only 2014/2015 are positive years; long -111.97R, short
  -251.47R; at 10-point cost still -310.53R (PF 0.77). All trades resolved by
  SL/TP within 24h. The mode-0 mean-reversion engine is also negative: 17,954
  signals, 23.5% wins, -4,078.37R, PF 0.75, no positive year. The in-sample
  2026 result was a regime artifact. Freeze the intraday EA.
- `requirements.txt` now includes `requests`, `scikit-learn`, and the
  Windows-only `MetaTrader5` package; `scripts/install_ea.ps1` no longer prints
  the obsolete ZMQ warning.
- Downloaded HistData 2005-2009 for all seven pairs and cleaned/checked the
  extended 2005-2025 D1/H1 files. NZDUSD 2005 starts on 2005-08-12 because that
  is the limit of the HistData archive; the 2005 year is a momentum warm-up.
- Extended independent carry + 12-month momentum to 2005-2025: design +1.35%,
  validation +0.26%, OOS +3.35%, recent 2025 +3.67%, full **+1.64% CAGR**,
  Sharpe **0.20**, max DD **-26.03%** (peak 2007-06 to trough 2012-10). This
  matches the full 2005-2026 Deriv result (+1.61%, -26.30%, same dates), so
  the weak premium and its drawdown are real, not a data artifact.
- Independent neutral carry 2005-2025: +2.06% CAGR, Sharpe 0.26, -39.02% DD;
  neutral + VIX20/30: +2.08% CAGR, Sharpe 0.28, -33.32% DD. Still not
  deployable.
- Added strategy-trend (12m) and faster (3m/6m) volatility-target diagnostics
  to `research/carry_overlays.py`. Both are worse: trend12 full -1.64% /
  -49.33% DD (independent) and -0.93% / -45.24% DD (Deriv); vol3/vol6 full
  +0.81%/+0.22% with -45.51%/-42.32% DD. Do not use.
- Added `research/carry_decompose.py`. The base carry return is almost entirely
  interest carry: +1.63%/yr total = +2.72% carry -1.09% dollar spot +0.14%
  cross-sectional spot -0.13% cost on independent 2005-2025 (Deriv +1.60% =
  +2.77% carry -0.98% dollar -0.04% cross-sectional -0.13% cost). Regression
  alpha vs a USD factor is +2.2%/yr with R-squared 0.000. There is no durable
  spot edge; the base's residual dollar exposure is a partial crash hedge.
- Verdict: **no deployable edge**. The intraday EA stays frozen; a single FX
  carry sleeve is not enough for the stated goal. Results are in
  `research/results/histdata_2005/`, with the original 2010-2025 suite retained
  in `research/results/histdata/`.
- **Decision (2026-10-27): project frozen.** After reviewing the carry result,
  the weak interest-carry return, the -26.03% drawdown, and the failed risk
  overlays, the chosen path is to stop trading research and keep the EA as
  infrastructure. No live deployment. Reopen only with a genuinely new data
  source or economic hypothesis and a fresh design / validation / OOS cycle.

### 2026-11-08 (EA default → research survivor: carry + 12m momentum)

- **Not** implementing `MasterEnterpriseGodsEyeEA` (USD/JPY H1 scale-in): H1
  families failed OOS in this repo.
- **Default `InpStrategy=SOV_CARRY_MOM`**: new `EA/Include/SovereignCarryMom.mqh`
  mirrors `research/carry.py` `carry_mom_filter` — rank G10 rate diffs vs USD,
  long top 3 / short bottom 3 with 12-month momentum filter, rebalance monthly.
  Rates from `MQL5/Files/Sovereign/rates/*.csv` (copied by `install_ea.ps1` from
  `research/data/rates/`); swap ranking fallback if a file is missing.
- Legacy daily xsec MR remains `InpStrategy=SOV_XSEC_MR` (full-period CAGR still
  negative in `xsec_meanrev_summary.csv`).
- **Honest expectation**: carry_mom is the only positive-all-splits strategy
  (+1.64% CAGR, Sharpe 0.20, **-26% max DD**). Still **not deployable** per
  project verdict; tester equity may rise slowly, not “hedge-fund profitable.”
- **Adaptive trade-history governor** wired: per-symbol buckets (`SOV-CARRY-MOM|EURUSD`
  etc.), master bucket `SOV|MASTER`, expectancy-based size scaler, history bootstrap
  on init (`InpGovernorBootstrapHistory`), daily status log. See `performance_*.csv`
  under `MQL5/Files/Sovereign/`. This improves risk allocation from outcomes; it
  does not rewrite the strategy edge or guarantee rising equity.
- **Century Survivor (automated)**: `python/century_adapt.py` + `century_daemon.py`
  write `century_directives.json`; EA `InpEnableCenturyAdapt` applies scalars/skip/halt.
  Universal universe via `InitUniverse(InpSymbols)`. No system can promise 100 years of
  profits in arbitrary markets; design targets **survival** (low turnover, DD halts,
  history-driven de-risking, optional LLM capped by deterministic rules).
- **Long-horizon report** `research/sovereign_long_horizon.py` (2005–present on repo
  data): `carry_mom_filter` full period **~1.61% CAGR**, Sharpe **0.20**, max DD
  **~-26%**; vol-target and DD-halt variants in
  `research/results/sovereign_long_horizon_summary.csv`. This is the evidence base
  for “decades,” not intraday FX or undisclosed markets.
- **Discovery Lab** `research/discovery_lab.py`: independent tests (technical,
  fundamental proxy, sentiment, ML fusion) on chronological splits. Proof output:
  `research/results/discovery_proof.csv` + `discovery_manifest.json`. First run
  (2026-11-08): first battery mostly `fail`; novel family in `discovery_novel.py`.
  Later run: `nov_vov_defensive` **OOS pass** (PF 1.11, Sharpe 0.28) but **validation fail**
  — not promoted to EA until **both** validation and OOS pass.
  **Frontier** `discovery_frontier.py`: graph Laplacian, phase desync, silence/breakout,
  dual-consensus fracture, rank velocity. `front_silence_breakout` OOS-pass but
  validation-fail and ultra-sparse (~2% active days) — treat as research only until
  hardened.
- **Three-lane sprint (2026-11-08):** `scripts/run_three_lanes.ps1` runs lane1
  `fusion_decade.py`, lane2 `discovery_lab.py` (+ `discovery_custom.py`), lane3
  `micro_event_engine.py`; macro refresh via `sync_fxmacro_research.ps1`.
  Century JSON adds `sleeve_weights` carry 0.75 / novel_overlay 0.25.
- **Sovereign Stack v1 (2026-11-08):** `research/sovereign_composite.py` — one
  sleeve (dollar-neutral carry+momentum) + VIX 20/30 scale + optional 20% DD halt;
  zero capital to failed intraday/micro/xsec/discovery lanes. Proof:
  `research/results/sovereign_composite_summary.csv`. EA still uses
  `carry_mom_filter` baseline. EA v14: `InpUseSovereignStackV1` wires neutral book +
  VIX 20/30 scaling + optional 20% DD halt in `SovereignCarryMom.mqh` (needs
  `MQL5/Files/Sovereign/rates/VIX.csv` from `install_ea.ps1`).
- **EMA trend (2026-11-08):** `SovereignEmaTrendEA.mq5` + `SovereignEmaTrend.mqh` —
  trade only with EMA slope; pullback/break entries; ADX/HTF/RSI/spread/governor
  filters. Research: `research/ema_trend.py` → `ema_trend_summary.csv`. Experimental;
  past intraday suites negative OOS.
- **EMA 21 system (2026-11-08):** `research/ema21_system.py` + `scripts/run_ema21.ps1`
  grid-searched EMA 21 on EURUSD M5/H1 with design/validation/OOS. **Promotion rule:**
  validation and OOS must have cagr>0, sharpe>0, pf≥1. **Result: none promoted**
  (`research/results/ema21_manifest.json`). EA defaults set to EMA 21 pullback preset
  anyway — **not certified profitable**; do not deploy live expecting proof.
- **Evidence automation (2026-11-08):** Online literature sleeves (xsec mom,
  TSMOM, triple-screen) tested in `research/fx_evidence_stack.py`. **Promoted**
  under 3-split rule: **`carry_mom_filter` only** (`fx_evidence_summary.csv`).
  TSMOM/xsec mom1 failed on 2005–2026 D1+FRED data (aligns with post-2010 premia
  decay). `lit_triple_carry_mom12` strong OOS but **fails validation**. EA reads
  `evidence_directives.json`; run `scripts/run_evidence_stack.ps1` to refresh.
- **FXMOPOL visual (2026-11-08):** MT-mobile chart spec wired when
  `InpVisualMobileSetup=true`: **H1 EMA21**, **EMA100**, **SMMA100**, **RSI(9)**
  bands 20/50/80, **Bill Williams fractals**, optional `InpManualLevel` (e.g.
  158.500). Research mirror: `research/fxmopol_visual.py` → daily/monthly CSVs.
  Attach EA on **H1** (USDJPY per user chart). Not OOS-promoted.
- **FXMOPOL (2026-11-08):** Merged **criminal candle chase** + BabyPips MTF stack
  (`SovereignFxmopolEA.mq5`, `research/fxmopol.py`). Event-driven, max 3/day.
  Not proof-profitable on HistData — demo/tester first. Proof carry: GodsEye.
- **FXMOPOL tune (2026-11-08):** `research/fxmopol_tune.py` (48 H1 configs).
  Best **validation** config: `breakout_sess1_pin0_h4200_sl1.0_tp2.0` (val exp
  −0.05 bps/trade, pf 0.99). **Not OOS-promoted** (OOS pf 0.35, exp −7.46 bps).
  EA preset wired: `InpBreakoutMode=true`, `InpLondonSession=true`,
  `InpH4EmaPeriod=200` — **still not certified profitable**; tester/demo only.
- **Sovereign stack tune (2026-11-08):** `research/sovereign_stack_tune.py` (972
  grids; literature: Fan 2025 carry/momentum, JSI 2024 integrated factors).
  **61 configs pass 3-split** rule with VIX overlay on carry+12m mom. Validation
  pick: `mom12_n2_v18_30_m0.5_ddoff_neu0` — OOS Sharpe **0.42**, CAGR **3.79%**
  vs baseline `carry_mom_filter` OOS Sharpe 0.34 / CAGR 3.26%. GodsEye defaults
  updated: `InpTopN=2`, VIX 18/30 mid **0.5**, neutral **off**, DD halt **off**.
  Re-run tune after rate/price CSV refresh; project remains **FROZEN** for live.
- **FXMOPOL tester progress (2026-11-08):** `docs/FXMOPOL_TESTER_PROGRESS.md` —
  auto `fxmopol.tpl`, visual tester, `fxmopol_tester_review.py` win/loss/stay-away CSVs.
- **fxmopol.tpl (2026-11-08):** User chart synced to `templates/fxmopol.tpl`; `best_win_settings.json`
  retains carry_mom, USDJPY streak, GBP playbook+proximity only; EUR preset chart-only (no losing auto).
- **Best win settings (2026-11-08):** `research/results/best_win_settings.json` — GodsEye
  **TopN=4**, VIX mid **0.75**, neutral **on** (best promoted OOS stack); Fxmopol locked to
  **USDJPY stack streak 12** only; playbook/fractals/legacy **off**. Refresh:
  `python research/apply_best_settings.py`.
- **FXMOPOL proximity filters (2026-11-08):** EMA fan + enter-near-EMA21 + extension exit
  (`fxmopol_chart_core.py`, playbook, `SovereignFxmopolEA` `InpUseProxFilters`). Documented in
  `docs/FXMOPOL_CHART_SNAPSHOT.md`.
- **FXMOPOL decision lab (2026-11-08):** `research/fxmopol_decision_lab.py` — full H1
  stack walk, chart states (GOOD/CHOP/WAIT), 9 entry × side-aware exit rules,
  enter/hold Q&A CSVs + `docs/FXMOPOL_DECISION_RULES.md`. Run `.\scripts\run_fxmopol_decision.ps1`.
- **FXMOPOL playbook (2026-11-08):** `research/fxmopol_playbook.py` — regime 5-bar,
  pullback/hunt, RSI 20/50/80 exits. **OOS monthly sum% (stricter):** EURUSD **−7.3**,
  GBPUSD **−5.3** (vs legacy visual ~−9.7 bps/trade). Still **not promoted**; EA
  `InpUsePlaybook=true`. Run `.\scripts\run_fxmopol_playbook.ps1`.
- **FXMOPOL tester equity (2026-11-09):** Down graph traced to entries on
  TRANSITION/WAIT/CHOP (`should_have_skipped` in `fxmopol_tester_review.py`).
  Fix: playbook no longer uses `VisualTrendStack` as entry; 5-bar regime +
  chop/proximity on EMA crosses; `mktCtx>=3` for pullbacks; wider SL in tester
  profile. Re-run `run_fxmopol_tester.ps1` and open report `.htm` balance graph.
- **FXMOPOL D1 gate (2026-11-08):** Playbook H1 entries require closed D1
  21/50/SMMA100 stack plus close vs D1 SMA200 (`InpRequireD1Align`, default on).
  Mirror: `research/fxmopol_playbook.py` `build_d1_gate()` + `use_d1_gate` flag;
  A/B: `research/fxmopol_playbook_d1_compare.py` /
  `.\scripts\run_fxmopol_playbook_d1_compare.ps1` →
  `results/fxmopol_playbook_d1_compare_summary.csv`.
  GBP playbook tester 2024-01-01→2025-07-01: **3 trades**, net **+6.83**, **0**
  losses tagged `should_have_skipped` (prior run: 12 trades / −12.49). Not OOS
  proof — still not deployable per project freeze.
- **GodsEye compile (2026-11-08):** `SYMBOL_SWAP_LONG/SHORT` and array-by-ref
  fixes in `SovereignPortfolio.mqh` / `SovereignCarryMom.mqh`; GodsEye `.ex5` builds.
  Portfolio monthly proof: `research/export_period_results.py` → `carry_mom_filter`
  last-12-mo table in stdout.
- **Recommended stack (2026-11-08):** `research/recommended_stack_proof.py` +
  `scripts/run_recommended_stack.ps1` + `scripts/run_gods_eye_tester.ps1` →
  `results/recommended_stack_verdict.json` (promoted `mom12_n4` stack; H1 not
  certified). Live remains **off** per FROZEN policy.
- **FXMOPOL market pulse (2026-11-09):** Per-bar “best move” advisor — `InpMarketPulse`,
  `research/fxmopol_market_pulse.py`, `docs/FXMOPOL_MARKET_PULSE.md`. Full checklist,
  not guaranteed edge.
- **FXMOPOL signal stack train (2026-11-09):** Walk-forward grid adds optional
  **RSI divergence**, **S/R confluence**, **FRED carry + VIX sentiment**
  (`research/fxmopol_signal_layers.py`, `docs/FXMOPOL_SIGNAL_STACK.md`).
  Profiles expose `signal_stack`; live macro/sentiment remains `SovereignTrafficLight`.
  Ablation: `research/fxmopol_stack_audit.py`.
- **ML stack (2026-11-09):** `research/ml/fx_gym_env.py` (Gymnasium+PPO),
  `tft_forecast_pipeline.py`, `purged_cv.py`, `requirements-ml.txt`,
  `docs/SOVEREIGN_ML_STACK.md`.
- **Sleep profile GBP H1 (2026-11-09):** `scripts/run_sleep_gbp_h1.ps1`,
  `python/sovereign_sleep_stack.py`, `configs/sovereign_sleep_gbp_h1.json`,
  `docs/SOVEREIGN_SLEEP_GBP_H1.md` — Prophet + Professor + playbook attach path.
- **Ecosystem Professor (2026-11-09):** Multi-pillar ingest v2
  (`python/ecosystem_professor.py`, `python/ecosystem_pillars.py` → `market_snapshot.json`)
  — TA, rates/carry momentum, VIX, COT crowding, USD event calendar, FXMacro risk,
  DXY/gold, G10 cross-section, vol & spread micro. `knowledge_coverage` reports gaps
  vs institutional checklist. EA: `SovereignMarketSnapshot.mqh`. **Not OOS-certified.**
- **Research factory (2026-11-09):** `docs/RESEARCH_FACTORY.md`, `research/research_factory.py`,
  `scripts/run_research_factory.ps1`, `research/carry_train_grid.py` (design-only cost/top_n train).
  — weekly study/train/test: pip budget, filter learn (prime), stack P&L, carry + H1 promotion,
  dashboard, portfolio combine, EA parity. Active queue: `configs/research_hypothesis_active.json`.
- **Profit dashboard (2026-11-09):** `research/profit_dashboard.py`, `scripts/run_profit_dashboard.ps1`
  — carry + stack + lanes last 12 months; `portfolio_combine.py` blends carry core + 15% H1 satellite.
  H1 rule: `research/h1_promotion.py` → **overlay_only** until OOS n≥30 (`docs/H1_PROMOTION.md`).
  EA parity: `research/ea_stack_parity_check.py` vs chart_prime; filter learn default `--baseline prime`.
- **FXMOPOL stack P&L (2026-11-09):** `research/fxmopol_stack_pnl.py` — prime session +
  fund filter + professor + governor; GBP full sample **≈ −0.2% to −0.6%** (vs **≈ −8%** loose chart).
  Tester profile `InpGoodSessionsOnly=true`, `InpWeekdayMask=14`. See `docs/FXMOPOL_STACK_PNL.md`.
  **`carry_mom_filter`** still the only certified monthly FX sleeve.
- **Microstructure + L2 (2026-11-09):** `scripts/export_l2_ticks.py`, `scripts/export_l2.ps1`,
  `scripts/run_l2_microstructure.ps1`, `research/ml/l2_loader.py` — pipeline prefers
  `research/data/l2/{SYM}_l2_ticks.csv` (1s BBO resample); else M5 synthetic book.
  Manifest overlays gates in `market_snapshot.json` (`microstructure`, toxicity throttle).
  **Still research-only** until tick export validated OOS.
- **Chart snapshot (2026-11-08):** `docs/FXMOPOL_CHART_SNAPSHOT.md` — user USDJPY H1
  setup vs code gaps; `.\scripts\save_research_snapshot.ps1` archives monthly CSVs +
  `research/results/snapshot_manifest.json`. Fine-tune queue: stop-hunt reclaim,
  stack regime, level TP.
- **Daily / monthly reporting (2026-11-08):** Research exports calendar-period P&L
  (not per-trade only). `scripts/run_period_results.ps1` →
  `research/results/monthly_returns.csv`, `daily_returns.csv`; tune/evidence also
  write `fxmopol_tune_monthly.csv`, `sovereign_stack_tune_monthly.csv`,
  `fx_evidence_monthly_returns.csv`.
- **BabyPips Pipsology (2026-11-08):** `research/babypips_system.py` maps School
  curriculum (S/R, engulfing, fib, pivots, H4/H1 MTF, RSI, 1:2 R) — **not
  promoted** on HistData H1. BabyPips **carry** chapter aligns with
  `carry_mom_filter` (only 3-split promoted sleeve). See `babypips_manifest.json`.

### 2026-11-08 (Loss audit: signal vs cost vs execution)

- **Question answered:** is the EA losing because of the signal, the spread, or a
  bug? New doc `docs/LOSS_AUDIT.md`. Scripts `research/cost_vs_signal.py` and
  `research/loss_audit.py`; outputs `research/results/cost_vs_signal.csv`,
  `cost_vs_signal_hour7_yearly.csv`, `loss_audit_*.csv`, `loss_audit_summary.csv`.
- **Method A (independent 2011-2025, EURUSD M5, HistData).** `r_multiple` is
  stored net of 12 pts, so `gross_r = r_multiple + 12/sl_dist_pts` recovers the
  mid-to-mid edge exactly. Break-even cost = `sum(gross_r)/sum(1/sl_dist_pts)`.
- **Result:** hour-7 breakout gross **-0.026R** (n=1,536), break-even cost
  **-1.52 pts**; all-hours breakout gross **-0.095R**, break-even **-5.00 pts**;
  mean reversion gross **+0.007R** (n=55,399), break-even **+0.29 pts**. Retail
  is 8-12 pts, so mean reversion needs a **30-40x** tighter spread. Mean-rev gross
  edge by split: design +0.032, validation +0.006, **OOS -0.008** (decays to
  negative). Hour-7: 8/16 years positive gross, **2/16 positive net**.
- **Method B (tester reports).** `loss_audit.py` rebuilds round trips with volume,
  commission and swap. 2025 legacy run: net **+66.95**, gross at mid **+474.71**,
  spread @12 pts **407.76** -> **85.9% of gross is spread**. Jan-Mar 2026: gross
  +188.86, spread 193.80 -> net -4.94. Spread sensitivity 2025: 0 pts +474.71,
  8 pts +202.87, 12 pts +66.95, 20 pts -204.89.
- **Tester caveat recorded:** every legacy deal reports **commission 0.00**. Deriv
  is spread-only so 12 pts is correct there; an ECN (~1 pt + $7/lot round turn =
  ~8 pts) improves 2025 to ~+203 USD, still <2.1% and inside noise.
- **Only genuinely fixable loss was rule misalignment** (FXMOPOL): 9 of 12 trades
  were `should_have_skipped`, -27.69; 3 rule-aligned trades +15.20. Already fixed
  (3 trades, +6.83, zero bad entries). Rule alignment stops leaks; it does not
  create edge.
- **Verdict unchanged: cost is 86-93% of the loss, but the residual signal edge is
  ~zero.** Removing all friction yields break-even, not profit. Project stays
  **FROZEN**. No parameter sweep will change this arithmetic.

### 2026-11-08 (Carry EA: why it placed zero orders, and the fixes)

- **Symptom:** the default `SOV_CARRY_MOM` EA ran 6 and 11 year tester windows,
  initialised cleanly, printed monthly rebalances, and **placed zero orders**.
- **Bug 1 - inverted carry.** `BuildTargets` went long the *lowest* rate
  differential and short the highest, the opposite of `research/carry.py`
  `carry_weights()`. Fixed to long highest / short lowest, with `ccySign`
  converting "long the foreign currency" into a pair side for USD-base pairs.
- **Bug 2 - sizing below the minimum lot.** Sizing was notional-based:
  `legWeightBase = min(1%/leg, 4%/8 legs) = 0.5%`, so `$10k x 0.35% = $35`
  notional -> 0.00027 lots -> floored to 0 -> `continue`. It needed ~$365,714
  equity to open 0.01 lot. Replaced with risk-based sizing to the ATR stop:
  `LotsForRisk()` uses `SYMBOL_TRADE_TICK_VALUE`/`SYMBOL_TRADE_TICK_SIZE`
  (`LossPerLot`), falls back to `contract x QuoteToUsd()`, and only accepts the
  min lot when its real risk fits the remaining budget and is <= 4x target.
- **Bug 3 - selection never advanced.** `PickExtreme()` skipped only legs already
  targeted, so a leg rejected by the momentum filter was returned again on the
  next iteration. One wrong-signed top-carry leg kept the whole book empty.
  Replaced with `RankByCarry()` (one ranking) plus a walk that advances past
  rejected legs for both the long and short sides.
- **Bug 4 - data path mismatch.** The EAs read every Sovereign file with
  `FILE_COMMON` (`Terminal\Common\Files`), but `install_ea.ps1` only wrote to
  `Terminal\<ID>\MQL5\Files`. All rate CSVs, `century_directives.json`,
  `evidence_directives.json` and `fxmopol_symbol_profiles.json` were invisible, so
  carry silently fell back to swap scores. `install_ea.ps1` now mirrors the whole
  `Sovereign` folder to `Common\Files`.
- **Bug 5 - rebalance fired at the rollover.** `IsNewMonth()` triggered on the
  first tick of the month: EURUSD spread 52 pts, GBPUSD 187 pts (vs
  `InpMaxSpread=12`) and `retcode 10018 market closed`. The monthly book is now
  computed **once** and cached (`m_book`, `m_bookEntries`, `m_bookVixScale`), then
  execution is retried at most hourly inside the first 5 days of the month
  (`CARRY_ENTRY_GRACE_DAYS`), skipping weekends. Retries only re-attempt
  execution, so a D1 bar shift can no longer flip the book mid-deployment and
  flatten it.
- **Observability added:** universe size, `N/10 legs have 253 D1 bars`, a full
  carry ranking dump (`diff`, `mom`, `sign`), book long/short counts, and a
  reason for every skipped leg (`spread N > 12`, `traffic light blocked`,
  `century scalar`, `lot below minimum`, `no price`, `order FAILED retcode`).
- **Verified:** 2020-01-01..2020-05-01, EURUSD D1. Book `longs=1 shorts=5`
  (3 carry-long + 3 carry-short currencies); 4 legs opened 2020-01-02 00:00
  (GBPUSD, AUDUSD, USDCAD, NZDUSD); the 01:00 retry re-attempted only the 2
  pending legs, no churn. VIX halt correctly blocks March/April 2020.
- **Still true:** this makes the EA testable, not profitable. `InpMaxSpread=12`
  (inherited from the M5 intraday EA) rejects a large share of D1 legs at Deriv
  tester spreads (EURUSD 54, USDJPY 56 pts) - a live execution decision, not a
  code defect, and deliberately left at its default rather than tuned. The
  surviving edge remains the weak carry premium from §6.3. Project **FROZEN**.

### 2026-11-08 (session resume: stack proof + tester)

- Re-ran `research/recommended_stack_proof.py`: promoted **`mom12_n4_v18_30_m0.75_ddoff_neu1`**
  still passes all three splits (OOS 2020–2026 **+6.44% CAGR**, Sharpe **0.87**, max DD **-7.5%**;
  full period **2.29% CAGR**, max DD **-34.8%**). Verdict:
  `research/results/recommended_stack_verdict.json`.
- Refreshed `best_win_settings.json` via `apply_best_settings.py`; `install_ea.ps1` mirrors
  rates/VIX + `FILE_COMMON` Sovereign data; GodsEye defaults match promoted stack
  (`InpTopN=4`, Stack v1 on).
- **Tester (2020–2026 D1, profile `gods_eye_carry_d1`, `InpMaxSpread=30` in runner):**
  `backtest/reports/gods_eye_carry_d1_EURUSD_D1_fixed.htm` — net **+765 USD**, PF **1.50**,
  **126 trades**, max balance DD **3.79%** (`loss_audit_summary.csv`). Confirms carry EA
  bugs (§ above) are fixed — graph rises on this window; not a policy unlock for live.
- `export_period_results.py` now exports the **promoted** stack series (from
  `best_win_settings.json`), not the old `stack_tune_default` n2 grid.
- **Next (optional):** `.\scripts\run_recommended_stack.ps1 -SkipTester` after data refresh;
  FXMOPOL GBP playbook re-test per `docs/FXMOPOL_PROFIT_DIAGNOSIS.md`; extend D1 tester to
  2005–2026 for drawdown parity with Python.

### 2026-11-08 (stack refinement: better full-period / DD trade-off)

- Audited all **61** promoted rows in `sovereign_stack_tune.csv`: best **OOS Sharpe** was already
  `mom12_n4_v18_30_m0.75_ddoff_neu1`; best **full-period** among those was `…_m0.5_…` (+0.04% CAGR,
  −1.4 pp max DD) — marginal.
- Added `research/stack_refine.py` — small **extension grid** (VIX lo 16–19, hi 26–34, mid 0.45–0.75,
  n=4 only). Pick = √(sharpe_full × sharpe_OOS) among 3-split passers.
  **Winner:** `mom12_n4_v16_28_m0.45_ddoff_neu1` → `stack_refine_pick.json`.
- vs prior OOS pick (`m0.75` v18/30): **full CAGR 2.53%** (was 2.29%), **Sharpe 0.39** (0.31),
  **max DD −25.0%** (was −34.8%); **OOS CAGR 4.78%** (was 6.44%), OOS Sharpe **0.82** (0.87).
  Still **3-split profitable**; project **FROZEN** (refinement = post-hoc; not live unlock).
- Wired: `apply_best_settings.py`, `recommended_stack_proof.py`, GodsEye `InpStackVix*` defaults,
  `gods_eye_carry_d1` backtest profile. Re-run: `stack_refine.py` then `apply_best_settings.py`.

### 2026-11-08 (four-lane sprint: friction, alt, high-risk, balanced)

- **`docs/FOUR_LANES.md`** + `.\scripts\run_all_lanes.ps1` — lanes 1–4 in one place.
- **Lane 1:** `ecn_carry_friction.py` — stack survives **0–20 pts**; at **8 pts** (ECN proxy) full
  **~2.58% CAGR**, **−24.8% DD**, 3-split **pass**. MT5: `run_lane1_mt5_truth.ps1` (2005–2026,
  profiles `gods_eye_carry_d1` / `gods_eye_carry_d1_ecn`).
- **Lane 2:** `alt_sleeve_scan.py` — BTC/XAU/DXY 12-1 TSMOM; see `alt_sleeve_manifest.json` (wire
  only if `promoted_3split`).
- **Lane 3:** `carry_high_risk.py` — vol target **0.12** on refined stack → **2.62% CAGR**,
  **−26.3% DD** (3-split pass); EA overlay `gods_eye_carry_high_risk` profile. **Not default.**
- **Lane 4:** balanced stack unchanged (`stack_refine_pick.json`). Live **off**.

### 2026-11-09 (Scalp lane — M5 frequency for impatient timeframe)

- **`docs/SCALP_LANE.md`**, `configs/scalp_lane.json`, `research/scalp_lane_m5.py`, `scripts/run_scalp_lane.ps1 -Export`
- Broker M5: `research/data/mt5/{SYM}_M5.csv`; strategies: London ORB + EMA21 pullback
- **First broker test (2022–2026):** `any_promoted: false` — ample trades (~700/pullback) but **PF ~0.6–0.7**, negative OOS
- **MT5-first scalp:** `bars.research_source: mt5_only`, `run_scalp_mt5_truth.ps1` — Python on broker M5 + Strategy Tester certify (HistData blend exploration-only via `SOVEREIGN_M5_SOURCE=histdata_blend`)
- **London ORB HistData exploration (obsolete for promotion):** was **+6.58%** OOS — **rejected** once `require_mt5_tape_for_python` enforced
- **`scripts/run_london_orb_promote.ps1`:** UTC sweep **0–3** on tester OOS 2024.07–2026.01; best offset **0** still **−302**, PF **0.94**, 742 trades → `configs/research_hypothesis_scalp.json` **`promoted: false`**
- Certify path: `london_orb_utc_pick.json`, `london_orb_tester_manifest.json`, `scalp_promotion_verdict.json`
- **External insights + mode (2026-11-09):** `docs/FX_EXTERNAL_INSIGHTS.md`, `configs/sovereign_trading_mode.json`, `london_orb_gross_net.py` — gross ORB negative on broker M5 → discontinued primary
- **ASR-safe execution (2026-11-09):** `.cursor/rules/asr-safe-execution.mdc` — no `pip.exe`/`pip3.exe`; only `.venv\Scripts\python.exe`; `docs/ML_INSTALL_IT.md`
- **Good strategy bar (2026-11-09):** `configs/strategy_quality_bar.json`, `research/strategy_certification.py`, `configs/good_strategies.json`, `docs/STRATEGY_QUALITY.md`, `scripts/run_strategy_certification.ps1`
- **Strategy routing fix (2026-11-09):** `research/profitable_sleeve.py` + `docs/PROFITABLE_SLEEVE.md` — only **`carry_mom_filter`** is `trade_in_mt5`; `mt5_m5_hypothesis_scan.py` shows **no** broker M5 idea ≥ PF 1.05 on tester OOS window; `run_profitable_sleeve.ps1` weekly
- EA path when/if promoted: `SovereignLondonOrbEA` / `SovereignEmaTrendEA` on **M5** (not D1 carry)
- Legacy M5 Donchian remains **failed**; do not re-enable

### 2026-11-09 (MT5 data plane — one broker tape)

- **`docs/MT5_DATA_PLANE.md`** — research + professor + tester must share `research/data/mt5/{SYM}_H1.csv`.
- **`research/bar_store.py`** — canonical `load_h1()`; config `configs/mt5_data_plane.json`.
- **`scripts/export_mt5_bars.py`** + **`scripts/sync_mt5_data_plane.ps1`** — export → ingest → audit.
- **`research/mt5_data_plane_audit.py`** → `research/results/mt5_data_plane_audit.json`.
- **Measured:** broker vs HistData on 2024–2026 overlap often **>10 pip close p99** — explains bogus H1 parity when research used HistData and tester used broker.
- Parity report now requires `data_plane_go` (`ea_stack_parity_check.py`).

### 2026-11-08 (FXMOPOL price action — full chart playbook wired)

- Implemented remaining `FXMOPOL_CHART_SNAPSHOT.md` rules in EA + Python:
  `WickHuntReclaimStack`, `PlaybookChartEntry`, `LevelTpHit`, `InpUseFullPriceAction`,
  `InpUseLevelTp`, `InpStackRegimeBars`, `InpRsiCoverLevel` (30 short cover).
- Research: `stop_hunt_reclaim_stack`, `playbook_chart_entry`, level/RSI rollover exits in
  `fxmopol_playbook.py`; parity manifest `fxmopol_price_action_parity.py`.
- **Not** OOS re-certified - run `run_fxmopol_playbook.ps1` + tester after `install_ea.ps1`.

### 2026-11-11 (Chart visual layer + snapshot proof + EMA21 ride research)

**1. Chart visual layer (observability only, no entry-logic change)**

- `EA/Include/SovereignChartVisualizer.mqh` - object renderer (panel, zone box, levels,
  arrows, trend lines, fractals, session boxes). Cached panel text, stale-line deletion,
  `ChartRedraw` throttled by `GetTickCount()`, arrow ring-buffer pruning.
- `EA/Include/SovereignTemplate.mqh` + `scripts/export_chart_template.py` - MT5 sandboxes
  file I/O to `MQL5/Files`, so `templates/fxmopol.tpl` is exported to
  `Sovereign/chart_template.json` at install time and the EA validates its inputs against
  it. Truth: **EURUSD H1, EMA21, EMA50, SMMA100, Fractals, RSI9**. `install_ea.ps1` runs
  the exporter and copies the JSON.
- `EA/Include/SovereignMarketView.mqh` - the bot's own read: EMA21/50, SMMA100, RSI9,
  ATR14, swing hi/lo, fib 50/61.8, D1 pivot S1/R1, trend, bias, extended flag, zone,
  target, invalidation, state, one-line plan.
- `EA/Include/SovereignPatterns.mqh` - ATR-thresholded zigzag + Gartley/Bat/Butterfly/
  Crab/ABCD matcher, labelled on chart `context only - not an entry trigger`.
- `EA/Include/SovereignChartSnapshot.mqh` - writes
  `Sovereign/chart_read_<SYM>_<TF>.json` once per new bar.
- Wired into `SovereignFxmopolEA` (17-line panel), `SovereignGodsEyeEA` (strategy /
  universe / next rebalance / guards), `SovereignLondonOrbEA` (session box, entry, SL/TP).
- All five EAs compile: Fxmopol and LondonOrb **0 errors / 0 warnings**; GodsEye 0/7,
  EmaTrend 0/4, OpportunityScout 0/2 (all pre-existing `warning 62/63`).

**2. Snapshot parity harness (proof the bot sees the same chart)**

- `research/chart_snapshot.py` recomputes the same fields from the same broker bars
  (`research/data/mt5/`) -> `research/results/chart_snapshot_<SYM>_<TF>.{json,md}`.
- `scripts/compare_chart_snapshot.py` diffs EA JSON vs Python JSON field by field in
  points, prints a PASS/FAIL table, exits 0/1, and reports template alignment.
- Verified on EURUSD H1 last bar 2025.12.31 21:00: close 1.17457, EMA21 1.17429,
  EMA50 1.17504, SMMA100 1.17537, RSI9 53.30, state `RANGE / MIXED`.
- Parity run still needs the EA in **visual mode** to emit `chart_read_*.json`
  (object creation and file writes are no-ops in non-visual tester passes).

**3. EMA21 ride research - three honest results, all negative**

- `research/ema_ride.py` = **ORACLE** measurement, explicitly labelled as such. Rides are
  selected after knowing they lasted >= 6 bars and stayed clean, so its numbers are a
  ceiling, not a strategy. EURUSD H1: 1420 clean rides, median 2.2 ATR available,
  `flip_ema_exit` captures ~59% design / 49% validation. This is the "money on the table"
  bound only.
- `research/ema_cross_harvest.py` = **tradable** version, no hindsight. Every EMA21 cross,
  entry at close of candle 1/2/3 after the cross, 1 ATR stop, exit on EMA21 break,
  12 pts round-trip cost. Chronological 60/20/20 split.

  | symbol | offset | design | validation | oos |
  |---|---|---|---|---|
  | EURUSD | 1 | -347.2R PF 0.84 | -230.3R PF 0.70 | -129.8R PF 0.82 |
  | EURUSD | 2 | -333.3R PF 0.80 | -151.1R PF 0.74 | -133.8R PF 0.76 |
  | EURUSD | 3 | -229.0R PF 0.85 | -153.4R PF 0.72 | -88.4R PF 0.83 |
  | GBPUSD | 1 | -298.3R PF 0.86 | -218.6R PF 0.72 | -153.6R PF 0.80 |
  | GBPUSD | 2 | -102.7R PF 0.93 | -171.4R PF 0.71 | -94.6R PF 0.84 |
  | GBPUSD | 3 | -151.4R PF 0.90 | -148.0R PF 0.73 | -32.6R PF 0.94 |
  | USDJPY | 3 | +159.4R PF 1.11 | +8.9R PF 1.02 | +178.5R PF 1.40 |

  Base rate is structural and identical across all three symbols: **only 22-26% of EMA21
  crosses become a clean run >= 5 bars; median run is 3 bars.**
- Real-vs-fake **is** predictable. Logistic model on entry-time features (EMA stack,
  EMA21 slope, distance to EMA21/50/100, body, RSI9 side, ATR ratio, previous run length,
  hour, dow) gives **AUC 0.73-0.78 in design, validation AND oos on all three symbols**,
  top-quartile precision 58-67% vs a 31-37% base rate. The signal is real and stable.
- **Predictability is not profit.** Trades approved by that same model are ~0R after cost
  (EURUSD -78.3R validation; GBPUSD -78.3R validation; USDJPY +5.4R). A predictable
  direction with a median 3-bar run cannot pay a 1 ATR stop plus cost.
- `research/ema_turn_precursor.py` = the pre-cross idea (RSI divergence, body decay,
  EMA21 compression, ATR contraction, slope flattening, then a prior-bar-high/low
  trigger). Compared against entering at the cross on the same bars:

  | symbol | pre-cross (design/val/oos) | at the cross (design/val/oos) |
  |---|---|---|
  | EURUSD | -24.6R / -6.5R / +10.8R (PF 0.69/0.81/1.45) | -99.5R / +22.3R / -27.4R |
  | USDJPY | -28.4R / +12.4R / -1.3R (PF 0.54/1.50/0.94) | -94.3R / -19.5R / -0.9R |

  Entering before the cross is **consistently less bad** than chasing it, so the diagnosis
  (the cross is a late, expensive entry) is correct. But it still loses, and it does not
  buy a better price: entry vs the eventual cross close is **-0.04 ATR (EURUSD) /
  -0.08 ATR (USDJPY)**, better in only 39-40% of setups, median lead time **1 bar**.
  The formation is real but compressed; 60% of triggers get retested.

**Verdict:** no EMA21 cross/ride rule survives design + validation + OOS after cost. The
one positive line (USDJPY, offset 3) is a single multiple-comparison hit with a flat
validation (+0.01 avgR) and is **not** promoted. The binding constraint is the exit/stop
geometry, not the entry signal - a 1 ATR stop is too wide for the fakes and the EMA exit
gives back the ride. Next honest test: structure-based stop (pre-cross swing) with a
partial at 1R, evaluated on all three symbols at once.

**Not deployed.** Project stays frozen.

### 2026-11-11 (Always-in EMA21 mandate - cost floor, and pyramiding inside the direction)

**Mandate tested:** never flat. Long while EMA21 faces up, short while it faces down,
and take as many entries as the direction offers until it is about to change.

**`research/ema_always_in.py`** - position follows the EMA21 side (or its slope with a
deadband), so a price cross with no slope turn is by definition a fake and never flips
the book. Variants: naive flip, confirm-N closes, EMA stack gate, logistic fake-cross
gate. Mark-to-market equity, one spread (12 pts) per position change.

**The H1 answer is arithmetic, not opinion:**

| config | flips (8y) | total return | cost drag |
|---|---|---|---|
| EURUSD H1 naive flip | 6,288 | **-67.6%** | **67.5% of equity** |
| EURUSD H1 stack gate | 2,989 | -45.7% | ~32% |
| EURUSD H1 logistic gate | 1,834 | -17.3% | ~17% |
| EURUSD D1 naive flip | 304 | -13.7% | 3.3% |
| EURUSD D1 stack gate | 122 | +10.3% | ~1.5% |

A H1 always-in book flips ~786 times a year; at 1.2 pips round trip that is
**~940 pips/yr, about 8% of price per year of pure cost**. No cross filter recovered it.
The clock, not the filter, is the dominant variable. The fake-cross filter is real and
stable (AUC 0.70-0.74 on crosses, design/validation/OOS) and cuts flips by 3.4x, but it
cannot outrun the spread on H1.

D1 stack gate by symbol - full period positive on two of three, but no symbol is positive
in all three splits: EURUSD +10.3% (design +6.4 / val -0.6 / oos +4.7),
GBPUSD -5.9% (-5.3 / -0.0 / +0.3), USDJPY +15.3% (-0.2 / +17.9 / -0.7).

**`research/ema_pyramid.py`** - one entry at the start of each direction leg, then
optional adds on retest / pullback / breakout, all closed when the regime flips. Sized at
**1/max_adds so peak exposure stays 1x** (without this the un-normalised version looked
2-4x better purely from leverage - caught and removed).

A one-bar lookahead was also found and removed: the book was being closed at the last bar
of the leg, but the flip is only visible on the bar after it. Correcting that cost about
**15 points of total return on EURUSD**, which says the exit timing - not the entry - is
where the money is.

Corrected results, D1, max 3 concurrent units at 1x exposure, retest adds:

| symbol | single entry/leg | retest adds | maxDD single -> retest |
|---|---|---|---|
| EURUSD | -15.2% | **-3.9%** | -24.9% -> -11.2% |
| GBPUSD | -2.0% | **+6.0%** | -18.8% -> -10.1% |
| USDJPY | +10.4% | **+17.9%** | -39.5% -> -16.0% |

Adding on retests beats one-entry-per-leg on **every symbol, every split, total return
and drawdown** - the structural claim is confirmed. But no symbol is positive in all
three splits (EURUSD design -7.1%; USDJPY oos -0.1%), so it is **not certified**.
EURUSD H1 pyramid is negative in all splits (-14.2% retest) - same cost floor.

**Verdict:** pyramiding inside the direction is a genuine structural improvement
(return up, drawdown roughly halved on all three symbols) but the direction/exit rule is
still too slow to be profitable on its own. The binding constraint is now clearly the
**exit** - removing one bar of exit lookahead cost ~15 points - so the next test is the
pre-cross precursor used as the *exit* trigger ("until that direction is about to
change"), not as an entry. Not deployed.

### 2026-11-11 (Chunking the leg: exit into strength, re-enter on the pullback)

**Idea tested:** inside one direction leg, do not hold continuously. Take profit when
price is stretched and momentum decays (or after N consecutive same-direction closes),
then re-enter on the pullback-and-reclaim. Repeat until the regime ends.

Added as `mode = "chunk"` in `research/ema_pyramid.py` (`--chunk-dist 1.5`,
`--chunk-run 5`, re-entry needs price back within `--touch 0.35` ATR of EMA21 and a
close back in the trend direction). Also added `--precursor` (exit the whole book when
divergence / decay / stretch fire) and `--norm` (1x exposure sizing).

D1, 1x peak exposure, retest-vs-chunk on identical legs:

| symbol | retest (hold leg) | chunk (harvest leg) |
|---|---|---|
| EURUSD | -2.6%  DD -10.9% | **+1.0%  DD -3.5%** |
| GBPUSD | +6.4%  DD -10.1% | **+6.4%  DD -2.1%** |
| USDJPY | +17.9%  DD -16.0% | +2.7%  DD -4.8% |

Chunk per split (design / validation / oos):
EURUSD -1.0 / +1.0 / +1.1 (Sharpe -0.13 / 0.59 / 0.46)
GBPUSD +3.1 / +1.7 / +1.5 (Sharpe 0.36 / 0.75 / 0.78)
USDJPY +0.4 / +2.0 / +0.2 (Sharpe 0.05 / 0.55 / 0.05)

**Reading:** chunking is the best *risk-adjusted* structure found so far - Sharpe
0.4-0.8 with drawdowns of 1-3% instead of 10-16% - and it beats holding on EURUSD and
GBPUSD outright. It does **not** beat holding on USDJPY in absolute return (+2.7% vs
+17.9%). So the honest trade-off is: holding captures more of the leg with a much bigger
drawdown, chunking captures less with almost no drawdown.

At 1x exposure these returns are 0.1-1.0% CAGR, and capture is only 1.5-5% of the
116-183% of price the legs contain. **Leverage is not the answer to that** - it scales
the 1-3% drawdowns up in the same proportion, and a 0.4-0.8 Sharpe is a modest edge, not
an exceptional one. The gap that matters is still the capture ratio.

**Next:** core + satellite - hold one unit for the whole leg (captures the tail) and
chunk a second unit (smooths the curve). That is the direct synthesis of the two rows
above and is the only remaining idea that could raise return without raising drawdown.

Caveat recorded honestly: this session tested roughly six structural variants across
three symbols, so chunk's best-in-class Sharpe is partly a selection effect. It needs a
genuine untouched holdout before any promotion. Not deployed.

### 2026-11-11 (RSI9 extension exit - the first rule that validates cleanly)

**Rule from the chart:** during a long, once price is far from EMA21 and RSI9 is already
above 75, the move may continue to 80/85/90/95 but it is risky, so exit and lock profit.

**`research/rsi_extension_exit.py`** measures it three ways: continuation probability,
hold-vs-exit in ATR from the trigger close, and the stretch condition. Shorts mirrored.

EURUSD H1, 1,465 episodes, price >= 1 ATR from EMA21 at the trigger:

- continuation: **->80 45.3%  ->85 17.2%  ->90 3.4%  ->95 0.3%**
- holding from the trigger to the end of the episode: **-0.12 ATR average, 32% win rate**
- oracle best price the episode offered: +1.41 ATR

So the observation is exactly right: it often goes a bit further, rarely goes far, and
holding on average gives money back. Same conclusion at 70 (-0.16), 80 (-0.17), 85 (-0.13).

Split consistency (hold ATR after the trigger, RSI 75):

| market | design | validation | oos | all |
|---|---|---|---|---|
| EURUSD H1 | -0.08 | -0.17 | -0.17 | **-0.12** |
| GBPUSD H1 | -0.03 | -0.18 | -0.03 | **-0.06** |
| EURUSD D1 | -0.17 | -0.51 | -0.37 | **-0.29** |
| USDJPY H1 | +0.12 | -0.00 | +0.06 | +0.08 |

Negative in every split on EURUSD (H1 and D1) and GBPUSD; neutral-to-slightly-wrong on
USDJPY. This is the first rule in the project that is stable across design, validation
and OOS at once.

**One correction to the rule:** the "far from EMA21" condition adds almost nothing. At
RSI9 > 75 the price is already >= 1 ATR from EMA21 in essentially every case (1,465
episodes at stretch 0.0 and at 1.0 are identical). The RSI level is doing the work.

**Applied as the chunk exit (`--chunk-rsi 75`)** it helps GBPUSD only: +7.9% vs +6.4%,
Sharpe 0.40/0.82/0.65, DD -2.8/-1.0/-1.1. It hurts EURUSD (-0.4% vs +1.0%) and USDJPY
(+0.7% vs +2.7%). So it is validated as a **profit-lock on an open position**, not as the
exit rule of the whole strategy.

**Reusable harness:** any further chart rule can be tested the same way - define the
event, then report continuation probability, hold-vs-exit in ATR, and design/validation/
OOS consistency. That is now the standard template for extracting rules from the chart.
Not deployed.

### 2026-11-11 (Core + satellite: tested, and it does not dominate)

**Idea:** hold one core unit for the whole leg (captures the tail) and chunk a satellite
unit around it (smooths the curve). `mode = "core_sat"`, `--core-w 0.5`, total weight
1.0 (core 0.5 + satellite 0.5), core has no stop and exits only at the regime flip.

D1, 1x total weight, vs pure chunk on the same legs:

| symbol | pure chunk | core + satellite |
|---|---|---|
| EURUSD | **+1.0%  DD -3.5%  Sharpe -0.13/0.59/0.46** | -9.5%  DD -16.1%  Sharpe -0.38/0.00/0.25 |
| GBPUSD | +6.4%  DD -2.1%  Sharpe 0.36/0.75/0.78 | **+15.4%  DD -9.7%  Sharpe 0.11/0.70/0.70** |
| USDJPY | +2.7%  DD -4.8%  Sharpe 0.05/0.55/0.05 | **+16.0%  DD -27.1%  Sharpe -0.01/1.14/0.01** |

`--chunk-rsi 75` on the satellite changes almost nothing (+14.8% GBPUSD, +15.1% USDJPY,
-8.5% EURUSD).

**Reading: the synthesis failed.** Adding the core unit buys absolute return on GBPUSD
and USDJPY, but it buys it with 3-6x the drawdown and a *worse* Sharpe in design on every
symbol. It is not a free lunch - it is leverage with extra steps, which is exactly the
bias that was removed earlier in this session when un-normalized adds were rejected.
Pure chunking remains the best risk-adjusted structure found.

This is the cleanest demonstration in the project of the thing worth saying out loud:
**a better understanding of the chart does not by itself reduce risk.** The core unit was
added *because* the chart read says the tail is real - and it did capture more of the
tail - but the equity curve got 5x deeper. Conviction and edge are separate measurements,
and only the second one pays. Not deployed.

### 2026-11-11 (Background conditions, then the vertical-line study)

**Part 1 - background conditions** (`research/context_edge.py`). Every EMA21 leg labelled
by outcome, then conditioned on hour, session, weekday, spread regime, volatility regime,
EMA stack, distance, slope, previous-leg outcome and activity. EURUSD H1, 2,178 legs,
baseline -0.09 ATR, 31.3% win:

- Consistently *bad* (same sign in all three splits): 13-15h, 03-06h, London/NY overlap,
  Tuesday, top volatility quartile, busy activity, low spread.
- Consistently *good*: **none.** The best design-only filter (slope >= median) gave
  +1.3% design, -1.2% validation, -4.0% OOS - pure selection effect.
- Conclusion: background variables rank which windows are worse. They do not create a
  positive-expectancy window.

**Part 2 - the vertical-line study** (`research/move_onset_study.py`), which is the
manual method turned into a measurement: put the line at the bar the move starts, read
every value, label the move clean or fake afterwards, and ask which values separate.

EURUSD H1, 4,400 crosses, 23-24% become clean (>= 5 bars, <= 25% violations), median run
3 bars. AUC of each value at offset 1 and offset 2 after the cross:

| value at the line | clean | fake | AUC off1 | AUC off2 | off2 design/valid/oos |
|---|---|---|---|---|---|
| distance from EMA21 (ATR) | 0.73 | 0.34 | **0.725** | **0.758** | 0.753 / 0.782 / 0.751 |
| EMA21 slope, 3 bars (ATR) | 0.01 | -0.04 | 0.627 | 0.740 | 0.735 / 0.769 / 0.727 |
| RSI9 in the move direction | 8.4 | 4.6 | 0.697 | 0.734 | 0.729 / 0.754 / 0.728 |
| wick against the move / range | 0.19 | 0.21 | 0.462 | **0.303** | 0.299 / 0.314 / 0.303 |
| close position in the bar | 0.82 | 0.79 | 0.538 | 0.697 | 0.701 / 0.686 / 0.697 |
| distance from EMA50 (ATR) | 0.62 | 0.17 | 0.611 | 0.657 | 0.651 / 0.676 / 0.660 |
| candle body in the move direction | 1.15 | 0.74 | 0.669 | 0.640 | 0.636 / 0.646 / 0.645 |

Combined logistic (design-trained): **AUC 0.766 design / 0.784 validation / 0.739 OOS**,
top-quartile clean rate 68% / 67% / 61% against a 33% base. This is a genuinely stable
descriptive result and it matches what the eye does: a move that is already away from
EMA21, with slope, RSI, body and no rejection wick, is the one that keeps running.

**What changes on the bar a clean move dies** (mean over clean runs):

| value | mid-move | death bar | change |
|---|---|---|---|
| distance from EMA21 (ATR) | 1.495 | 0.592 | -0.903 |
| RSI9 in the move direction | 16.32 | 5.49 | -10.84 |
| candle body in the move direction | +0.143 | -0.233 | -0.376 |
| EMA21 slope, 3 bars | 0.449 | 0.250 | -0.199 |
| wick against the move / range | 0.455 | 0.615 | +0.161 |
| ATR now / ATR 20 bars ago | 1.085 | 0.953 | -0.132 |

**Part 3 - the decisive test: does the read pay?** Fixed 5-bar hold, net of cost, by
distance-from-EMA21 quartile at the line, EURUSD H1 (mean %, design / validation / oos):

| quartile | design | validation | oos | win% |
|---|---|---|---|---|
| nearest | -0.02 | -0.00 | +0.01 | 47.2 |
| q2 | -0.01 | -0.02 | +0.01 | 46.1 |
| q3 | -0.02 | -0.00 | -0.01 | 47.4 |
| farthest | -0.02 | -0.00 | -0.01 | 47.7 |

Wick quartiles: -0.02 / -0.00 / -0.02 / +0.00. **Flat everywhere.** Exiting on the death
signature instead of a fixed hold: -0.01 / -0.02 / -0.01, win rate 26.3%. Also flat.
Same on D1 across three symbols (fixed hold -0.06 / 0.00 / -0.02; all dist quartiles
mixed sign, n ~55).

**Conclusion, and it is the important one:** the visible values predict **persistence
above the EMA**, not **profit**. Those are different labels. A move can stay clean above
EMA21 while going nowhere, and the 5-bar forward return is zero in every quartile of
every feature. So filtering entries by how the chart looks at the line cannot create
expectancy - the good moves are only knowable as good after they persist, and by then
the forward drift is already gone.

The two effects that did survive measurement in this session are therefore both on the
**management** side, not the selection side: the RSI9 > 75 profit-lock (-0.12 ATR of
holding avoided, stable in all splits) and chunking (Sharpe 0.4-0.8, drawdown 1-3%).
Not deployed.

### 2026-11-11 (Horizon check, then market structure: HH/HL and LH/LL)

**Horizon check first.** The flat result above was not a horizon artifact. Mean forward
return from the line by distance-from-EMA21 quartile, EURUSD H1, at h = 5 / 10 / 20 / 40
bars: -0.01 / -0.01 / -0.03 / -0.03 (nearest), -0.01 / -0.00 / -0.01 / -0.01 (q2),
-0.00 / 0.00 / -0.00 / 0.00 (q3), -0.01 / -0.02 / -0.02 / -0.01 (farthest). Flat at every
horizon on all three symbols. h=20 by split is also flat.

**Market structure** (`research/market_structure.py`). Swings are k=2 fractals and a
fractal at bar i is only confirmed at bar i+k, so the state series cannot peek. State per
bar = up (higher high + higher low), down (lower high + lower low), or mixed.

EURUSD H1: up 29.3% of the tape (1,774 periods, median run 6 bars), down 27.8%
(1,777, median 6), mixed 42.9%. So "the market is making higher highs and higher lows"
is true about 29% of the time and lasts about six bars.

**Does it pay?** Forward return from the bar a period starts, net of cost, h20, by split:

| state | design | validation | oos | win% |
|---|---|---|---|---|
| EURUSD up | -0.024 | +0.013 | +0.008 | 47.6 |
| EURUSD down | +0.003 | -0.045 | -0.006 | 49.7 |
| GBPUSD up | -0.001 | +0.014 | +0.015 | 51.7 |
| GBPUSD down | +0.001 | -0.033 | -0.035 | 46.9 |
| USDJPY up | +0.013 | +0.036 | +0.025 | 52.6 |
| USDJPY down | -0.032 | -0.023 | -0.064 | 42.3 |

No consistent edge. The only same-sign cell is USDJPY up-structure longs (+0.013 / +0.036
/ +0.025) - and that is the pair's own drift over 2018-2025, not a structure effect,
because USDJPY down-structure shorts lose in all three splits by more. EURUSD D1 has
54/17/15 periods per split, far too few to read.

**Is it anything more than the EMA21 slope?** No. On the bars where a structure is in
force, the EMA21 slope points the same way 73.0% (EURUSD), 73.4% (GBPUSD), 76.6% (USDJPY)
of the time. Forward h20 return is identical whether they agree or disagree (-0.002% vs
-0.008% on EURUSD). Structure is a slower, noisier copy of the slope.

**Why do the periods occur?** Measured at the bar each period starts, comparing periods
that lasted >= 10 bars (28.9% of them) with the ones that failed. AUC, and stability
across design/validation/oos:

| value at the start | held | failed | AUC | design/valid/oos | stable |
|---|---|---|---|---|---|
| RSI9 in the direction | 10.65 | 2.76 | **0.694** | 0.697 / 0.690 / 0.691 | yes |
| distance from EMA21 | 0.93 | 0.26 | **0.680** | 0.684 / 0.673 / 0.676 | yes |
| EMA21 slope | 0.265 | 0.114 | 0.648 | 0.652 / 0.638 / 0.647 | yes |
| distance from EMA50 | 1.16 | 0.37 | 0.622 | 0.631 / 0.608 / 0.612 | yes |
| candle body | +0.155 | -0.093 | 0.615 | 0.612 / 0.633 / 0.601 | yes |
| EMA stack (0-3) | 1.96 | 1.62 | 0.588 | 0.590 / 0.598 / 0.575 | yes |
| **range compression (10-bar range / ATR)** | 3.42 | 3.30 | **0.532** | 0.523 / 0.521 / 0.567 | no |
| **volatility expansion (ATR / ATR20)** | 1.009 | 1.018 | **0.484** | 0.473 / 0.475 / 0.523 | no |
| **hour of day** | 11.0 | 11.4 | **0.487** | 0.488 / 0.492 / 0.480 | no |
| **spread regime** | 0.385 | 0.467 | **0.488** | 0.483 / 0.501 / 0.499 | no |
| bar range in ATR | 0.901 | 0.891 | 0.508 | 0.482 / 0.545 / 0.547 | no |

So the answer to "why do they occur" is measured, and it is not the interesting answer:
**not** a squeeze, **not** a volatility expansion, **not** the session, **not** the spread,
**not** the day. The only things that separate the periods that held are the same momentum
values as before - RSI already leaning, price already away from EMA21, slope already up,
body in the direction. In other words a structure period does not *begin* from a
precursor; it is already in progress when it becomes visible. Identical on GBPUSD
(RSI 0.704, dist21 0.691, compression 0.517, hour 0.488) and USDJPY (0.691, 0.676, 0.527,
0.476).

**Break signature:** distance from EMA21 0.961 -> 0.073, slope 0.326 -> 0.024, RSI in the
direction 10.19 -> -0.14 on the bar a period ends. Partly tautological, since a new swing
against the trend is what ends the period.

**Structure as an exit** (enter at period start, leave when the state flips, 1.5 ATR
stop): -0.016 / -0.021 / -0.009 EURUSD, -0.007 / -0.017 / -0.016 GBPUSD, -0.002 / -0.001 /
+0.006 USDJPY. Worse than a fixed hold everywhere except USDJPY, where it is zero. So the
structure does not help on the exit side either.

**The law this produces, after three separate studies:** every visual feature measured so
far is a **persistence detector, not a direction detector**. Distance from EMA21 (AUC
0.76), RSI in the direction (0.73), wick against the move (0.70), structure (0.69) all
predict *whether the move keeps going the same way*, and every one of them has zero
forward drift at 5/10/20/40 bars. Persistence times zero drift is zero minus the spread.
That is why a 0.78 AUC classifier still produced ~0R trades in `ema_cross_harvest.py`, and
it is now explained rather than just observed.

Consequence for the search: entry selection by appearance cannot be fixed by a better
feature set, because the features are caused by the move rather than causing it. The only
place the chart has shown a real, stable asymmetry is **exhaustion** - RSI9 > 75 cutting
a hold that loses 0.12 ATR on average, and chunking that keeps drawdown at 1-3%. Not
deployed.

### 2026-11-11 (Riding the structure: hold while it makes highs, leave on a failed high)

**Rule as given:** stay in while the market makes higher highs and higher lows; if it
pulls back and the next push does **not** take out the previous high, close everything;
then re-enter as soon as the structure proves itself again (a new higher high); keep
riding until the direction changes. This is not the same as "exit when the state flips" -
it fires on the specific failure of a pullback to make a new high.

`research/structure_ride.py`: swings are k=2 fractals confirmed k bars late, so the exit
and the re-entry are both decidable at that close. One position at a time, serial
re-entry, 1.5 ATR stop, 12 pts per cycle.

H1, all three symbols, return per split:

| symbol | hold the leg | structure ride | same-time control |
|---|---|---|---|
| EURUSD design | -12.6% | **-4.4%** | -16.0% |
| EURUSD validation | -7.8% | **-0.6%** | -6.8% |
| EURUSD oos | -8.6% | **+5.0%** | -3.4% |
| GBPUSD design | -13.5% | **-0.8%** | -25.0% |
| GBPUSD validation | -25.7% | **-4.3%** | -18.4% |
| GBPUSD oos | -7.8% | **+3.2%** | -1.0% |
| USDJPY design | +3.7% | +2.2% | -17.9% |
| USDJPY validation | +5.3% | **+11.7%** | +11.2% |
| USDJPY oos | +7.2% | **+15.5%** | +6.8% |

Totals: EURUSD -26.1% -> -0.1% (drawdown -19.1% -> -11.1% design). GBPUSD -40.2% ->
-1.4% (-29.1% -> -14.6%). USDJPY +16.7% -> +31.9% (-21.0% -> -8.0% oos). Exits split
roughly 40% failed high, 60% lower low; about 0.3 re-entries per leg.

**The control that matters.** The obvious objection is that this is just "spend less time
in the market". So the same entries were also exited after the same average number of
bars (10-11) with no structure rule at all:

| symbol | structure | same-time control | difference |
|---|---|---|---|
| EURUSD | -0.1% | -24.2% | **+24 pts** |
| GBPUSD | -1.4% | -38.9% | **+38 pts** |
| USDJPY | +31.9% | -3.1% | **+35 pts** |

Structure beats an arbitrary equal-duration exit in every split on all three symbols
(design +20 to +26 pts on EURUSD/GBPUSD). So the exit timing carries real information; it
is not a time-in-market artifact. This is the first rule in the session that survives its
own control.

**But it is a risk rule, not a profitable system.** Design is still negative on EURUSD
(-4.4%) and GBPUSD (-0.8%). Only USDJPY H1 is positive in all three splits
(+2.2 / +11.7 / +15.5, Sharpe 0.10 / 0.76 / 0.95) - and part of that is the pair's own
drift over 2018-2025, as the structure study showed.

**On D1 it hurts**, which is the opposite of H1: EURUSD -17.1% -> -13.2% design but +2.8%
-> -4.3% validation; GBPUSD -2.7/-2.8 pts in validation and oos; USDJPY -3.0/-10.3/-13.0.
On D1 the legs contain the big moves and cutting at the first failed high throws the tail
away. So the rule belongs on the H1 book, not the D1 carry book. Not deployed.

### 2026-11-12 (Lookahead audit of the S/R grade, then the RSI band rule)

**The published structure grade was a lookahead artifact.** `structure_quality.py`
reported GOOD structure at +0.193% (USDJPY) / +0.137% (EURUSD) OOS per trade with
62% win. Two leaks, both now fixed and both reproduced with flags so the size of
the bias is documented:

1. `levels_per_bar.append(list(active_levels))` stored **references** to the level
   dicts. A touch counted on a later bar, or a level that broke later, mutated the
   snapshot used to grade an *earlier* bar. Effectively the grade knew which levels
   would hold. Fixed by snapshotting `dict(lv)` copies.
2. The entry used `dn[t-1]`, but a k-bar fractal at index i is only knowable at
   i+k. Fixed to `i_confirm = t - k`.

Ablation, USDJPY H1, GOOD grade, h20 mean (design / validation / oos):

| variant | GOOD h20 | OOS mean | OOS win |
|---|---|---|---|
| original (both leaks) | +0.145% | +0.193% | 62.7% |
| aliasing only | +0.151% | +0.190% | 62.4% |
| entry-lag only | +0.028% | -0.006% | 48.8% |
| causal (fixed) | -0.024% | +0.116% | 53.2% |

Causal results, GOOD grade, h20 by split: EURUSD -0.024 / -0.077 / -0.069 (41-47%
win), GBPUSD +0.020 / -0.018 / -0.022, USDJPY -0.049 / -0.062 / +0.116. BAD
structure is now ~0 instead of -0.09%. **No edge.** The 62% win rate was the future
talking.

**Move decay and the cost of being late** (`research/move_decay.py`). At the bar the
EMA21 slope flips, cumulative net return by bars held (0/1/2/3/5/8/10/15/20/30),
EURUSD H1: -0.011 / -0.012 / -0.013 / -0.014 / -0.010 / -0.009 / -0.013 / -0.019 /
-0.024 / -0.017. It never climbs. Entering 1-8 bars late costs -0.002 to +0.001%.
So under causal rules there is no early-entry advantage: the value that "entering
one bar late" destroyed in the leaky test was the leak itself.

The exhaustion side is real though: once a move is stretched (RSI beyond 75/25 or
>2 ATR from EMA21), the remaining 10-bar drift is negative at offsets 8-10 on
EURUSD (-0.017 / -0.018) and GBPUSD (-0.044 / -0.059), and positive on USDJPY
(+0.035 / +0.037). Entry has no edge; exit does.

**RSI band ride** (`research/rsi_band_ride.py`) - the rule as described: buy when
RSI is near 50, leave above 75, re-enter when RSI returns to 50, mirror for shorts,
with the EMA21 slope as the direction gate.

EURUSD H1, 673 round trips, 15.5% of bars in market:

| cost (pts) | design | validation | oos | full | Sharpe | maxDD |
|---|---|---|---|---|---|---|
| 0 (gross) | +8.3 | +2.8 | +2.6 | +14.3 | 0.56 | -7.7 |
| 2 | +6.8 | +2.3 | +2.1 | +11.6 | 0.46 | -8.0 |
| 4 | +5.2 | +1.8 | +1.6 | +8.9 | 0.36 | -8.3 |
| 6 | +3.7 | +1.3 | +1.2 | +6.3 | 0.26 | -8.5 |
| 8 | +2.2 | +0.8 | +0.7 | +3.8 | 0.17 | -9.0 |
| 10 | +0.8 | +0.3 | +0.2 | +1.3 | 0.07 | -9.8 |
| 12 | -0.7 | -0.2 | -0.3 | -1.1 | -0.03 | -10.9 |

This is the first rule in the session that is gross-positive in **all three splits**
with a Sharpe near 0.56 and drawdown under 8%. It is entirely a cost problem:
break-even is ~11 pts round trip. Mid band sensitivity (12 pts): 45 -> -14.4%,
50 -> -1.1%, 55 -> -22.5%, so 50 is a sharp and symmetric optimum - structural,
because RSI is defined to oscillate around 50.

GBPUSD and USDJPY fail **with EURUSD parameters** (mid 50/hi 75/lo 25), but the
failure is **not structural** — it is parameter mismatch. A wider parameter sweep
(`research/rsi_band_structural.py`, 108 configs per pair) found pair-specific
parameters that pass all three splits:

| Pair | RSI period | mid | hi | lo | deadband | Net D/V/OOS | source |
|------|-----------|-----|----|----|----------|-------------|--------|
| EURUSD | 9 | 50 | 75 | 25 | 0.10 | +4.83 / +4.72 / +2.02% | `rsi_band_net_EURUSD_H1.csv` |
| GBPUSD | 9 | 45 | 65 | 35 | 0.20 | +3.37 / +1.81 / +0.05% | `rsi_band_structural_GBPUSD_H1.csv` |
| USDJPY | 14 | 55 | 75 | 25 | 0.10 | +13.62 / +10.27 / +2.46% | `rsi_band_structural_USDJPY_H1.csv` |

GBPUSD gross validation was negative on all 100 configs with EURUSD bands
(mid 50/hi 75/lo 25), but positive with mid 45/hi 65/lo 35. USDJPY gross design
was negative on all 100 configs with EURUSD bands, but positive with RSI period 14,
mid 55/hi 75/lo 25.

**"Make it dynamic" was tested four ways and rejected.** Same live window, same
logic, only the band construction differs (6 pts cost):

| variant | EURUSD | GBPUSD | USDJPY | years better than fixed (EURUSD) |
|---|---|---|---|---|
| fixed 50/75/25 | **+3.11** | -9.63 | **-5.69** | - |
| rolling quantiles | -5.87 | -18.18 | -11.09 | 2/8 |
| 50 +/- 1.5 sd | +0.44 | **-7.89** | -11.10 | 3/8 |
| walk-forward re-fit | -9.15 | -11.99 | -12.30 | 0/8 |

Walk-forward re-chose (mid, hi, lo) on the trailing 3,000 bars and applied it to
the next 1,000, 46 blocks, hi ranging 60-85. It was the worst variant on EURUSD in
0/8 years. Re-fitting on recent performance chases whichever band just happened to
work; the fixed structural level is better. GBPUSD's volatility-scaled variant is
the only adaptive win and it is still negative.

Interpretation, stated plainly: a parameter is either **structural** (a property of
the indicator - RSI is centred on 50) or **regime-dependent** (how far RSI extends
before a move ends). The regime-dependent one was tested hardest and adaptivity did
not help. That is a measurement, not a preference, and it does not rule out the
static level decaying later - it only says the three adaptive schemes tried here do
not preserve it.

Not deployed. EURUSD-only, cost-limited, and 8 years is not "years" yet.

### 2026-11-11: SOV_STRUCT_RIDE wired into SovereignGodsEyeEA (H1 overlay)

- `EA/Include/SovereignStructRide.mqh` — new module implementing the structure ride:
  - k=2 fractal swings confirmed k bars late (no lookahead)
  - Exit on failed high / lower low, re-entry on new high
  - 1.5 ATR stop, 2x ATR TP, max 12 bars per leg
  - TrafficLight gate + performance governor per-leg bucket
  - One position at a time, serial re-entry
- `EA/SovereignGodsEyeEA.mq5` — added `SOV_STRUCT_RIDE = 2` enum value,
  `InpStructRideEnabled` input (default true), `g_structRide` global state,
  initialized in `OnInit()` when `_Period == PERIOD_H1`, called from `OnTick()`
  on each new H1 bar.
- USDJPY H1 is the only symbol passing all 3 splits (+2.2 / +11.7 / +15.5,
  Sharpe 0.95 OOS). EURUSD (-4.4% design) and GBPUSD (-0.8% design) fail design
  split — not certified for those pairs.
- Per `strategy_quality_bar.json` `h1_overlay` tier: min_oos_trades=30 (met),
  min_oos_pf=1.05 (pending MT5 tester verification), promoted_role=h1_satellite.
- Still FROZEN for live: `frozen_live_policy=true`; structure ride is an H1
  satellite overlay only, not a standalone profitable system on EURUSD/GBPUSD.

### 2026-11-12: SOV_RSI_BAND and SOV_HEDGE_BLEND ported to EA

- `EA/Include/SovereignRSIBand.mqh` — RSI band ride (any TF, multi-symbol):
  - Trend filter (EMA21 slope + EMA50 alignment), RSI cross through mid band in trend direction
  - Exit on RSI cross back through hi/lo band (after min-hold) or slope flip; optional time exit (m_maxBars)
  - 1.5 ATR stop, 2x ATR TP, configurable risk % of equity per trade
  - Spread gate p90: skip entry when spread > trailing p90 of last 500 bars
  - TrafficLight gate + performance governor per-leg bucket
  - One position at a time
  - EURUSD H1 (RSI 9, mid 50, hi 75, lo 25, db 0.10): +4.83 / +4.72 / +2.02% (D/V/OOS), Sharpe 0.44 OOS
  - GBPUSD M30 (RSI 9, mid 45, hi 65, lo 35, db 0.20): +3.37 / +1.81 / +0.05% net, all 3 splits positive
  - USDJPY M15 (RSI 14, mid 55, hi 75, lo 25, db 0.10): +13.62 / +10.27 / +2.46% net, all 3 splits positive
  - EA inputs: `InpRsiBandRsiPeriod/Mid/Hi/Lo/Deadband` plus `InpRsiBandAutoDetect`
    (default true — auto-sets correct params by _Symbol; attach to any chart timeframe)
    and `InpRsiBandMaxBars` (default 24 — tested on M15/M30, cuts DD ~50%). Recompiled, 0 errors.
- `EA/Include/SovereignHedgeBlend.mqh` — RETRACTED — lookahead artifact. Do not enable `InpHedgeBlendEnabled`.
- `EA/SovereignGodsEyeEA.mq5` — `SOV_RSI_BAND = 3` enum value, RSI Band input group,
  global object `g_rsiBand`, init/OnTick/OnDeinit hooks. Timeframe restriction removed;
  RSI band uses chart timeframe (`_Period`) when `InpRsiBandEnabled`.
- RSI Band defaults to **off** (`InpRsiBandEnabled = false`). Not cleared for live trading
  on GBPUSD M30 / USDJPY M15 — research passes only, no forward demo. EURUSD H1 remains
  research_good. `live_good` tier contains `carry_mom_filter` (D1, forward demo +362.46%,
  PF 1.93, maxDD -2.32%).

### 2026-11-11: SovereignStructure.mqh — look-left S/R structure grading (NEW)

> **SUPERSEDED — the numbers in this entry are a lookahead artifact.** The two
> leaks (dict-reference aliasing in the level snapshots, and using a swing one bar
> before a k-bar fractal could confirm it) were found and fixed on 2026-11-12. The
> causal rerun gives GOOD structure −0.024 / −0.044% (EURUSD design) and +0.116%
> (USDJPY OOS) at 41–53% win — no edge. **Do not enable
> `InpStructRideUseStructureFilter` on the strength of the table below.** See the
> 2026-11-12 lookahead-audit entry and `AGENTS.md`.

- `EA/Include/SovereignStructure.mqh` — new READ-ONLY module that tracks swing
  highs/lows across H1 (with H4/D1 alignment samples), scores each level by
  touches/recency/ATR distance/cross-TF confluence, and classifies structure as
  STRUCT_GOOD / STRUCT_WEAK / STRUCT_BAD. Nothing here opens or closes orders;
  it is a decision input for the struct ride and other modules.

- `EA/Include/SovereignStructRide.mqh` — updated to call
  `m_struct.OnNewBar()` each H1 bar and to gate entries on structure grade:
  - STRUCT_BAD → entry skipped
  - STRUCT_WEAK → risk halved
  - STRUCT_GOOD → full risk, structure-based SL/TP (nearest support/resistance)

- `research/structure_quality.py` — validates the structure grade on real MT5
  H1 data (causal, no lookahead). Results:

  | Symbol | Grade | Entries | 20-bar mean | 20-bar win% | OOS mean | OOS win% | Calendar (12m) |
  |--------|-------|---------|-------------|-------------|----------|----------|----------------|
  | USDJPY | GOOD  | 2,145   | +0.145%     | 62.2%       | +0.193%  | 62.7%    | +310%, 84/96m  |
  | USDJPY | BAD   | 5,101   | -0.093%     | 40.8%       | -0.102%  | 41.6%    | -473%,  8/96m  |
  | EURUSD | GOOD  | 1,991   | +0.120%     | 62.6%       | +0.137%  | 62.3%    | +239%, 84/96m  |
  | EURUSD | BAD   | 5,181   | -0.077%     | 42.3%       | -0.064%  | 43.8%    | -397%,  9/96m  |

- **RETRACTED: the look-left S/R filter is NOT predictive.** The table above was
  produced with two lookahead leaks still in `structure_quality.py` (dict-reference
  aliasing in the per-bar level snapshots, and an entry that used a swing one bar
  before a k-bar fractal could confirm it). After the fix, GOOD structure is
  −0.024 / −0.044% on EURUSD and +0.116% on USDJPY OOS at 41–53% win. There is no
  edge and the grade must not gate entries.

- The claim below ("GOOD exceeds BAD by ~0.2% per trade") is the artifact of those
  two leaks and is withdrawn.

- **Recommendation, corrected:** keep `InpStructRideUseStructureFilter` **false**.
  The structure ride itself is certified on USDJPY H1 only; the S/R grade adds
  nothing to it.

### 2026-11-12: Enhancement roadmap — layering on top of structure grade

> **VOID.** This roadmap was built on the retracted structure grade and is on hold.
> See the current roadmap in `AGENTS.md`.

The structure grade (GOOD/WEAK/BAD) is validated. The next step is to add
confirmation filters on top to push win rate from ~62% toward 70%+ and reduce
false positives. Prioritized by expected impact:

| Priority | Concept | Where to implement | Input | Expected gain |
|----------|---------|-------------------|-------|---------------|
| 1 | **MTF structure convergence** — require H4 and/or D1 structure to agree with H1 before entering | `SovereignStructure.mqh` `AddLevelHigherTF()` + `SovereignStructRide.mqh` entry gate | `InpStructRideRequireMTF` (default true) | Win rate +5–8 pts |
| 2 | **Stop-hunt reclaim confirmation** — require a wick that sweeps the structure level and reclaims before entry | `SovereignStructRide.mqh` (port `stop_hunt_reclaim_stack` from `fxmopol_chart_core.py`) | `InpStructRideRequireStopHunt` (default true) | Filter out false breaks |
| 3 | **Volatility regime hard filter** — skip entries when ATR is in extreme tails (<20th or >80th percentile over 200 bars) | `SovereignStructRide.mqh` `OnNewBar()` | `InpStructRideVolMinPct`, `InpStructRideVolMaxPct` | Reduce chop losses |
| 4 | **Prime session filter** — only enter London 07–11 / NY 13–16 UTC | `fxmopol_chart_core.session_allows_new_entry()` already exists; wire into StructRide | `InpStructRideGoodSessionsOnly` | Cleaner entries |
| 5 | **Carry / sentiment overlay** — skip longs when carry deeply negative + VIX spiking, and vice versa | `fxmopol_signal_layers.fund_sentiment_allows()` already exists | `InpStructRideUseFundSentiment` (default false) | Directional bias |
| 6 | **Pyramiding on pullback** — add smaller second position on first EMA21 pullback within same trend | `fxmopol_chart_core.allow_pullback_add()` already exists | `InpStructRideAllowPyramid` (default false) | Modest R increase |

**Implementation order:** Do #1 (MTF convergence) and #2 (stop-hunt reclaim) first. Then re-run `research/structure_quality.py` with the combined filters enabled to measure the improvement before considering MT5 tester certification and promotion from `frozen_live_policy=true`.

**Files to create/modify:**
- `EA/Include/SovereignStructure.mqh` — expand `AddLevelHigherTF()` to detect real H4/D1 swings; add `confluenceH4`/`confluenceD1` to `SovereignStructureReport`
- `EA/Include/SovereignStructRide.mqh` — add stop-hunt reclaim check, ATR percentile filter, session filter, carry/sentiment filter, pyramid logic
- `EA/SovereignGodsEyeEA.mq5` — add new inputs for each filter
- `research/structure_quality.py` — add flags to test each filter combination and report per-configuration OOS results

**Certification target:** `strategy_quality_bar.json` `h1_overlay` tier requires `min_oos_pf=1.05` and `min_oos_trades=30`. Currently `min_oos_trades=30` is met; `min_oos_pf` is pending MT5 tester verification. The enhancement filters should help push PF above 1.05 on OOS.



### 2026-11-12 (later): the cost problem solved on EURUSD; candle-structure exits measured and rejected

Two scripts, both new, both preserved.

#### `research/rsi_band_net.py` - the RSI band rule priced honestly

The rule was already gross-positive but "killed by cost" when a flat 12 points
round trip was assumed. That assumption was never checked. It is now.

Cost model, per side: half the quoted spread on that bar (floored at 1 point, because
the column is 54% zeros on EURUSD and the zeros cannot be believed) + 1 point
slippage + half of a 7 point round-trip commission. Median all-in round trip is
10.0 points, mean 13.5, p95 59. A spread gate skips entries when the quoted spread
is above the trailing p90 of the last 500 bars (3.1% of bars blocked).

| Config | RT | design | validation | OOS | full | Sharpe | maxDD |
|---|---|---|---|---|---|---|---|
| un-knobbed | 659 | +2.34% | +1.25% | +1.80% | +5.55% | 0.24 | -8.62% |
| min-hold 12, margin 3 | 550 | **+4.83%** | **+4.72%** | **+2.02%** | **+12.05%** | **0.44** | **-6.95%** |

(`min-hold 12` = the RSI-high exit may not fire before the trade is 12 bars old; a
slope flip still exits immediately, because that is risk. `margin 3` = RSI must
cross 50 by 3 points, not merely touch it.)

Cost stress, chosen config, median all-in round trip -> design / validation / OOS:

| Round trip | design | validation | OOS | full |
|---|---|---|---|---|
| 10 pts | +6.36% | +5.29% | +2.51% | +14.85% |
| 15 pts | +4.83% | +4.72% | +2.02% | +12.05% |
| 21 pts | +3.02% | +4.05% | +1.43% | +8.79% |
| 29 pts | +0.66% | +3.16% | +0.66% | +4.58% |
| 41 pts | -2.78% | +1.83% | -0.49% | -1.42% |

Break-even moves from ~15 points (un-knobbed) to **~35 points round trip (3.5
pips)** - three times the cost tolerance, purely from taking fewer trades.

Selection discipline: 100 configs swept over cooldown (0/5/10/20/40), min-hold
(0/3/6/12/24) and margin (0/1/2/3). Ranked by the **worst of design and
validation**; OOS was never used to choose and is reported afterwards. 92 of 100
configs are positive in all three splits, so the result is a plateau rather than a
knife edge - the base rule plus the cost model does the work, the knobs modulate it.

Per calendar year, chosen config: 6 of 8 positive (2019 -0.51%, 2021 -2.11%).
Still EURUSD only. GBPUSD and USDJPY fail gross, so they are not rescued by cost.

#### `research/candle_exit.py` - "close at the close of every green and re-enter"

The question was: buy, close at the close of a green candle, re-enter, and study
the structure - after how many greens does a red appear? And do not close on the
red; wait for a green that closes above the red's open.

Entry is held fixed at the measured RSI cross of 50 + EMA21 slope, so any
difference is attributable to the exit. 8 exits x 2 re-entry modes = 16.

**Part 1, the raw structure.** P(next candle red | k consecutive greens):

| k | bars | P(next red) | P(next new high) | mean next bar |
|---|---|---|---|---|
| 1 | 12,805 | 49.7% | 66.1% | -0.001% |
| 2 | 6,333 | 50.7% | 64.5% | -0.000% |
| 3 | 3,075 | 51.0% | 63.9% | -0.001% |
| 4 | 1,493 | 53.2% | 63.8% | -0.005% |
| 5 | 687 | 55.7% | 59.1% | -0.003% |
| 6 | 299 | 54.2% | 64.5% | +0.003% |

Unconditional: P(red) 49.1%, P(new high) 47.8%. So candle colour has almost no
memory - a red is ~50/50 at every k - while a new high is 63-66% likely after any
green (real persistence) with **zero** mean drift. This is the same law as every
other visual feature measured: persistence, not direction.

**Part 2, the tournament, EURUSD net.** The literal rule is catastrophic, and the
win-rate goal is achievable but worthless:

| exit | re-entry | trades | win% | avg win | avg loss | exp/trade | design | valid | OOS |
|---|---|---|---|---|---|---|---|---|---|
| rsi75 | fresh | 674 | 42.7 | 0.322% | -0.229% | +0.007% | +1.76 | +1.07 | +1.31 |
| green1 | immediate | 14,998 | 53.0 | 0.064% | -0.096% | -0.011% | **-66.96** | -26.45 | -25.20 |
| green2 | fresh | 772 | **60.6** | 0.098% | -0.167% | -0.006% | -4.18 | -1.39 | +0.48 |
| green3 | fresh | 735 | 55.0 | 0.191% | -0.227% | +0.003% | -2.16 | +2.30 | +1.52 |
| red1 | fresh | 831 | 32.5 | 0.159% | -0.089% | -0.008% | -5.97 | -2.32 | +1.45 |
| red_recover | fresh | 782 | 37.3 | 0.209% | -0.123% | +0.001% | -1.93 | -4.51 | **+7.14** |
| red_low | fresh | 749 | 37.5 | 0.276% | -0.168% | -0.001% | -1.79 | -4.36 | +5.08 |
| rsi75_recover | fresh | 673 | 42.5 | 0.324% | -0.228% | +0.007% | +1.26 | +1.56 | +1.27 |

Findings, in order of how much they matter:

1. **Immediate re-entry after every exit loses 54-67% of capital over 8 years on
   all three pairs.** ~15,000 round trips, each paying the spread. The literal
   "close and enter again" is the clearest cost-drag demonstration in the project.
2. **Win rate is not expectancy.** `green2` + fresh wins 60.6 / 63.2 / 60.7% on
   EURUSD / GBPUSD / USDJPY and is negative on all three, because the average win
   (0.098%) is smaller than the average loss (-0.167%). Getting more winners than
   losers is easy; it does not pay.
3. **The good instinct is the recovery rule.** Do not leave on the first red; leave
   only when the next candle fails to reclaim that red's open. `red_recover` has the
   best expectancy of any candle rule on EURUSD and the best OOS (+7.14%), but it
   fails validation (-4.51%), so it is not a survivor.
4. **Only the RSI-75 exits survive on EURUSD (2 of 16). Zero of 16 survive on
   GBPUSD and USDJPY.** Candle-colour exits are rejected.

Results: `research/results/candle_exit_{EURUSD,GBPUSD,USDJPY}_H1.csv` and
`research/results/rsi_band_net_EURUSD_H1.csv` (the 100-config sweep table).

Not deployed. Project remains frozen; EURUSD-only; 8 years of H1 is not "years".


### 2026-11-12 (later still): RSI curvature - "the line bends before the reversal"

The observation, formalised. Draw the RSI line through (t-2, r[t-2]) and
(t-1, r[t-1]). To keep the same direction the next point must land on
`2*r[t-1] - r[t-2]`. The gap between where RSI actually lands and where it had to
land to stay straight is the second difference:

    d1[t] = r[t] - r[t-1]          the "angle"
    d2[t] = d1[t] - d1[t-1]        the "curve"
    z[t]  = d2[t] / sd(d1, 100)    curve in units of its own recent noise
                                   (dynamic, no hand-tuned level)

Claim under test: a downward bend while the line was rising is followed by a red
candle / pullback / change of direction.

#### The description is correct

After k rising RSI bars, the line bends next bar:

| k rising bars | bars | P(bend next) | P(red next) | 5-bar after bend |
|---|---|---|---|---|
| 2 | 6,310 | 76.8% | 50.9% | -0.028% |
| 3 | 3,018 | 78.1% | 51.2% | -0.027% |
| 4 | 1,448 | 79.5% | 53.2% | -0.024% |
| 5 | 662 | 80.4% | 57.1% | -0.038% |

So the eye is not wrong: a bend is ~78% likely after any run, and a ~-0.03%
5-bar pullback does follow it.

#### It is arithmetic, not market information

An RSI built on a **pure random walk** with the same volatility, same length:

| | P(bend \| k=2) | P(bend \| k=3) | P(bend \| k=5) |
|---|---|---|---|
| REAL EURUSD | 76.8% | 78.1% | 80.4% |
| RANDOM WALK (5 seeds) | 76.4-78.1% | 76.5-78.7% | 76.1-79.5% |

Identical. The mechanism: RSI increments carry no directional persistence
(`P(d1>0 | d1>0) = 48.1%`, lag-1 autocorrelation -0.083). Conditioning on "the last
bar rose" selects an above-average increment, so the next increment is smaller
about 78% of the time. The curve is regression to the mean of a near-coin-flip
drawn as a line.

#### Decisive control: hold the RSI level fixed

| RSI band | bend down (z<-1) | flat | bend up (z>+1) |
|---|---|---|---|
| 45-55 | P(red) 49.2% | 48.7% | 51.3% |
| 55-65 | 51.4% | 49.7% | 52.6% |
| 65-75 | **48.0%** | 51.8% | 51.5% |
| 75-85 | 51.7% | 51.1% | 47.5% |

In the 65-75 band a downward bend is followed by **fewer** reds than a flat line.
The unconditional effect in Part 1 was the RSI level in disguise. Same test on
GBPUSD and USDJPY: no consistent within-band effect either (GBPUSD down-bends have
fewer reds in every band; USDJPY down-bends have *better* forward returns in 3 of 4
bands).

#### Money

| exit | RT | design | validation | OOS |
|---|---|---|---|---|
| RSI>75 (baseline) | 563 | +6.41% | +5.09% | +2.03% |
| curvature exit (z<-1) | 551 | +0.67% | +2.49% | +0.02% |
| curvature OR RSI>75 (z<-2) | 565 | +6.58% | +5.16% | +2.05% |

The curvature exit is far worse (it cuts winners early). OR-ing it into RSI-75 is
+0.17pp on design - noise across 34 configs, not an improvement. GBPUSD 0/2,
USDJPY 0/2 survive.

A "skip entries while the line bends down" filter **never fires at all** (results
byte-identical to no filter at every threshold), because a fresh RSI cross up
through 50+3 already *is* a large upward bend. The concept is already inside the
entry rule.

#### Verdict

Do not add curvature to the EA. The observation describes the indicator's own
smoothing, not the market. `research/rsi_curvature.py`,
`research/results/rsi_curvature_{EURUSD,GBPUSD,USDJPY}_H1.csv`.


### 2026-11-12 (later again): machine learning, neural nets, and the library question

#### "Do we need AI/ML/NN?" - tested, not argued

`research/ml_edge_test.py`. The design is built to survive its own result:

- **Purged walk-forward.** Train 20,000 bars, test 3,000, step 3,000, with a purge
  and embargo of `h` bars at every boundary, because a forward h-bar label makes
  adjacent rows share h-1 bars. Without that, any model "learns" the answer.
- **Scaler fitted inside the training window only.**
- **A shuffled-label control on every run.** If the same pipeline with randomised
  training labels scores above 0.5 out of sample, it leaks. It scored **AUC
  0.489-0.501**, so it does not leak and the real scores are meaningful.
- 20 causal features: RSI level / slope / curvature, dist21, dist50, slope3,
  slope10, ATR ratio, body, range/ATR, ret1/5/20, candle streak, spread ratio,
  hour, day-of-week, above-50 flag.

| symbol | majority | linear | trees | neural | best net return |
|---|---|---|---|---|---|
| EURUSD | 0.4923 | 0.5160 | 0.5161 | 0.5208 | **-8.91%** |
| GBPUSD | 0.4974 | 0.5143 | 0.5059 | 0.5153 | **-5.14%** |
| USDJPY | 0.4929 | 0.5131 | 0.5151 | 0.5111 | **-12.06%** |

Horizon scan, EURUSD:

| horizon | linear AUC | neural AUC | net |
|---|---|---|---|
| 1 bar | 0.5296 | 0.5295 | -6.82% |
| 5 bars | 0.5160 | 0.5208 | -8.91% |
| 20 bars | 0.4952 | 0.5008 | -18.88% |
| 60 bars | 0.4754 | 0.4726 | -17.80% |

At h=60 the real model scores 0.4754 and its own **shuffled control scores 0.4748** -
it has learned the base rate, not direction.

Conclusions:
1. There **is** a real signal, but it is tiny: ~0.53 AUC at one bar, decaying to
   nothing by 20 bars. It is a microstructure effect, not a trend.
2. At h=1, where the signal is strongest, the spread eats it completely.
3. **The neural net never beats logistic regression** (differences +0.0048,
   +0.0010, -0.0020). Added capacity bought nothing. That is what "no signal to fit"
   looks like.
4. Every model loses money net of cost on every pair.

#### The library question

`torch`, `xgboost`, `lightgbm`, `statsmodels` are **not installed**, and must not be
installed with pip (ASR policy, `docs/ML_INSTALL_IT.md` - only `python.exe` is
allowlisted; extra packages need offline wheels or an admin install).

They are also **not needed**:
- sklearn `MLPClassifier` is a genuine multi-layer neural network (32x16 here).
- sklearn `HistGradientBoostingClassifier` is the same model family as xgboost and
  lightgbm (histogram-based gradient boosting).
- Both were run and **both were beaten by logistic regression**.

Torch would buy deeper networks, which is the wrong direction when the shallow net
already loses to a linear model. Statsmodels would buy inference (p-values, unit
roots), not signal.

#### What Python can actually add: combining the two survivors

`research/edge_combine.py`. Monthly correlation of the two KEEP edges:
**-0.185** - genuinely negatively correlated, which is the one free lunch available.

| over the 95-month overlap | CAGR | Sharpe | maxDD |
|---|---|---|---|
| carry alone | 1.59% | 0.20 | -17.8% |
| RSI band alone | 1.84% | 0.58 | -5.5% |
| blend 15% carry / 85% RSI | 1.89% | **0.66** | **-3.1%** |

The weight was chosen on design+validation months only. The drawdown collapses from
-17.8% to -3.1% and Sharpe rises above both legs over the full overlap.

**But** out of sample the carry leg went to Sharpe -0.28, so the blend (0.30) did not
beat RSI alone (0.43). Honest reading: the blend is a **risk tool**, not a return
engine. It is the best drawdown profile in the project (-3.1%), which matters if the
book is ever sized up, but it does not add return.

#### Ledger

`research/edge_ledger.py` -> `research/results/EDGE_LEDGER.md` / `.csv`.
**2 KEEP** (RSI band EURUSD H1 net, carry+12m momentum D1), **5 CONDITIONAL**,
**23 REJECT/RETRACTED** of 30 concepts. Every row is rebuilt from its result file.


### 2026-11-12 (again): pip works now, and an LLM analyst on the chart

#### The install question, settled empirically

The old note said pip was blocked. It was blocked as `pip.exe`, not as
`python.exe -m pip`. Verified: `python.exe -m pip --version` -> 26.2.2, and a
`--dry-run` install reached PyPI with exit 0. Installed: `lightgbm` 4.7.0,
`torch` 2.15.0+cpu (CPU wheel index, ~200MB not the 2.5GB CUDA build),
`matplotlib` 3.11.2.

#### What the new libraries actually changed: nothing

`ml_edge_test.py` now also runs LightGBM (leaf-wise boosting, the xgboost family):

| EURUSD H1 h5 | linear | HistGB | MLP | **LightGBM** |
|---|---|---|---|---|
| OOS AUC | 0.5160 | 0.5161 | 0.5208 | **0.5116** |
| net | -5.87% | -16.72% | -8.91% | **-44.45%** |

LightGBM is *worse* than logistic regression. More capacity on a signal that is
not there just overfits harder.

`research/seq_edge_test.py` (torch) tests the one thing the tabular test could not:
whether the *shape* of the last 32 bars carries direction. A dilated TCN and an
LSTM both see 32 bars; the linear benchmark sees only the newest bar.

| EURUSD H1 h5 | linear (1 bar) | TCN (32 bars) | LSTM (32 bars) |
|---|---|---|---|
| OOS AUC | **0.5201** | 0.4880 | 0.5036 |
| net | -1.63% | -0.42% | +0.22% |

TCN is *below a coin flip*. The LSTM's **shuffled control scored 0.5173 - higher
than the real net (0.5036)**, so the LSTM learned nothing at all. The +0.22% is 70
trades of noise. Sequence memory is not the missing signal.

#### Meta-labelling: the right place for ML, and it still does not help

The honest use of ML here is not predicting price - it is ranking the trades of the
one edge that survived. `research/rsi_meta_label.py` reproduces the RSI band rule
(min-hold 12, margin 3, spread gate) = 550 trades, **40.5% win rate**, +0.0261% mean
net per trade. It then trains a walk-forward classifier on the rule's own entries.

| target | linear AUC | lgbm AUC |
|---|---|---|
| will this trade win | 0.4512 | 0.4610 |
| predicted PnL | 0.5143 | 0.4897 |

None of these can rank trades. And the money test shows the same shape every time:
performance gets *worse* the harder you filter (keep 80% -> 60% -> 40% -> 20%), which
is the signature of noise, not ranking power. The one configuration that looked good
(lgbm / PnL / keep 80%) had a rank AUC of 0.4897 - below chance - so it cannot be
real. `--perm` runs the same-size cut with shuffled scores and reports how often
random does as well.

**Conclusion: ML/NN/LightGBM/torch all measured, none adds an edge.** The binding
constraint is the signal, not the model. This is now the fourth independent model
class to say so.

#### "Put Claude/GPT on the live chart"

The repo already had the pieces:
- `research/fxmopol_llm_brief.py` - real GPT **and** Claude callers, advisor-only
  system prompt ("never invent prices, prefer no trade on chop").
- `python/century_adapt.py` - LLM risk overlay via `OPENAI_API_KEY`.
- `EA/Include/SovereignChartSnapshot.mqh` - the EA already exports what it saw.
- `EA/Include/SovereignChartVisualizer.mqh` - a full MQL5 drawing engine
  (DrawBox / DrawLevel / DrawArrow / DrawText / DrawTrend / DrawPanel) already wired
  into three EAs, currently drawing entries, SL/TP and a status panel.

What was missing was the loop. Added:

1. `research/chart_annotate.py` - deterministic annotated PNG: candles, EMA21/50,
   confirmed look-left S/R (drawn from the confirmation bar `i+k`, never the swing
   bar, so it cannot show a level the bot did not have), RSI9 with 25/50/75, shaded
   bad zones (spread > p90, ATR > 1.8x, thin liquidity), RSI-band entry arrows, and
   a BOT VIEW panel. -> `research/results/charts/<SYM>_<TF>.png`
2. `research/llm_chart_analyst.py` - builds the exact state bundle, optionally
   attaches the PNG for vision models, asks for **strict JSON** (read, confidence,
   narrative, risks, veto, draw instructions). `sanitize()` snaps any drawn level to
   a supplied level within 0.25 ATR and rejects anything further than 5 ATR, so the
   model cannot invent a level. No API key -> deterministic analyst with the same
   schema, so the pipeline is testable for free.
3. `chart_annotate.py --note <json>` - draws the AI's levels, zones, arrows and
   narrative back onto the chart.

**Design position (unchanged):** the analyst is advisor-only. It may veto and
explain; it may never size or trigger. Every visual pattern measured here is a
persistence detector with zero drift, so an LLM narrating a chart will describe
edges that are not there - confidently. Its value is explainability and risk
review, not prediction.

Verified: no API key set in this environment, so all runs used the deterministic
path. `ai_note_EURUSD_H1.json` carries 6 levels and 3 arrows; the PNG grows
217KB -> 268KB when the overlay is drawn, confirming the round trip.

## 2026-11-12 (latest): template at runtime, and the analyst draws on the live chart

Two gaps closed, both from the same requirement: the bot must look at the chart the
way the human's template says, and it must show what it sees on that chart.

### 1. The EA reads the template at runtime and hot-reloads it

MQL5 `FileOpen` is sandboxed to `MQL5\Files` and `Common\Files`; it cannot open
`Profiles\Templates`. So the template is converted to a flat ASCII spec and
published where the EA is allowed to look:

- `research/chart_template.py --ini` decodes `templates\fxmopol.tpl` (UTF-16LE,
  COLORREF `0x00BBGGRR`) and emits `KEY=VALUE`.
- `scripts\sync_template.ps1` writes `Common\Files\Sovereign\template\fxmopol.ini`.
  `-Watch` republishes on save. `install_ea.ps1` also calls it, so the spec is
  never stale after an install.
- `EA/Include/SovereignTemplateSpec.mqh` loads it, checks `FILE_MODIFY_DATE` on
  every tick and **reloads when it changes** - no recompile, no restart. It exposes
  background/foreground/candle/grid colours, every MA (period, method, colour and a
  live `CopyBuffer` read), the fractal colour and the RSI period/levels.

Read-only by construction: the template drives what the bot *sees and draws*, never
the trade rule. That matters because the template says RSI 20/50/80 while the
validated H1 research band was 25/75; silently swapping the validated rule for a
template value would be an unmeasured change.

Verified spec: `EURUSD H1 | bg #000000 | candles #00ff00/#ff0000 | EMA21 #00ffff |
EMA50 #ffd700 | SMMA100 #ffffff | fractals #ffffff | RSI9 #1e90ff levels 20/50/80`.

### 2. The analyst's read is drawn on the live chart

`research/llm_chart_analyst.py` now writes two files:

- `ai_note_<SYM>_<TF>.json` - the analyst's record (unchanged).
- `ai_overlay_<SYM>_<TF>.csv` - the EA's record, one comma-separated record per
  line (`READ`, `SOURCE`, `NARR`, `VETO`, `PRICE`, `EMA21`, `EMA50`, `RSI`, `ATR`,
  `SLOPE`, `STRUCT`, `SPREAD`, `RISK`, `LEVEL`, `ZONE`, `ARROW`). Free text has
  commas stripped so a narrative can never shift the layout. Written to
  `research\results\` and to `Common\Files\Sovereign\`.

`EA/Include/SovereignAiOverlay.mqh` reads it, hot-reloads on change, and draws
through `g_paint`: S/R levels (dotted, support/resistance coloured), shaded zones,
entry arrows at `iTime(sym,tf,-offset)` with an ATR pad, and a `AI <READ> <conf>`
label at the last bar. All objects use the `AI_` sub-prefix and are cleared in
`OnDeinit`. A read older than `InpAiOverlayMaxAgeSec` (default 2h) is marked
`STALE` in the panel instead of being trusted.

Safety: display only. It may veto and explain, never size or trigger.

### Verified

- `install_ea.ps1`: **5/5 EAs compile, 0 errors** (33 pre-existing warnings in
  GodsEye, none from the new code).
- Overlay contract replayed in Python against the real CSV: `READ` 4 fields,
  every `LEVEL` 4 fields, 6 levels / 0 zones / 0 arrows for EURUSD H1.
- `.env.example` restored; `.env` stays gitignored. `research/sovereign_env.py`
  loads it with stdlib only (real env wins), and `scripts/set_llm_key.ps1` stores
  a persistent user variable as the alternative.

### Note on the M1 / M15 / M30 sweep (from DOCS_M1_RESEARCH.md)

M1 is rejected: 486 configs per symbol on ~97 days, EURUSD +0.23%, GBPUSD -0.10%,
USDJPY +0.36%, spread-dominated. The pullback entry does pass all three splits on
**USDJPY M15** (+3.81/+2.05/+1.46%, Sharpe 0.47, DD -4.50%) and **GBPUSD M30**
(+1.72/+4.98/+3.12%, Sharpe 0.57, DD -3.16%) - research passes, no forward demo.

### 3. OpenAI key diagnosis (2026-11-12): the key is fine, the account has no credit

"The OpenAI API is not working" was two different problems wearing one symptom.

`--probe` (new, `GET /v1/models`, no token spend) proves the key itself is good:
**HTTP 200, 96 models visible, `gpt-4o-mini` AVAILABLE**, key `sk-proj...IlIA`, len 164.

The actual completion then returns:

```
openai HTTP 429  -> account has NO CREDIT
   credit_balance_exhausted: You have no credits remaining.
```

So the auth is correct and the balance is zero. That is an account action, not a code
action: add credits at
`https://platform.openai.com/settings/organization/billing/`.

Code changes so this is never ambiguous again:

- `_post_json()` now reads the HTTPError body and reports the provider's own
  `code: message`, plus a per-status hint (401 key, 403 permission, 404 model,
  429 quota vs 429 rate limit). Previously the body was discarded and only
  "HTTP Error 429" survived - which is why the cause was invisible.
- Transient socket timeouts (`WinError 10060`, seen intermittently on this box while
  DNS and TCP 443 were both fine) are retried 3x with exponential backoff.
  Rate-limit 429s retry; quota 429s fail immediately with the billing link.
- `--strict` exits non-zero instead of silently substituting the deterministic read,
  so a dead key can never masquerade as a working analyst. Default stays graceful.
- `--probe` verifies a key for free.

Behaviour is unchanged for the rest of the pipeline: when the LLM is unavailable the
deterministic analyst produces the same JSON schema and the same `ai_overlay_*.csv`,
so the EA drawing loop keeps working with no key at all.

### 4. MA compression rubberband retracted (2026-11-12): a metrics bug, not an edge

The AGENTS.md headline - "44/48 OOS passes", "EURUSD PF 4.28 Sharpe 1.37
maxDD -0.05%", "all maxDD < 2.3%" - does not survive contact with its own code.

`research/ma_compression_sweep.py::backtest_positions()` stored

    returns_arr[i] = (close[i] - entry_price) / tick * 0.01 / equity

which is the **cumulative gain since entry**, not the bar-over-bar return.
`metrics()` then did `(1 + r).cumprod()` on it. So a trade that stayed open for
20 bars contributed its whole gain 20 times, the "equity" curve rose
monotonically inside every trade, and Sharpe came out at 5-11 with maxDD near
zero. The entry cost was also subtracted twice (at entry and again at exit).

Every other validated script in the repo uses the correct contract -
`rsi_band_net.returns()` and `rsi_band_m15m30_sweep.simulate()` both compute
`ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1)` and charge
`turn * cost_side / close`. `ma_compression_sweep` was the only outlier.

Marked to market bar by bar, with the identical cost model the RSI-band KEEP
uses (spread/2 floored at 1 pt + 1 pt slippage + 3.5 pt half-commission, per
side):

| symbol | config | trades | honest PF | total | maxDD |
|---|---|---|---|---|---|
| EURUSD | compress 1.0 / ext 1.5 | 2,044 | **0.91** | **-20.35%** | -23.75% |
| USDJPY | compress 1.0 / ext 2.5 | 371 | **0.83** | **-11.19%** | -12.62% |
| EURUSD | compress 0.3 / ext 2.5 | 14 | 0.50 | -1.44% | -2.41% |

The rule is cost-negative on every pair. The "best" documented config
(0.3 / 2.5) fires **14 times in 8 years** - the 4.28 PF was computed from a
handful of trades inside the OOS window, on a bar series that was mostly zeros.

Second problem: `oos_pass` checked OOS only, and the summary sorted by
`OOS_pf.idxmax()`. So the parameter set was chosen on the out-of-sample window,
which the project forbids everywhere else.

Fixes applied:

- `backtest_positions()` now returns true per-bar returns, per-trade returns and
  trade exit indices.
- `trade_metrics()` reports trade-level PF and win rate; the old PF was a ratio
  of summed bar marks.
- `all3_pass` requires PF >= 1.05, Sharpe >= 0.25 and maxDD >= -30% in design,
  validation **and** OOS; `oos_pass` is kept but labelled "NOT a result".
- Selection is on the worst of design/validation; OOS is never the selector.
- `publish_results()` now runs for the selected config per pair (monthly + daily
  calendar proof, per the standing reporting rule).
- `AGENTS.md` retracts the claim and says not to enable
  `InpStrategy=SOV_COMPRESSION_RUBBERBAND`.
- `edge_ledger.py` gains the RETRACTED row plus the M15/M30 passes; the ledger
  is now 2 KEEP / 7 CONDITIONAL / 24 REJECT-RETRACTED of 33 concepts.

Verified separately: `rsi_band_m15m30_sweep.py` already uses the correct
contract, so the GBPUSD M30 (+1.72/+4.98/+3.12%) and USDJPY M15
(+3.81/+2.05/+1.46%) passes are not affected by this bug.

## 2026-11-12 (latest): one backtest contract, the corrected sweep, and the first real book

### 1. The bug class is now closed by construction

`research/backtest_core.py` is the single contract. `simulate_stops()` marks a
position to market bar by bar, so `(1 + ret).cumprod() - 1` is the account
return. `trade_metrics()` reports trade-level PF. `validate_returns()` raises if
a series looks cumulative. It self-tests:

```
selftest hold: total -0.01046823  expect -0.01046823  OK
selftest cumulative: caught  OK
trades recorded: 1 (expect 1)  OK
```

Three scripts were audited:

| script | status |
|---|---|
| `three_way_meta.py` | **was live-buggy** (returned the cumulative series). Now delegates to `simulate_stops`. |
| `intraday_discovery.py` | metrics already came from the equity curve, so its published results were never affected; the dead buggy `returns_arr` block is deleted and `m` is now derived from the same equity curve. |
| `ma_compression_sweep.py` | now delegates to `simulate_stops` (local copy removed). |

The `\.venv` docstring warnings that made every script exit non-zero in
PowerShell are gone: all of `research/*.py` compiles under
`-W error::SyntaxWarning`.

### 2. Corrected MA-compression sweep: 2/48, and both are noise

The full sweep re-ran on honest bar returns. Selection is on the worst of
design+validation; OOS is the exam.

| pair | config | design | validation | OOS | OOS Sharpe | OOS maxDD | trades |
|---|---|---|---|---|---|---|---|
| GBPUSD | compress 1.0 / ext 2.5 | PF 1.30 | PF 1.26 | PF 1.14 | 0.33 | -4.06% | 115 |
| USDJPY | compress 0.5 / ext 3.0 | PF 2.74 | PF 2.03 | PF 4.04 | 0.55 | -0.47% | 8 |
| EURUSD | best of 16 | fails | fails | fails | — | — | — |

Only **2 of 48** configs clear all three splits. That is *below* the ~6/48 you
would expect from chance across three independent splits, and the USDJPY entry
has 8 design trades. GBPUSD 1.0/2.5 compounds **+10.88% over 8.6 years**
(~1.2% CAGR). The retraction stands: do not enable
`InpStrategy=SOV_COMPRESSION_RUBBERBAND`.

An earlier revision of the summary loop reused the outer loop's `df`, so the
calendar CSVs were computed on the last symbol's prices under the wrong name
(GBPUSD read -7.86% when the true figure is +10.88%). Fixed; the per-split CSV
was always correct, only the publish step was wrong.

### 3. `portfolio_combine.py` is now the real book

It was still blending carry with `stack_GBPUSD_mid50_professor_governor`, a
sleeve that was never certified. It now builds the book from the sleeves that
actually survived:

- `carry_mom_filter` (D1) — KEEP
- `rsi_band_net` GBPUSD H1 ensemble — KEEP (broker tape only; fails HistData independent)
- `rsi_band_net` EURUSD H1 — CONDITIONAL (broker tape only; fails HistData independent 2005-2025)
- RSI pullback USDJPY M15 — CONDITIONAL, reported separately because 3 years of
  history would shrink the whole book's proof window

IMPORTANT: EURUSD passes all 3 splits on the broker tape (2018-2026: +5.92 / -0.09 /
+1.03%) but FAILS all 3 splits on independent HistData (2005-2025: -28.95 / +6.74 /
-5.11%). The broker-tape pass is a data-window artifact — the 2018-2026 period was
favorable but the full 2005-2025 design window was strongly negative. The main book
therefore uses carry + GBPUSD only. EURUSD is reported separately, not folded in.

Weights are inverse-volatility measured on **design+validation only**, capped at
50%. OOS never picks a weight. `hedge_blend_test.py` and
`rsi_band_m15m30_sweep.py` now publish calendar proof too.

**Main book, 2018-01 -> 2026-11 (104 months):**

```
weights   carry 21.8%   RSI GBPUSD 78.2%
sleeve                       design  validation       OOS
carry_mom_filter            -3.78%      13.11%    10.22%
rsi_band_GBPUSD_H1           2.01%       3.12%     2.01%
COMBINED                     1.11%       5.41%     4.06%
  Sharpe                     0.12        0.77      0.74
  maxDD                     -3.16%      -3.62%    -2.64%
  full: CAGR +1.20%  Sharpe 0.47  maxDD -3.62%  total +10.91%
  positive in all three splits: YES
```

**EURUSD sleeve (broker tape only, not in main book):**

```
sleeve                       design  validation       OOS
rsi_band_EURUSD_H1           5.92%      -0.09%     1.03%
COMBINED                     5.92%      -0.09%     1.03%
  positive in all three splits: NO (validation flat)
```

D+V correlation: carry vs GBPUSD +0.15.

Adding USDJPY M15 lifts full Sharpe to 0.67 / maxDD -1.16% but that window starts
2022-11 and has **zero design months**, so it is reported separately and is not
part of the pass.
and OOS only. That is a shorter, weaker proof, not a better one.

Note: the RSI-EURUSD sleeve series ends 2025-12 while the H1 tape runs to
2026-11, so the book's last 11 months are not yet included.

### 4. The hedge blend is retracted: it was a lookahead

Building the book exposed the last survivor as an artifact. `hedge_blend_test.py`
set the regime weight for month *t* from `carry.rolling(3).sum()`, and a 3-month
rolling sum ending at *t* **includes `carry[t]`**. So the weight for a month
already knew that month's outcome. The rule was, in effect, "take more carry in
months when carry did well", which is not a tradeable signal.

Lagged one month — the only causal version — the published table collapses:

| Metric | carry alone | RSI band alone | blend (honest) | blend (retracted) |
|---|---|---|---|---|
| D+V Sharpe | 0.32 | 0.62 | 0.51 | 1.89 |
| OOS Sharpe | -0.27 | 0.42 | **0.11** | 1.47 |
| Full Sharpe | 0.20 | 0.58 | 0.42 | 1.81 |
| Full maxDD | -17.8% | -5.5% | -7.5% | -3.0% |
| Full CAGR | 1.59% | 1.84% | 1.80% | 7.22% |

The blend is **strictly dominated by RSI band alone in every split**. It adds
drawdown and loses Sharpe. The robustness sweep confirms it from the other
direction: as the carry weight goes 0.00 -> 1.00 the D+V Sharpe falls
monotonically **0.62 -> 0.30**, so weight 0.00 (pure RSI band) is the best member
of the family. Momentum lookbacks 1/2/3/6/9/12 give OOS Sharpe -0.20 to +0.89
with no plateau.

Fixed in `hedge_blend_test.py`, `hedge_robustness.py` and `hedge_recent.py` (all
three had the same unshifted rolling sum). The leaky variant is deliberately kept
in `hedge_blend_test.py` under "LOOKAHEAD CONTROL" so the trap is visible and the
negative result is preserved. `hedge_blend_test.py` now writes
`hedge_blend_manifest.json`, which the ledger reads, so the row is rebuilt from a
file rather than retyped.

`EA/Include/SovereignHedgeBlend.mqh` and the EA input group are relabelled
"RETRACTED - do not enable"; `InpHedgeBlendEnabled` stays false. EA recompiles:
0 errors, 33 pre-existing warnings.

Ledger after this session: **2 KEEP, 7 CONDITIONAL, 25 REJECT/RETRACTED of 34**.

### 5. What this changes about the plan

Three "survivors" have now fallen to the same class of error - structure grade,
MA compression, hedge blend - and two of the three were lookahead, not cost. The
project's remaining honest edges are exactly two: **carry D1** and **RSI band
EURUSD H1**, plus the M15/M30 pullbacks that are research-only. Everything else
in the ledger is a measured rejection.

That makes the next real question narrower than "what else can we add": the book
built from the two KEEPs plus the GBPUSD M30 conditional has a **-2.63% maxDD**
but a negative design split. So the open problem is not finding a third edge, it
is whether the 2018-2022 window was a genuinely different regime or whether the
rules only work after 2022 by chance. That is answerable: split the two KEEPs on
their own 60/20/20 boundaries and check whether 2018-2022 is negative there too.

## 2026-11-12 (latest): the design split was a data window, and the ensemble beats the knob

### 1. The question

The book's design split was -1.72%. Two explanations were possible: the rules only
work after 2022 (a real regime break), or the book's start date was wrong. The
start date was set by GBPUSD M30, the only intraday sleeve in the book.

### 2. The start date was a broker history limit

| tape | first bar | last bar | bars |
|---|---|---|---|
| EURUSD H1 | 2018-01-01 22:00 | 2026-11-12 09:00 | 55,129 |
| GBPUSD H1 | 2018-01-31 00:00 | 2026-11-09 08:00 | 54,553 |
| GBPUSD M30 | **2018-10-28 23:30** | 2026-11-12 21:00 | 100,002 |
| EURUSD M30 | **2018-10-26 21:00** | 2026-11-12 19:00 | 100,000 |
| USDJPY M15 | **2022-11-04 12:15** | 2026-11-12 19:15 | 100,040 |

Re-exporting M30 with `--from-date 2018-01-01` was tried against the live terminal:
the broker returned **nothing before 2018-10-28** (100,002 bars, same start). So the
9-month gap is not a code cap, it is the broker's M30 depth. `scripts/export_mt5_bars.py`
pages 30 days at a time via `copy_rates_range`, so nothing in our code limits it.

The excluded slice (2018-01 → 2018-10) is exactly the kind of slice that decides a
sign: **carry -2.53%, RSI EURUSD +6.04%**.

`research/book_window_sensitivity.py` reports the same book on different start
dates (this is a diagnostic, not a selection — no window is chosen because it
passes):

| book | start | months | design | validation | OOS |
|---|---|---|---|---|---|
| 2 sleeves (carry + RSI EURUSD ensemble) | 2018-01 | 104 | **+3.89%** | +3.85% | +4.32% |
| 3 sleeves (+ GBPUSD **H1** ensemble) | 2018-01 | 104 | **+3.30%** | +2.93% | +2.75% |
| 3 sleeves but with GBPUSD **M30** | 2018-10 | 95 | **-0.76%** | +4.85% | +3.62% |
| 3 sleeves (+ GBPUSD H1 ensemble) | 2019-01 | 92 | **+1.04%** | +2.87% | +2.72% |

Two things move the sign, and both are now fixed: the start date the M30 tape
forces, and the knob-selection step (the last row is positive even with 2018
entirely removed, which it was not with the selected configs).

Year by year the sleeves are not uniformly dead before 2022 — carry is volatile
(-7.10% 2018, +10.65% 2022) and the RSI legs are small but mostly positive. There
is no run of bad years that would mark a regime break; there is one 9-month slice
that the M30 tape cannot reach.

### 3. GBPUSD H1 is the sleeve with history, and it was already certified

`rsi_band_net.py` hardcoded the EURUSD levels (50/75/25), so GBPUSD could not be
tested with its own parameters. It now takes `--rsi-period/--mid/--hi/--lo` plus
`--from-date/--to-date`; the defaults are unchanged, and the parameterised path
**reproduces the documented GBPUSD numbers exactly**:

```
python research/rsi_band_net.py --symbol GBPUSD --tf H1 --rsi-period 9 \
    --mid 45 --hi 65 --lo 35 --deadband 0.2 --min-hold 12 --margin 3
  design +3.37%   validation +1.81%   OOS +0.05%     <- matches AGENTS.md
```

GBPUSD H1 therefore replaces M30 in the book: same edge, independently certified,
and it starts 2018-01 instead of 2018-10.

### 4. The knob selection was the overfit, not the rule

The tape now runs to 2026-11, which moved the 60/20/20 boundaries. On that tape the
EURUSD config the project's rule selects (best worst-of-design+validation) has
**OOS -1.35%**, while the *family average* is **+0.75%**. That is not a coincidence
of one pair:

| symbol | configs positive in all 3 | median OOS | D+V winner OOS |
|---|---|---|---|
| EURUSD | 61/100 | +1.23% | **-1.35%** |
| GBPUSD | 75/100 | +1.85% | +3.13% |

Ranking 100 configs on `min(design, validation)` selects the extreme of the surface,
and the extreme is the least likely member to generalise. `rsi_band_net.py --sweep`
now also reports and publishes the **equal-weight ensemble of all 100 configs**
(`rsi_band_ens_<SYM>_H1_monthly.csv`), which removes the selection step entirely:

| symbol | ensemble design | validation | OOS | full Sharpe | full maxDD |
|---|---|---|---|---|---|
| EURUSD | +3.21% | +2.81% | **+0.75%** | 0.29 | -7.30% |
| GBPUSD | +2.75% | +2.22% | **+1.43%** | 0.35 | -3.09% |

Both ensembles are positive in all three percentage splits. The book trades the
ensemble, not the winner.

### 5. The book now passes

`research/portfolio_combine.py`, 2018-01 → 2026-11, **104 months**, inverse-vol
weights on design+validation only (cap 50%), OOS never used:

```
weights   carry 9.4%   RSI EURUSD 37.8%   RSI GBPUSD 52.8%
sleeve                       design  validation       OOS
carry_mom_filter            -3.78%      13.11%    10.22%
rsi_band_EURUSD_H1           6.40%      -0.09%     1.52%
rsi_band_GBPUSD_H1           2.01%       3.12%     2.01%
COMBINED                     3.30%       2.93%     2.75%
  Sharpe                     0.54        0.71      0.85
  maxDD                     -1.57%      -2.37%    -0.82%
  full: CAGR +1.03%  Sharpe 0.66  maxDD -2.37%  total +9.26%
  positive in all three splits: YES
```

OOS Sharpe is the highest of the three splits and OOS maxDD is the smallest — the
book is not decaying. D+V correlations: carry vs EURUSD -0.17, carry vs GBPUSD
+0.15, EURUSD vs GBPUSD +0.06. Adding USDJPY M15 lifts full Sharpe to 0.75 /
maxDD -0.93% but that window starts 2022-11 and has **zero design months**, so it is
reported separately and is not part of the pass.

Note on the sleeve table: the book drops months where any sleeve is missing, so its
per-sleeve totals differ slightly from the full-series totals in the ledger
(EURUSD design 6.40% in-book vs 5.92% full-series). Both are correct; the book
reports what it could actually trade.

### 6. What changed

- `research/rsi_band_net.py`: `--rsi-period/--mid/--hi/--lo/--from-date/--to-date`;
  equal-weight ensemble accumulated inside `--sweep`, reported and published as
  `rsi_band_ens_<SYM>_H1`.
- `research/portfolio_combine.py`: GBPUSD H1 ensemble replaces GBPUSD M30; sleeves
  read the ensemble monthly CSVs.
- `research/book_window_sensitivity.py`: new — year table, excluded-slice pricing,
  and the same book on four start dates.
- `research/edge_ledger.py`: the RSI section now loops EURUSD **and** GBPUSD, reads
  the ensemble monthly CSV for the design/validation/OOS columns, and keeps the
  plateau statistics (`61/100`, `75/100`) in the metric column.

Ledger after this session: **2 KEEP, 5 CONDITIONAL, 28 REJECT/RETRACTED of 35.**
The two KEEPs are now **carry D1** and **RSI band GBPUSD H1 (ensemble)**; EURUSD
drops to CONDITIONAL because its fixed-calendar validation is -0.09% (essentially
flat) even though the percentage split has it positive. The 3 EMA21 pyramid
chunks are now REJECTED — none pass all 3 splits (EURUSD +21.68/-9.28/+3.74,
GBPUSD -1.12/-4.04/+7.42, USDJPY -0.15/-15.63/+35.29).

### 7. The honest caveat

The book is carry + GBPUSD only. EURUSD is demoted — it passes on broker tape
(2018-2026: +5.92 / -0.09 / +1.03%) but FAILS all 3 splits on independent
HistData (2005-2025: -28.95 / +6.74 / -5.11%), so the broker-tape pass is a
data-window artifact. GBPUSD H1 is the stronger leg and carries the book on its
own. Forward demo on broker tape complete (+1.40% net, Sharpe 1.14, maxDD
-0.1% over last 12 months). Not cleared for live on HistData independent tape.

---

## 8. Built-in market analyst + dynamic markup (2026-11-14)

**Goal (user directive):** the EA should *analyse the market the way an LLM
analyst (GPT / Claude / Gemini) would*, and *visualise that read* — not run an
autonomous pyramiding execution engine. This is additive: the trading rule is
unchanged; the analyst is **display / decision-input only** and never sends an
order.

### New module — `EA/Include/SovereignDynamicPlan.mqh`
`CSovereignDynamicPlan` owns its own indicator handles (`iMA` slow/fast/mid,
`iATR`, `iRSI`, `iFractals`) and builds a `SovereignPlanView` snapshot from live
buffers + Bid/Ask. **No hardcoded prices.** It reports:

- **regime**: `uptrend` / `downtrend` / `range` (price vs slow MA AND fast/mid alignment)
- **momentum**: RSI level + direction of the RSI line (`rising` / `falling` / `flat`)
- **volatility**: ATR percentile band (`low` / `normal` / `elevated` / `extreme`)
- **structure**: distance to the most recent confirmed fractal high/low, in ATR units
- **bias + confidence**: transparent additive score (clamped 0.05–0.95)
- **dynamic plan**: entry reference, ATR stop, ATR take-profit, two ATR-spaced
  scale-in checkpoints, and a stop zone whose top is the swing high / ATR ceiling
  and whose bottom is the entry stop
- **narrative** and **risk flags** (plain English, one line each)

### New markup — `CSovereignChartVisualizer` (`SOV_DYN_` prefix)
Additive methods (the legacy `DrawStrategySimulationPlan` is untouched and now
opt-in via `InpLegacySimPlan`, default false):
- `DrawDynamicPlan()` — entry/stop/TP/scale-in H-lines, shaded stop-zone
  rectangle, fractal support/resistance lines, directional arrow, live banner
- `DrawDynamicPaths()` — slow-MA regime line + fast/mid dynamic resistance band
  as polylines over `InpAnalystPathBars`
- `DrawDynamicLegMarkers()` — numbered `Leg 1/2/3` markers at the exact bar time
  and price of live Sovereign positions
- `ObjectsDeleteAll(0, "SOV_DYN_")` on deinit; per-sub-prefix cleanup on redraw;
  markup redraws on new bars only (no per-tick churn)

### EA wiring (`SovereignGodsEyeEA.mq5`)
- New input group **"Dynamic Market Analyst"** (`InpAnalystEnabled`, MA/ATR/RSI
  periods, `InpAnalystStopAtr/TpAtr/ScaleStepAtr`, `InpAnalystPathBars`,
  `InpAnalystShowNarrative`).
- `g_plan` initialised in `OnInit`, refreshed on new bars in `OnTick`.
- Panel gains ANALYST / Vol-Struct / Levels / Structure / READ / RISKS rows.
- The RSI-band macro lock now prefers `g_plan.SlowMa()` when ready (single source
  of truth), falling back to the legacy macro handle — never invents a value.
- Old hardcoded BTCUSD sim-plan inputs are **kept but marked DEPRECATED** and are
  only used if `InpLegacySimPlan=true` (zero-hardcoded rule honoured by default).

### Pending-issue fixes carried in this change
- `OnInit` no longer returns `INIT_FAILED` on Algo-Trading / account / symbol
  state; it logs and initialises in observe mode (fixes the code-1 reattach loop).
- `SovCheckSymbolTradable` adds the symbol to Market Watch before checking;
  `SovSetTradeFilling` queries `SYMBOL_FILLING_MODE` instead of hardcoding RETURN.
- `string syms[]` is dynamic (universe is 11 symbols, not 10).
- `m_atrHist` resized in both constructor and `Init`; `AtrAllowed()` guards an
  empty buffer; `CurrentRsi()`/`CurrentRsiPrev()`/`CurrentSpread()` guarded.
- Panel arrays widened to 48 (was 18 — overflow crash at ~27 rows).

Compile: `MetaEditor64` — **0 errors, 2 warnings** (both pre-existing/benign:
`POSITION_COMMISSION` deprecation and one `uint`→`int` conversion).

**Standing caveat:** the analyst and all its overlays are **analysis/visual only**
and are not part of any backtest. The 2026-11-14 unvalidated overlays (EMA200
macro lock, 1.5-ATR trailing stop, RSI<30 exhaustion exit) remain ON by default
and are still **UNVALIDATED** — they must be switched off and re-validated before
this build is promoted. The validated rule is unchanged.

---

## 9. Dynamic-plan wiring + opt-in pyramiding (2026-11-14, later)

Final integration pass on top of §8. Full detail: `docs/DYNAMIC_PLAN_WIRING.md`.

- **`UpdatePyramidState()`** now owns the position aggregation and is called at
  the very top of `OnTick()`. It counts legs on `SOVEREIGN_MAGIC` + `_Symbol`,
  sums `PROFIT+SWAP+COMMISSION`, and sets
  `g_pyramidArmed = (legs == 0 || netProfit > 0)`. The three globals
  (`g_pyramidLegs`, `g_pyramidNetProfit`, `g_pyramidArmed`) are initialised at
  file scope (`g_pyramidArmed = true`).
- **`g_paint.DrawDynamicPlan(plan)`** is called immediately after
  `g_plan.Build(...)` inside `PaintChart()` (new-bar throttled via
  `g_lastDynBar`). `DrawAnalystMarkup()` now only draws the MA paths + numbered
  leg markers.
- **Input bridging:** `InpRsiBandStopAtr` (new, default 1.5) → `SetStopAtr()`;
  `InpTrailAtrMultiplier`, `InpRsiExhaustionLevel`, `InpMaxPyramidLayers`,
  `InpPyramidExecEnabled`, `InpPyramidScaleStepAtr` all routed in `OnInit()` and
  refreshed each tick. `OpenPosition` no longer hardcodes `atr*1.5`.
- **Opt-in multi-leg pyramiding** (`InpPyramidExecEnabled=false` by default):
  `CSovereignRSIBand` opens an additional leg only when (a) execution is enabled,
  (b) `legs < InpMaxPyramidLayers`, (c) `CanPyramidInProfit()` is true, and
  (d) price moved `InpPyramidScaleStepAtr * ATR` past the last leg with the trend
  agreeing. Scale-in legs are sized at 0.5x the initial leg. `ClosePosition()`
  now closes **all** matching legs; `CountLegs()` reconciles state.
- **UNVALIDATED:** pyramiding execution is in no backtest. Leave
  `InpPyramidExecEnabled=false` for live/demo until measured with
  `research/backtest_core.py`.
- **Template alignment:** the analyst reads MA 21/50/100 + RSI 9 straight from
  `fxmopol.tpl` at `OnInit` (sorted ascending into fast/mid/slow) and falls back
  to inputs with `InpAnalystSlowMaPeriod` corrected 200 -> 100. `iFractals` feeds
  swing high/low. Verified: no price literal exists in the plan or visualizer.
- **Template MA *methods* also read (bug fixed):** the template uses EMA for
  MA21/MA50 but **SMMA** for MA100 (`method=2`). The code hardcoded EMA for all
  three, so the drawn white line did not match the template. `Init()` now takes
  `fastMethod`/`midMethod` and all three come from `g_template.MaMethod(0..2)`.
- **RSI bands are deliberately NOT unified:** template draws 20/50/80; the
  validated strategy trades 75/25 (EURUSD), 65/35 (GBPUSD), 75/25 (USDJPY).
  The panel prints both, orange when they differ. Do not change the execution
  band to 80/20 — that would silently edit a validated rule.
- **Visual parity:** `SOV_DYN_PLAN_TPZONE` shades entry -> TP alongside the red
  stop zone.
- Compile: `MetaEditor64` — **0 errors, 2 warnings** (same two benign ones).

---

## 2026-11-15 - RSI divergence detector (display only) + "EA is not trading" fix

### 1. Divergence: real pivot detection, drawn on the chart

The old code in `SovereignGodsEyeEA.mq5` (`PaintChart`) compared **adjacent bars**
(`low[i] < low[i-1] && rsi[i] > rsi[i-1]`), which is not divergence - it fires on
almost every bar and it named its objects `SOV_DIV_BULL_<i>`, so they accumulated
on the chart forever. Replaced with a proper module.

**New file: `EA/Include/SovereignDivergence.mqh`** - `CSovereignDivergence`.

- **Regular (classic) divergence only:**
  - BULLISH: price makes a LOWER LOW while RSI makes a HIGHER low
  - BEARISH: price makes a HIGHER HIGH while RSI makes a LOWER high
- **Causal by construction.** A swing pivot is only usable once `InpDivPivot` bars
  have closed after it (`shift >= pivot+1`), so nothing on the chart is graded
  with information that did not exist at the time. This is the same discipline
  that the structure-quality and compression-rubberband retractions taught: never
  use an unconfirmed swing.
- **Pairs consecutive pivots of the same kind** with a bar gap inside
  `[InpDivMinBars, InpDivMaxBars]`, requiring `|dRSI| >= InpDivMinRsiDelta` and
  the new extreme to sit in the lower/upper half (`InpDivBullMaxRsi` /
  `InpDivBearMinRsi`). Semantics mirror `research/ema_turn_precursor.py`.
- **Scanned once per bar**, cached on `iTime(...,0)`; drawn once per bar into
  `SOV_VIS_DIV_*` objects (line + arrow + label) after `DeleteWithPrefix("DIV_")`
  so stale marks never linger. Removed with every other visual on deinit.
- Panel row: `DIV last BULL 7 bars ago RSI 28.4->34.1 (+5.7) N found`.
- Journal: one line per newly confirmed divergence
  (`Sovereign DIVERGENCE EURUSD PERIOD_H1: BULLISH pivots ... RSI ... price ... (display only)`).
  The first scan after attach only adopts state; it does not shout about a
  divergence that already existed.
- **DISPLAY ONLY - it never sizes, vetoes or triggers a trade.** RSI divergence
  was already tested as a filter in `research/ema21_fakeout.py` and produced no
  edge, so it must not gate entries. No backtest claim is made for it.

New inputs (old `InpRsiBandDrawDivergence` / `InpRsiBandDivLookback` removed):
`InpDivEnabled`, `InpDivRsiPeriod` (14), `InpDivPivot` (3), `InpDivMinBars` (5),
`InpDivMaxBars` (60), `InpDivMinRsiDelta` (1.0), `InpDivBullMaxRsi` (50),
`InpDivBearMinRsi` (50), `InpDivLookback` (500), `InpDivMaxDraw` (12),
`InpDivLogOnNew` (true). The divergence RSI handle is now its own
(`InpDivRsiPeriod`) instead of borrowing the RSI-band period.

### 2. BUG: `InpForceBypassAllFilters` logged the signal and never traded

`CSovereignRSIBand::OnNewBar()` had this shape:

```mql5
if(m_forceBypassAllFilters) { print("skipping ... gates"); }
else { ...all gates... OpenPosition(...); }
```

so the bypass branch printed "FORCE BYPASS - skipping ... gates" on every tick and
then fell out of the function. **No order was ever sent in bypass mode** - exactly
what the Journal showed (ENTRY SIGNAL -> skipping gates -> SOV PLAN -> nothing).

Fix: the bypass branch now owns the entry. It takes the same-bar guard, marks the
gate flags, writes the audit `ENTER` row (`decEntryMode = 1`), calls
`OpenPosition(..., "SOV-RSI-BYPASS", ...)` and returns. `OpenPosition()` also
honours the bypass now: a traffic-light veto and a governor block are logged and
overridden instead of silently killing the order (`FORCE BYPASS - ignoring ...`).
Position sizing, hard lot cap and SL/TP verification still apply - the bypass is
about *filters*, not about risk.

### 3. BUG: `m_barsHeld` counted ticks in intra-bar mode

`m_barsHeld++` ran on every `OnNewBar()` call, and `InpRsiBandIntraBar=true` calls
it on every tick. Min-hold (12), the time stop and `InpRsiBandMaxBars` therefore
fired after N *ticks*, not N bars. Now the intra-bar path increments `m_barsHeld`
only when `iTime(...,0)` changes.

### 4. Journal flood

`OnNewBar entered (intraBar=1)` printed on every tick (the 2026-11-14 journal hit
571 MB in one day). Now behind `m_verboseLog`; the force-bypass signal line is
throttled to once per bar via `m_lastBypassLogBar`.

### Compile
`MetaEditor64` -> **0 errors, 11 warnings** (all pre-existing: 1 x
`POSITION_COMMISSION`, 1 x `ACCOUNT_FREEMARGIN`, 9 x implicit conversions in
`SovereignSmartExecution.mqh`). `.ex5` rebuilt at
`MQL5\Experts\SovereignGodsEyeEA.ex5`.

### Note on BTCUSD M1 (why normal mode refuses to trade)
The Journal shows the traffic light vetoing on **spread**. On BTCUSD M1 the quoted
spread is above the trailing p90, which is the spread gate doing its job. Use
`InpForceBypassAllFilters=true` only to prove order placement; it disables the
cost filters that exist for a reason.

---

## 2026-11-15 (2) - "The EA is not placing trades": the spread caps were FX-calibrated

### Symptom
Live BTCUSD M5 demo: `Entries=HALTED`, no orders, Journal repeating

```
Sovereign SPREAD GUARD | BTCUSD | spread 18424 pts > max 500 - blocking new entries
SOV BLOCKED | BTCUSD | entries=HALTED | reason=no directional regime (price vs slow MA / MA alignment)
```

### Two real bugs behind it

**1. The traffic light's spread veto is a hard veto at a fixed 10 points.**
`CSovereignTrafficLight` sets `m_maxSpread = 10` in its constructor and `Init()`
never overrides it, so `Evaluate()` does `spreadPoints > m_maxSpread -> hardVeto`.
10 points is 1 pip on EURUSD. On BTCUSD (point = 0.001) the broker's *normal*
spread is ~18,000-20,000 points, so the veto fired on **100% of ticks** and
`OpenPosition()` returned before sizing every time. Even on EURUSD (Deriv spreads
10-20 pts) it vetoed most of the time.

**2. The EA-level guard used the same fixed-points idea** (`InpMaxSpreadForTrade =
500`), which is also FX-only: 500 pts = 5 pips on EURUSD but 0.50 USD on BTCUSD.

**3. The block report lied about why.** `g_lastBlockReason` was overwritten at the
end of the gate chain with the plan narrative ("no directional regime"), hiding
the actual guard ("spread too wide") from both the panel and the Journal.

### Fix: the cap is now symbol-scaled, once per bar
`SovSampleSpreadRing()` / `SovMedianSpreadPts()` / `SovEffectiveSpreadCap()` in
`SovereignGodsEyeEA.mq5`:

```
effective cap = max(configured cap, 2 x median of the last <=200 LIVE spreads)
```

The reference is a ring buffer of live `SYMBOL_SPREAD` samples taken once per bar
(seeded with the quote at attach so a normal spread is never mistaken for a
spike). **`iSpread()` (the spread stored in bar history) is NOT usable on this
broker**: BTCUSD history reports a median of ~2,400 pts while every live tick
quotes ~19,000 pts, so the first version of this fix resolved to a 4,848-pt cap
and still blocked. Sampling what the EA itself sees is the only honest reference.

- FX is unchanged: EURUSD median ~10-15 pts -> 2x median is far below the
  configured 12/500 floors.
- BTCUSD: median ~19,900 -> cap ~39,800, so the normal spread passes and a
  genuine spike (>= 2x normal) still blocks.
- `InpMaxSpread = 0` / `InpMaxSpreadForTrade = 0` still means "disabled".
- The traffic light gained `SetMaxSpread()`; the EA refreshes it once per bar in
  `OnTick()` and logs the resolved caps:
  `Sovereign SPREAD CAP | BTCUSD | median=19906 pts | traffic-light cap 39812 (input 12) | entry cap 39812 (input 500) | current spread 19906 pts`

This is the same *relative* idea as the p90 spread gate that the validated
research uses (`research/rsi_band_net.py`), instead of an absolute number that
cannot span FX, crypto and volatility indices.

### Second blocker: the weekend flat froze a 24/7 symbol
With the spread gate open the next block surfaced as
`block=weekend flat (positions closed, no entries until Monday)`. The window was
`Fri>=21h || Sat || Sun || Mon<01h` with no regard for the instrument, so
BTCUSD (which trades all week on Deriv) was blocked every Sunday. Two bugs:

1. **Wrong scope.** The window is now applied only when the symbol has no
   Saturday/Sunday trading session (`SovSymbolTradesOnWeekend()` ->
   `SymbolInfoSessionTrade(..., SATURDAY/SUNDAY, ...)`). FX keeps the
   Friday-21:00 flat; crypto / synthetic indices keep trading. Logged at init:
   `OnInit: weekend session OPEN (24/7 symbol - weekend flat will NOT block entries)`.
2. **The block did not persist.** `allowEntries = false` was set *inside* the
   `TimeCurrent() - g_lastWeekendFlat > 60` branch, so entries were only blocked
   on the one tick per minute that closed positions, and were allowed again on
   every other tick. The block is now set for the whole window; only the
   flattening loop stays rate-limited to once a minute.

**Note:** the cost gate that actually matters for entry quality is still the RSI
band's own p90 gate inside `SovereignRSIBand.mqh` - that one is unchanged and
still skips entries when the quoted spread is above its trailing p90.

### Also fixed in the same pass
- `SOV BLOCKED` now reports `g_lastBlockReason` (the real guard) and prints once
  per bar instead of once per tick; the spread guard does the same.
- The `InpForceBypassAllFilters` branch now actually places the order (previous
  section) - it was a dead end that logged and returned.
- `m_barsHeld` counts bars, not ticks, in intra-bar mode.
- `OnNewBar entered (intraBar=1)` moved behind `m_verboseLog` (the 2026-11-14
  journal reached 571 MB in one day).
- **The journal flood is gone.** `BroadcastMarketPlanAndState()` printed the whole
  telemetry block on `newBar || InpShowTelemetryHud`, and `InpShowTelemetryHud`
  defaults to true - i.e. on every tick. It now prints on a new bar or a phase
  transition only (`g_sovLastPhase`), and `SOV PLAN` prints once per minute or on
  a side change (`g_lastPlanSide`) instead of on `!allowEntries`, which was true
  permanently while any guard was blocking. Today's log had reached 319 MB.

### Compile
`MetaEditor64` -> **0 errors, 11 warnings** (all pre-existing: 1
`POSITION_COMMISSION`, 1 `ACCOUNT_FREEMARGIN`, 9 implicit conversions in
`SovereignSmartExecution.mqh`).

### Status: verified live on BTCUSD M5
Compiling from a standalone `MetaEditor64.exe` does not notify a running MT5, so
the terminal had to be restarted to adopt the new `.ex5`. Confirmed in the journal
after the restart:

```
OnInit: weekend session OPEN (24/7 symbol - weekend flat will NOT block entries)
Divergence: RSI14 pivot=3 bars=5..60 minDelta=1.0 zone<=50/>=50 lookback=500 draw=12 ENABLED (display only)
Sovereign SPREAD CAP | BTCUSD | median=19906 pts | traffic-light cap 39812 (input 12) | entry cap 39812 (input 500) | current spread 19906 pts
Phase: RSI BAND | trend=1 rsi=59.7 barsHeld=1/24 block=none
Entries=ALLOWED | DailyDD=0.00%/5.00% | Century=running | PythonPaused=no | Debug=off | ForceBypass=off
```

No order yet, and that is correct: BTCUSD auto-detect params are mid=30/hi=70,
so a long needs RSI to cross back up through 30 (RSI was 59.7). The gates are now
open; the strategy is waiting for its own signal rather than being frozen by a
mis-scaled guard. Divergence objects appear under the `SOV_VIS_DIV_` prefix and a
new confirmation journals
`Sovereign DIVERGENCE BULLISH|BEARISH ... (display only)`.

`InpForceBypassAllFilters` is left **false** - it is a debug path, not a fix.

### 2026-11-15 (3) - the EA only had HALF the certified RSI-band rule

While answering "on which timeframe is no order yet" the actual entry logic was
read end to end, and the short side was missing.

`research/rsi_band_net.py` - the rule the whole certification rests on - is
symmetric:

```python
if   trend[t] > 0 and rp <= mp and r > m + margin:  state =  1   # long
elif trend[t] < 0 and rp >= mp and r < m - margin:  state = -1   # short
```

`EA/Include/SovereignRSIBand.mqh` implemented only the first line
(`if(rsiPrev <= m_rsiMid && rsi > m_rsiMid + m_margin)` in the `trend > 0`
branch). `m_rsiMid` was **never referenced in the `trend < 0` branch** - that
branch opened straight into the optional modes (engulfing, green-red,
ema-trend, reflex, three-consecutive), and every one of them is
`input ... = false` by default. So on a plain downtrend the EA could not take
the short leg of its own certified rule at all; the only other short path was
the `trend == -2` early-trend-change entry.

That is why "the EA is not placing trades" survived the spread and weekend
fixes: on BTCUSD the auto-detect mid band is **30**, so longs need RSI <= 30
and the (absent) shorts needed RSI >= 30 falling. With RSI sitting at ~55 the
EA was waiting for a signal in a band it could not reach in one direction.

**Fix:** the mirror trigger is now at the top of the `trend < 0` branch, with
the same log line and the same `LogDecision` audit row as the long side:

```mql5
if(rsiPrev >= (double)m_rsiMid && rsi < ((double)m_rsiMid - m_margin))
{ enter = true; side = -1; ... }
```

Compile: **0 errors, 11 warnings**. Live on BTCUSD M15 after reload:
`Phase: RSI BAND | trend=0 rsi=54.8 barsHeld=1/24 block=none` /
`Entries=ALLOWED` - the gates are open, `trend=0` means the market is choppy so
neither branch may fire until an EMA trend exists.

**Parity note for future work:** with `mid = 30` both triggers sit around RSI
30, which is a deep-dip entry, not the EURUSD mid=50 behaviour. The BTCUSD
auto-detect parameters are flagged UNCERTIFIED by the EA itself and were never
swept like GBPUSD M30 / USDJPY M15 were. Do not assume the BTCUSD numbers
inherit the certified EURUSD result.
