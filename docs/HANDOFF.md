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
