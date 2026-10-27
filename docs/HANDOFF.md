# Project Sovereign - AI Handoff / Work Log

Last updated: **2026-10-27 01:05 local (UTC+1)**

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
| Git | **Not a git repository.** Consider `git init` + initial commit. |

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
tested. Every one failed a chronological out-of-sample check:

| Family | Timeframe / data | Design | Validation | OOS / holdout | Verdict |
|---|---|---|---|---|---|
| Donchian trend, EURUSD | H1, 2025-2026 | best +16.08R | -3.39R | **-23.94R** (2025) | fail |
| Donchian trend, 8 pairs | H1, 2025-2026 | none positive | none positive | negative | fail |
| Z-score mean reversion, 9 pairs | H1, 2025-2026 | +11.67R best | +21.82R best | **-53.40R** (2025) | fail |
| Donchian trend, 12 pairs | D1, 2005-2026 | none positive | none positive | mixed, recent negative | fail |
| Time-series / cross-sectional momentum | D1, 2006-2026 | +3-4% CAGR | -6 to -8% CAGR | -2 to -8% CAGR | fail |
| CFTC COT positioning | weekly, 2010-2026 | +5.91% CAGR | +3.30% CAGR | **-1.63% CAGR** | fail |
| Carry + 12m momentum filter | monthly, 2005-2026 | +1.32% CAGR | +0.05% CAGR | +3.26% CAGR | weak only |

The only strategy positive in every split is **carry + 12-month momentum
filter**. On independent HistData it is design **+2.63%**, validation
**+0.26%**, OOS **+3.35%**, recent 2025 **+3.67%**; full-period **+2.13% CAGR**,
Sharpe **0.24**, max drawdown **-20.73%**. It is still not deployable: the
validation period is nearly flat, the drawdown is high, and it is a monthly
portfolio strategy, not the intraday EA. Pure carry has a **-32.10%** max
drawdown on independent data and fails validation. A VIX throttle cuts the
carry max drawdown to about **-16.65%** but also cuts OOS CAGR to **+1.47%**;
Sharpe stays 0.24, so it is not a clear improvement. The carry max drawdown is
concentrated in 2012 (-20.7%, euro crisis); 2016 and 2018 were also negative
years. This is not a smooth, stress-free return stream. Yearly returns:
2011 +2.97%, 2012 -2.82%, 2013 +6.90%, 2014 +6.00%, 2015 +5.84%, 2016
-4.82%, 2017 +3.55%, 2018 -6.77%, 2019 +4.14%, 2020 +0.33%, 2021 +1.50%, 2022
+12.91%, 2023 +1.01%, 2024 +1.23%, 2025 +3.35%.

A **dollar-neutral** version of the momentum filter (stand aside when the
momentum filter removes every name on one side) looked stronger on the
2010-2025 independent slice: full-period **+3.15% CAGR**, Sharpe **0.41**, max
DD **-18.64%**; design +1.82%, validation **+0.19%**, OOS 2020-2025 **+6.90%**,
recent 2025 **+13.68%**. It is invested in only 96 of 190 months, so the return
is episodic, and the validation period is still nearly flat. **It does not
survive the full 2005-2026 Deriv history**: design +0.33%, validation -0.07%,
max DD **-38.37%**. The difference is the 2008 Global Financial Crisis, which
the independent 2010-2025 slice does not cover. The neutral variant is therefore
a period artifact, not a deployable improvement. The base `carry_mom` survives
the full Deriv design+validation only barely (design +1.32%, validation +0.05%)
and has a **-26.30%** max DD there; on independent 2010-2025 it is +2.13% CAGR,
Sharpe 0.24, -20.73% DD. The missing test is 2008 on independent data.
VIX overlays reduce the neutral variant's full-period DD to -33.80% at best,
which is still not acceptable.

