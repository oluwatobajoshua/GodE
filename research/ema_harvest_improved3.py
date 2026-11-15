#!/usr/bin/env python3
"""EMA harvest monthly momentum filter — skip entries in down months.

Test: only allow long entries when prior month's close > prior month open
(month was up). This avoids entering new longs in bear-market months while
still letting existing positions run.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_improved3"
OUT.mkdir(parents=True, exist_ok=True)

from period_returns import publish_results  # noqa: E402


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
    o = df["open"].astype(float)
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
    monthly_momentum = params["monthly_momentum"]  # skip new longs if prior month down

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()

    # Monthly up/down flag: True if month close > month open
    monthly = df.resample("ME").agg({"open": "first", "close": "last"})
    monthly["up_month"] = monthly["close"] > monthly["open"]
    monthly_map = monthly["up_month"]

    side = 0
    entry_i = -1
    entry_px = 0.0
    high_px = 0.0
    low_px = 0.0
    consec_long = 0
    consec_short = 0

    trade_rets = []
    trade_times = []

    start_i = max(params["ema_period"], params["trend_period"]) + 10

    for i in range(start_i, n - 1):
        if side == 0:
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

            # Monthly momentum: skip new longs if prior month was down
            if long_allowed and monthly_momentum:
                cur_month = df.index[i].to_period("M")
                prev_month = cur_month - 1
                if prev_month in monthly_map.index:
                    if not monthly_map.loc[prev_month]:
                        long_allowed = False

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
        "monthly_momentum": False,
    }

    print("\n--- Baseline (long_only_above EMA100, no momentum filter) ---")
    p0 = dict(base_params)
    rets0 = simulate_harvest(df, p0)
    total0 = float(np.prod(1 + rets0) - 1) * 100
    pf0 = rets0[rets0 > 0].sum() / abs(rets0[rets0 < 0].sum())
    print(f"total={total0:.2f}% pf={pf0:.2f} n={len(rets0)}")

    print("\n--- With monthly momentum filter (skip new longs if prior month down) ---")
    p1 = dict(base_params, monthly_momentum=True)
    rets1 = simulate_harvest(df, p1)
    total1 = float(np.prod(1 + rets1) - 1) * 100
    pf1 = rets1[rets1 > 0].sum() / abs(rets1[rets1 < 0].sum()) if (rets1 < 0).any() else 999
    print(f"total={total1:.2f}% pf={pf1:.2f} n={len(rets1)}")

    # Monthly report for momentum version
    rets1 = rets1.sort_index()
    publish_results("ema_harvest_monthly_momentum", rets1, last_months=24)

    # Year summary
    tab = rets1.to_frame("ret")
    tab["date"] = tab.index
    monthly = tab.groupby(tab.index.to_period("M")).agg(
        return_pct=("ret", lambda x: float(np.prod(1 + x) - 1) * 100),
        trades=("ret", "count"),
    )
    monthly.index = monthly.index.to_timestamp("M")
    monthly["win_period"] = monthly["return_pct"] > 0
    monthly = monthly.reset_index().rename(columns={"index": "date"})
    monthly["year"] = pd.to_datetime(monthly["date"]).dt.year
    g = monthly.groupby("year").agg(
        months=("return_pct", "count"),
        win_months=("win_period", "sum"),
        total=("return_pct", "sum"),
        trades=("trades", "sum"),
        worst=("return_pct", "min"),
    )
    neg_years = g[g["total"] < 0]
    print(f"\nYears: {len(g)}, Negative years: {len(neg_years)} {neg_years.index.tolist()}")
    print(f"Worst year: {g['total'].min():.2f}%  Best year: {g['total'].max():.2f}%")
    print(f"Win months: {100*g['win_months'].sum()/g['months'].sum():.1f}%")
    monthly.to_csv(OUT / "ema_harvest_monthly_momentum_monthly.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
