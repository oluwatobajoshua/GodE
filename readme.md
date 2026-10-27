# Project Sovereign: God's Eye Institutional Hybrid Algorithmic Trading Ecosystem

> **2026-10-27 status: no deployable edge. Do not deploy live.**
> Independent HistData 2005-2025 validation confirms that H1 trend, H1 mean
> reversion, D1 trend, D1 momentum, CFTC COT positioning, and the EA's own M5
> breakout and mean-reversion engines all fail a chronological out-of-sample
> check. The M5 breakout is negative in design, validation, and OOS
> (**-355.79R full 2011-2025, PF 0.75**); the M5 mean-reversion engine is also
> negative (**-4,078.37R, PF 0.75**, no positive year). The 2025 holdout was
> +0.67% and the 2026 in-sample result was a regime artifact. The only survivor
> is monthly **carry + 12-month momentum**: +1.64% CAGR, Sharpe 0.20, -26.03%
> max drawdown on independent 2005-2025 data (peak 2007-06 to trough 2012-10).
> The independent series matches the full Deriv 2005-2026 result (+1.61% CAGR,
> -26.30% DD, same dates), so the weak premium and its drawdown are real. A
> dollar-neutral variant is +2.06% CAGR / -39.02% DD; adding a VIX overlay gives
> +2.08% CAGR / -33.32% DD. Strategy-trend, faster-vol, and VIX overlays do not
> improve the risk-adjusted result enough. A return decomposition shows the
> carry return is almost entirely interest carry, not a durable spot edge. Carry
> is not an intraday EA strategy, is still crash-prone, and is not enough for the
> stated goal. **Freeze the intraday EA**; do not deploy the carry sleeve. See
> `docs/HANDOFF.md` sections 6.3-6.4 before changing any parameter or running the
> EA on a live account.

Welcome to the official technical documentation and deployment guide for **Project Sovereign**, an institutional-grade, hybrid algorithmic trading engine that bridges high-performance MQL5 execution with external Python-driven machine learning and volume profile analytics.

---

## 0. Deployment Status (verified 2026-10-26)

| Component | Status |
|---|---|
| `python/institutional_quant_master.py` | Runs on Python 3.14.7; publishes JSON on `tcp://127.0.0.1:5555` |
| `EA/SovereignGodsEyeEA.mq5` | Compiles clean (0 errors, 0 warnings) with MetaEditor 5 |
| Python dependencies | Bootstrapped in `.venv/` from `requirements.txt` |
| Native TCP NDJSON transport | Implemented on `127.0.0.1:5556`; the EA falls back to autonomous mode if the heartbeat is stale > 3 s |
| Python → EA signal consumption | Optional trade gate implemented; disabled (`InpRequirePythonSignal=false`) because the Python feed is synthetic |
| Python input data | **Synthetic** random-walk bars; real MT5 tick ingestion is not implemented |
| Backtests / live incubation | Backtests run; no deployable edge; no live incubation |

> The analytics logic, execution engine, and native TCP telemetry are present,
> but the Python feed is synthetic and the signal gate is disabled. Treat the
> system as research/backtest infrastructure, not live-ready, until a validated
> edge and a real causal data feed exist.

---

## 1. Executive Summary & Architecture

Project Sovereign decouples execution risk from analytical complexity by splitting the system into two distinct operational domains. Python publishes analytics on **ZeroMQ (TCP/5555)**, and the EA consumes a native **TCP NDJSON telemetry feed (127.0.0.1:5556)** with autonomous fallback. The Python feed is still synthetic; see the deployment status above.

* **The Brain (Python / ZeroMQ):** Handles advanced data science, rolling structural metrics, volume profile Point of Control (POC) calculations, volatility regime detection, and external analytics streaming.
* **The Muscle (MQL5):** Handles sub-millisecond trade execution, continuous capacity lot sizing, hard circuit breakers, slippage guards, timezone-normalized killzones, and native economic news blackouts.
* **The Infrastructure:** Leverages MT5 genetic optimization, tick-level backtesting data, institutional ECN liquidity, and low-latency VPS co-location.

---

## 2. Key Institutional Features

1. **Continuous Capacity-Optimized Sizing ($10 to Institutional Scale):**
* Automatically scales risk dynamically based on current equity.
* Features a built-in **Micro-Account Floor** that safely accommodates pocket-change ECN accounts (e.g., $10 deposits) by flooring orders at `0.01` lots while enforcing strict margin capacity checks to prevent margin calls.


2. **Automatic Dual-Mode Execution:**
* Inspects recent candle anatomy and rejection wicks in real time.
* Automatically routes trades between patient **Pending Limit Orders** (zero slippage at structural extremes) and aggressive **Instant Market Orders** (high-velocity liquidity sweeps).


3. **Volume Profile & POC Integration:**
* Calculates price-volume distribution to intercept price reacting off High Volume Nodes (HVNs) rather than arbitrary high/low boundaries.


4. **Multi-Timeframe Structure Confluence:**
* Forces H1 moving average and market structure alignment before any M5 execution trigger is validated.


5. **Volatility Regime Switching:**
* Dynamically adjusts stop-loss buffers and risk scaling based on rolling ATR expansion and compression ratios.


6. **Heartbeat & Failover Watchdog:**
* Monitors the native TCP analytics heartbeat. If the Python analytics brain stops publishing for more than 3 seconds, the EA falls back to **Pure-MQL5 Autonomous Mode**. The EA currently runs in autonomous mode for entries because the Python signal gate is disabled (`InpRequirePythonSignal=false`).