**Root cause:** the problem is not the EA, risk code, exits, or the Deriv-Demo
feed. The H1/D1 entry signals have no durable edge, and the independent HistData
check reproduces those failures. The EA's own M5 setup also fails independent
history: full 2011-2025 **-355.79R**, PF **0.75**, with design, validation, and
OOS all negative. The in-sample 2026 result was a regime artifact. More
parameter tuning on the same families will produce false positives, not a
solution. The only survivor is a weak monthly carry premium, which is
structurally different from the intraday EA.

**Do not run this EA live on the strength of the current results.**

#### Independent data check (completed)

To rule out a Deriv-Demo data artifact, the same rules were rerun on
HistData.com M1 data for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, and
NZDUSD, 2010-2025. The M1 data is converted from New York time to UTC,
aggregated to H1/D1, and partial Sunday D1 bars are dropped. HistData does not
yet publish a 2026 yearly archive, so the H1 design year is 2024, validation is
2024 H2, and OOS is 2025. The D1 tests use 2010-2014 design, 2015-2019
validation, and 2020-2025 OOS.

Scripts:
- `research/download_histdata.py` - download and aggregate
- `research/clean_histdata.py` - drop partial Sunday D1 bars and duplicates
- `research/run_independent_validation.py` - rerun the whole suite
- `research/compare_data.py` - compare Deriv and HistData D1 returns
- `research/check_histdata.py` - check rows, duplicates, bad OHLC, NaNs
- `research/carry_overlays.py` - VIX exposure overlays on carry
- `research/build_histdata_m5.py` - build EURUSD M5 from HistData M1
- `research/m5_independent.py` - mode-1 breakout independent test
- `research/m5_meanrev_independent.py` - mode-0 metrics from the saved candidates

Results go to `research/results/histdata/`, never over the original results.

| Family | Independent data result | Verdict |
|---|---|---|
| H1 Donchian trend, EURUSD | design 2024 H1 **+3.57R**, validation 2024 H2 **+15.94R**, OOS 2025 **-11.37R**; every other config negative OOS | fail |
| H1 Donchian trend, 7 pairs | no config positive in design and validation; best validation config OOS **-25.10R** | fail |
| H1 z-score mean reversion, 7 pairs | no config positive in design and validation; best validation config OOS **-65.24R** | fail |
| D1 Donchian trend, 7 pairs | no config positive in design and validation; 2020-2025 OOS mixed or negative | fail |
| D1 time-series / cross-sectional momentum | design negative; validation and OOS negative. Best full-period `tsmom_252_v10` only **+1.07% CAGR**, design -0.45%, validation +0.19%, OOS +2.77% | fail |
| CFTC COT positioning | `follow_z2.0_w156` positive in design and validation, but OOS 2021-2025 **-3.36% CAGR**, recent 2025 **-7.86%** | fail |
| Carry + 12m momentum | design 2010-2014 **+2.63%**, validation 2015-2019 **+0.26%**, OOS 2020-2025 **+3.35%**, recent 2025 **+3.67%**; full **+2.13% CAGR**, Sharpe **0.24**, max DD **-20.73%** | **only survivor; weak** |
| M5 Donchian breakout, EURUSD, hour 7 | design 2011-2014 **-61.65R**, validation 2015-2019 **-191.58R**, OOS 2020-2025 **-102.56R**, full 2011-2025 **-355.79R** (PF **0.75**); every hour negative; only 2014/2015 positive | fail |
| M5 mean reversion, EURUSD, 07:00-16:59 UTC | design 2011-2014 **-1,029.81R**, validation 2015-2019 **-1,557.03R**, OOS 2020-2025 **-1,491.53R**, full 2011-2025 **-4,078.37R** (PF **0.75**); no positive year; every hour negative | fail |

Data quality: the HistData D1 files have 4,167 weekday bars per pair
(2010-2025), 260-262 bars per year, no duplicate timestamps, no bad OHLC rows,
and no missing values. Against Deriv-Demo D1, the matched-day daily-return
correlation is **0.984-0.994**, with a mean absolute close difference of
21-30 points. The sources are broadly consistent; the negative results are not
caused by an obviously broken independent feed. HistData 2023 H1 has some
missing intraday bars, but the D1 bars remain complete; the H1 tests use
2024-2025, so they are unaffected.

