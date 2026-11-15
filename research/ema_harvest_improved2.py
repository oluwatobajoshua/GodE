#!/usr/bin/env python3
"""EMA harvest profitability improvements — test risk controls.

Tests on BTCUSD H1, EMA=2 base (best from sweep):
  - ATR trailing stop (0, 1.0, 1.5, 2.0)
  - Exit on EMA break (true/false)
  - Circuit breaker: pause K months after cumulative monthly loss > threshold
  - Monthly momentum: skip if prior month return < 0
  - Risk scaling: halve risk after 2 consecutive losing months
  - Trend filter: long-only-above EMA100

Goal: eliminate negative years while keeping edge.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_improved2"
OUT.mkdir(parents=True, exist_ok=True)

from period_returns import publish_results, to_monthly  # noqa: E402


def load_csv(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    if not p.exists():
        p = DATA / "mt5" / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def simulate_harvest(df: pd.DataFrame, params: dict) -> pd.Series:
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    e = ema(c, params["ema_period"])
    r = rsi(c)
    trend_e = ema(c, params["trend_period"])
    n = len(df)

    min_consec_long = params["min_consec_long"]
    min_consec_short = params["min_consec_short"]
    exit_on_ema = params["exit_on_ema"]
    rsi_exit_long = params["rsi_exit_long"]
    rsi_exit_short = params["rsi_exit_short"]
    max_hold = params["max_hold"]
    trail_atr = params["trail_atr"]
    trend_filter = params["trend_filter"]
    circuit_breaker = params["circuit_breaker"]  # months pause after DD threshold
    circuit_threshold = params["circuit_threshold"]  # max drawdown pct before pause

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()

    side = 0
    entry_i = -1
    entry_px = 0.0
    high_px = 0.0
    low_px = 0.0
    consec_long = 0
    consec_short = 0

    trade_rets = []
    trade_times = []

    # circuit breaker state
    paused_until = None  # bar index until which we pause entries
    equity = 1.0
    peak_equity = 1.0
    monthly_rets = []  # list of (period, return) for circuit breaker

    start_i = max(params["ema_period"], params["trend_period"]) + 10

    for i in range(start_i, n - 1):
        if side == 0:
            #--- circuit breaker: check if paused
            if paused_until is not None and i >= paused_until:
                paused_until = None

            if c.iloc[i] > e.iloc[i]:
                consec_long += 1
                consec_short = 0
            elif c.iloc[i] < e.iloc[i]:
                consec_short += 1
                consec_long = 0
            else:
                consec_long = 0
                consec_short = 0

            c_i = float(c.iloc[i])
            te_i = float(trend_e.iloc[i])

            long_allowed = True
            short_allowed = True
            if trend_filter == "long_only_above":
                short_allowed = False
                if c_i <= te_i:
                    long_allowed = False
            elif trend_filter == "both_sides":
                if c_i <= te_i:
                    long_allowed = False
                if c_i >= te_i:
                    short_allowed = False

            if paused_until is None:
                if long_allowed and consec_long >= min_consec_long and i >= 1 and c.iloc[i - 1] <= e.iloc[i - 1]:
                    side = 1
                    entry_i = i
                    entry_px = float(c.iloc[i + 1])
                    high_px = entry_px
                    low_px = entry_px
                    consec_long = 0
                    consec_short = 0
                    continue

                if short_allowed and consec_short >= min_consec_short and i >= 1 and c.iloc[i - 1] >= e.iloc[i - 1]:
                    side = -1
                    entry_i = i
                    entry_px = float(c.iloc[i + 1])
                    high_px = entry_px
                    low_px = entry_px
                    consec_long = 0
                    consec_short = 0
                    continue

        else:
            if side > 0:
                high_px = max(high_px, float(h.iloc[i]))
                low_px = min(low_px, float(l.iloc[i]))
            else:
                high_px = max(high_px, float(h.iloc[i]))
                low_px = min(low_px, float(l.iloc[i]))

            exit_now = False
            exit_reason = ""

            c_i = float(c.iloc[i])
            bars_held = i - entry_i

            if exit_on_ema:
                if side > 0 and c_i < float(e.iloc[i]):
                    exit_now = True
                    exit_reason = "ema_break"
                if side < 0 and c_i > float(e.iloc[i]):
                    exit_now = True
                    exit_reason = "ema_break"

            rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
            if side > 0 and rv >= rsi_exit_long:
                exit_now = True
                exit_reason = "rsi_extreme"
            if side < 0 and rv <= rsi_exit_short:
                exit_now = True
                exit_reason = "rsi_extreme"

            if trail_atr > 0 and bars_held > 0:
                a = float(atr.iloc[entry_i]) if pd.notna(atr.iloc[entry_i]) else 0.001
                if side > 0:
                    trail_px = high_px - trail_atr * a
                    if c_i <= trail_px:
                        exit_now = True
                        exit_reason = "trail_stop"
                else:
                    trail_px = low_px + trail_atr * a
                    if c_i >= trail_px:
                        exit_now = True
                        exit_reason = "trail_stop"

            if bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                exit_px = c_i
                if side > 0:
                    gross = (exit_px - entry_px) / entry_px
                else:
                    gross = (entry_px - exit_px) / entry_px
                trade_rets.append(gross)
                trade_times.append(df.index[i])
                equity *= (1 + gross)
                peak_equity = max(peak_equity, equity)

                #--- circuit breaker: check drawdown from peak
                dd = (peak_equity - equity) / peak_equity * 100.0
                if circuit_breaker > 0 and dd >= circuit_threshold and paused_until is None:
                    paused_until = i + circuit_breaker * 24 * 30  # K months of bars (approx)
                    print(f"  CIRCUIT BREAKER triggered at bar {i} (DD={dd:.1f}%), pause {circuit_breaker} months")

                side = 0

    if side != 0 and entry_i >= 0:
        exit_px = float(c.iloc[n - 1])
        if side > 0:
            gross = (exit_px - entry_px) / entry_px
        else:
            gross = (entry_px - exit_px) / entry_px
        trade_rets.append(gross)
        trade_times.append(df.index[n - 1])

    return pd.Series(trade_rets, index=pd.DatetimeIndex(trade_times))


def main() -> int:
    df = load_csv("BTCUSD", "H1")
    print(f"=== BTCUSD H1 ({len(df)} bars) ===")

    base_params = {
        "ema_period": 2,
        "min_consec_long": 1,
        "min_consec_short": 3,
        "exit_on_ema": False,
        "rsi_exit_long": 80,
        "rsi_exit_short": 20,
        "max_hold": 48,
        "trail_atr": 0.0,
        "trend_period": 100,
        "trend_filter": "long_only_above",
        "circuit_breaker": 0,
        "circuit_threshold": 0.0,
    }

    # Baseline: best from previous test
    print("\n--- Baseline (long_only_above EMA100, no trail, no circuit) ---")
    p0 = dict(base_params)
    rets0 = simulate_harvest(df, p0)
    total0 = float(np.prod(1 + rets0) - 1) * 100
    pf0 = rets0[rets0 > 0].sum() / abs(rets0[rets0 < 0].sum())
    print(f"total={total0:.2f}% pf={pf0:.2f} n={len(rets0)}")

    # Sweep improvements
    results = []

    # 1. ATR trailing stop
    for trail in (1.0, 1.5, 2.0, 3.0):
        p = dict(base_params, trail_atr=trail)
        rets = simulate_harvest(df, p)
        total = float(np.prod(1 + rets) - 1) * 100
        pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if (rets < 0).any() else 999
        results.append({"config": f"trail_atr={trail}", "total_pct": round(total, 2), "pf": round(pf, 2), "n": len(rets)})
        print(f"trail_atr={trail:4.1f}  total={total:>10.2f}%  pf={pf:.2f}  n={len(rets)}")

    # 2. Exit on EMA break
    for exit_ema in (True, False):
        p = dict(base_params, exit_on_ema=exit_ema)
        rets = simulate_harvest(df, p)
        total = float(np.prod(1 + rets) - 1) * 100
        pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if (rets < 0).any() else 999
        results.append({"config": f"exit_on_ema={exit_ema}", "total_pct": round(total, 2), "pf": round(pf, 2), "n": len(rets)})
        print(f"exit_on_ema={str(exit_ema):5s}  total={total:>10.2f}%  pf={pf:.2f}  n={len(rets)}")

    # 3. Circuit breaker
    for cb_months in (3, 6, 12):
        for cb_thresh in (20.0, 30.0, 40.0):
            p = dict(base_params, circuit_breaker=cb_months, circuit_threshold=cb_thresh)
            rets = simulate_harvest(df, p)
            total = float(np.prod(1 + rets) - 1) * 100
            pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if (rets < 0).any() else 999
            results.append({"config": f"cb={cb_months}m@{cb_thresh}%", "total_pct": round(total, 2), "pf": round(pf, 2), "n": len(rets)})
            print(f"circuit={cb_months}m@{cb_thresh}%  total={total:>10.2f}%  pf={pf:.2f}  n={len(rets)}")

    # 4. Combined best: trail_atr=1.5 + circuit breaker 6m@30%
    p = dict(base_params, trail_atr=1.5, circuit_breaker=6, circuit_threshold=30.0)
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if (rets < 0).any() else 999
    results.append({"config": "trail1.5+cb6m@30%", "total_pct": round(total, 2), "pf": round(pf, 2), "n": len(rets)})
    print(f"trail1.5+cb6m@30%  total={total:>10.2f}%  pf={pf:.2f}  n={len(rets)}")

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUT / "improvement_sweep.csv", index=False)

    # Full monthly report for the combined best
    print(f"\n=== COMBINED BEST: trail_atr=1.5 + circuit breaker 6m@30% ===")
    rets_best = simulate_harvest(df, p)
    rets_best = rets_best.sort_index()
    total_best = float(np.prod(1 + rets_best) - 1) * 100
    pf_best = rets_best[rets_best > 0].sum() / abs(rets_best[rets_best < 0].sum())
    print(f"total={total_best:.2f}% pf={pf_best:.2f} winrate={100*(rets_best>0).mean():.1f}% n={len(rets_best)}")

    publish_results("ema_harvest_improved_trail_cb", rets_best, last_months=24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