---

## 3. Repository Structure & Files

* `python/institutional_quant_master.py` — The Python ZeroMQ analytics and feature engineering engine.
* `EA/SovereignGodsEyeEA.mq5` — The master MQL5 Expert Advisor handling risk, order routing, and trade management.
* `requirements.txt` — Python dependency ranges.
* `scripts/start_brain.ps1` — Launches the Python brain using the local virtual environment.
* `scripts/healthcheck.py` — Subscribes to the feed and validates the heartbeat/analytics payload schema.
* `scripts/install_ea.ps1` — Copies the EA into the detected MT5 data folder and compiles it with MetaEditor.
* `.venv/` — Local Python environment (created by setup, ignored by git).

---

## 4. Prerequisites & Environment Setup

### Software Requirements

* **MetaTrader 5 (MT5)** terminal provided by a true ECN / Raw Spread broker (e.g., IC Markets or Pepperstone). Avoid market-maker (B-Book) brokers like Deriv.
* **Python 3.10+** environment with core analytical libraries installed.

### Python Dependencies

Run the following command in your terminal to install the required socket and data science packages:

```bash
pip install -r requirements.txt
```

A reproducible local environment is already provided in `.venv/`. To rebuild it from scratch:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### Automated Setup (Windows)

```powershell
# 1. Start the Python brain (uses .venv automatically)
.\scripts\start_brain.ps1

# 2. In a second terminal, verify the feed/schema
.\.venv\Scripts\python.exe .\scripts\healthcheck.py

# 3. Install + compile the EA into your detected MT5 data folder
.\scripts\install_ea.ps1
```

The health check exits `0` only after receiving and schema-validating three payloads.

---

## 5. Step-by-Step Deployment Guide

### Step 1: Launch the Python Quant Brain

1. Run the brain (it lives in `python/institutional_quant_master.py`):
```powershell
.\scripts\start_brain.ps1
```
2. Verify that the console initializes successfully on `tcp://127.0.0.1:5555` and begins streaming analytics and heartbeat packets. In a second terminal:
```powershell
.\.venv\Scripts\python.exe .\scripts\healthcheck.py
```

### Step 2: Compile the MQL5 Expert Advisor

1. Run:
```powershell
.\scripts\install_ea.ps1
```
This copies `EA/SovereignGodsEyeEA.mq5` to `MQL5\Experts\Sovereign\` in the detected terminal data folder, compiles it with `MetaEditor64.exe`, and verifies `0 errors`.
2. Alternatively, open the file in **MetaEditor** (`F4`) and press **Compile (`F7`)**.
3. In MT5, enable **Algo Trading** and attach `SovereignGodsEyeEA` to an **M5** chart.

### Step 3: Strategy Tester Validation (`Ctrl + R`)

1. Press `Ctrl + R` in MT5 to open the Strategy Tester.
2. Select `SovereignGodsEyeEA.mq5`, choose your target ECN symbol (e.g., `EURUSD`), and set the timeframe to **M5**.
3. Set modeling to **"Every tick based on real ticks"**.
4. Run your backtest to inspect the equity curve, profit factor, and drawdown metrics.

### Step 4: The 30-Day Incubation Protocol

1. Provision a low-latency MetaTrader VPS co-located in institutional data centers (Equinix LD4 London or NY4 New York).
2. Attach the EA to your live chart.
3. Deploy on a micro-funded live ECN account or ECN demo account ($10 to $500 starting balance at 0.01 initial lot sizing) for a mandatory **30-day forward incubation period** before scaling capital.

---

## 6. Known Gaps Before Live Deployment

1. **ZeroMQ transport and signal consumption in the EA.** `SovereignGodsEyeEA.mq5` has no ZeroMQ client and never reads the Python payload. `lastZmqHeartbeatTime` is never updated, so `isZmqConnected` remains `false` and the EA always uses autonomous fallback. Options:
   * Integrate the `mql-zmq` wrapper (`libzmq.dll` + `MqlZmq.mqh`) as a SUB socket; requires enabling DLL imports in MT5.
   * Implement a native MQL5 socket bridge (no DLL) and mirror it in the Python publisher.
   * Use a resilient file/HTTP heartbeat bridge between the Python process and the EA.
   * In all cases, define a validated signal contract (action, confidence, POC, expiry, symbol) and gate entries on it.
2. **Synthetic analytics input.** The Python engine generates random-walk bars. For production it must ingest MT5 ticks/bars (e.g., via ZeroMQ request/reply, a database, or exported CSV) and publish the resulting features.
3. **Sizing risk on micro accounts.** The `0.01` minimum-lot floor can exceed the configured 1% risk on a $10–$500 account. The EA logs the floor event, but the resulting risk is broker-minimum driven, not equity driven. Validate on demo before micro-live.
4. **Immediate stop modification.** Break-even/partial-close logic uses immediate `PositionModify`; brokers with a `SYMBOL_TRADE_STOPS_LEVEL` or freeze level may reject it. Add stops-level normalization before deployment.
5. **No audited backtest yet.** Run Strategy Tester with real ticks and archive the report before incubation.

---

Would you like to review specific input parameters for your EUR/USD optimization run, or help setting up automated CSV execution logging for slippage auditing?

* Optimizing Strategy Tester inputs for EUR/USD
* Setting up automated trade and slippage auditing logs
* Deploying to a remote VPS environment