The independent check therefore **confirms the negative verdict** for every
intraday and D1 price-only family, including the EA's own M5 hour-7 setup.
Carry + momentum is the only family that survives, and it is a weak, crash-prone
monthly premium, not a standalone 100-year system.

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

- The performance governor learned from too few closed trades: one
  `trade_outcomes.csv` had only 4 rows while 53 positions closed. The governor
  cannot adapt reliably until this is fixed.
- `signal_audit.csv` stops at about 1,005,926 bytes while the EA keeps running.
  The root cause is unresolved; it does not stop trading, but it weakens audit
  completeness.
- The Python feed is synthetic and the EA-to-Python live telemetry path is not
  confirmed. Keep `InpRequirePythonSignal=false`.
- The EA compile-time defaults for partial close and break-even at +1.5R are not
  the tested configuration (see section 6.3).
- No git repository was initialized at the time of this check; the research
  baseline should be committed so negative results are preserved.

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

## 8. Next actions (priority order)

1. **Freeze the current intraday EA.** Independent HistData validation confirms
   that H1 trend, H1 mean reversion, D1 trend, D1 momentum, and the EA's own M5
   hour-7 breakout have no durable edge. The 2025 M5 holdout was flat and the
   independent M5 check is negative in design, validation, and OOS. Do not paper
   over a missing edge with more filters, exits, or parameter sweeps on the same
   families.
2. **Independent validation is complete for H1/D1/COT/carry and the M5 setup.**
   The negative results were not a Deriv-Demo data artifact; HistData reproduces
   them. If a new data source or family is tested, keep the same chronological
   design / validation / OOS discipline and write results to a separate
   `SOVEREIGN_RESULTS_DIR`.
3. **Choose the next path deliberately:**
   - Build a **separate monthly carry sleeve** around the validated
     carry + 12-month momentum signal. It must be multi-symbol,
     volatility-targeted, and include a causal crash filter that is positive in
     design, validation, and OOS. The current +2.1% CAGR / -20.7% DD is not
     enough for the stated goal. Do not force this into the M5 EA.
   - Or pivot to a genuinely different edge/data source (options skew, order
     flow, policy surprises, real macro). More price-only FX indicator mining
     is low-probability.
   - Or freeze the trading project and keep the EA as infrastructure only.
4. If the carry sleeve is pursued, **first download HistData 2005-2009** and
   rerun carry on 2005-2025. The current independent slice starts in 2010 and
   misses the 2008 GFC, which is the worst carry crash. On the full 2005-2026
   Deriv history the dollar-neutral variant already fails validation (-0.07%)
   with a -38.37% max DD, and the base variant has a -26.30% max DD. Require a
   causal crash filter validated in design, validation, and OOS. The VIX
   throttle is not sufficient; it only moves the base max DD from -20.7% to
   -16.7% while cutting OOS CAGR from +3.35% to +1.47%. Do not call the current
   result profitable enough to deploy.
5. Keep all research scripts and result CSVs under `research/` so negative
   results are reproducible. Never delete a failed test; it prevents repeat
   work.
6. Add `requests` to `requirements.txt` for the HistData downloader (required
   on a fresh machine).
7. Update `scripts/install_ea.ps1` stale warning.
8. Write `docs/DEPLOYMENT.md` only after a deployable edge exists. It should
   cover VPS, firewall, demo incubation, and a migration checklist.
9. `git init` and commit the research baseline, including the negative results.

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

### 2026-10-27

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
  The 2010-2025 independent slice misses the 2008 GFC, so the neutral variant is
  not a deployable improvement. The base carry survives full Deriv
  design+validation only barely (+1.32% / +0.05%) with a -26.30% max DD.
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
