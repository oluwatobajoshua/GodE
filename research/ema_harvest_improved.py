#!/usr/bin/env python3
"""EMA harvest improvement — trend filters to cut bear-market losses.

Tests EMA=2 harvest on BTCUSD H1 with optional trend filters:
  - trend_ema: only trade long when close > trend_ema, only short when close < trend_ema
  - trend_period: EMA periods to test for the trend filter

Goal: eliminate 2018 (-82.8%) and 2022 (-53.8%) drawdown years while keeping the edge.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_improved"
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
    trend_filter = params["trend_filter"]  # 'none', 'long_only_above', 'both_sides'

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

            #--- trend filter
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


def year_summary(df_monthly: pd.DataFrame) -> dict:
    df_monthly = df_monthly.copy()
    df_monthly["year"] = pd.to_datetime(df_monthly["date"]).dt.year
    g = df_monthly.groupby("year").agg(
        months=("return_pct", "count"),
        win_months=("win_period", "sum"),
        total=("return_pct", "sum"),
        trades=("trades", "sum"),
        worst=("return_pct", "min"),
        best=("return_pct", "max"),
    )
    neg_years = g[g["total"] < 0]
    return {
        "n_years": len(g),
        "neg_years": len(neg_years),
        "neg_year_list": neg_years.index.tolist(),
        "worst_year": g["total"].min(),
        "best_year": g["total"].max(),
        "total": g["total"].sum(),
        "win_months_pct": round(100 * g["win_months"].sum() / g["months"].sum(), 1),
    }


def main() -> int:
    df = load_csv("BTCUSD", "H1")
    print(f"=== BTCUSD H1 ({len(df)} bars, {df.index[0]} to {df.index[-1]}) ===")

    base_params = {
        "ema_period": 2,
        "min_consec_long": 1,
        "min_consec_short": 3,
        "exit_on_ema": False,
        "rsi_exit_long": 80,
        "rsi_exit_short": 20,
        "max_hold": 48,
        "trail_atr": 0.0,
    }

    # Baseline: no trend filter
    print("\n--- Baseline: no trend filter ---")
    p0 = dict(base_params, trend_period=200, trend_filter="none")
    rets0 = simulate_harvest(df, p0)
    total0 = float(np.prod(1 + rets0) - 1) * 100
    print(f"total={total0:.2f}% n={len(rets0)}")

    # Test trend filters
    trend_periods = [50, 100, 200]
    trend_filters = ["long_only_above", "both_sides"]

    results = []
    for tf in trend_filters:
        for tp in trend_periods:
            p = dict(base_params, trend_period=tp, trend_filter=tf)
            rets = simulate_harvest(df, p)
            if len(rets) == 0:
                continue
            total = float(np.prod(1 + rets) - 1) * 100
            wins = rets[rets > 0]
            losses = rets[rets < 0]
            pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
            winrate = 100 * (rets > 0).mean()
            results.append({
                "trend_filter": tf, "trend_period": tp,
                "total_pct": round(total, 2), "pf": round(pf, 2),
                "winrate": round(winrate, 1), "n_trades": len(rets),
            })
            print(f"{tf:18s} EMA_trend={tp:4d}  total={total:>10.2f}%  pf={pf:.2f}  win={winrate:5.1f}%  n={len(rets)}")

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUT / "trend_filter_comparison.csv", index=False)

    # Best config: run full monthly report
    best = results_df.sort_values("total_pct", ascending=False).iloc[0]
    print(f"\n=== BEST: {best['trend_filter']} trend_period={best['trend_period']} ===")
    p_best = dict(base_params, trend_period=int(best["trend_period"]), trend_filter=best["trend_filter"])
    rets_best = simulate_harvest(df, p_best)
    rets_best = rets_best.sort_index()

    total_best = float(np.prod(1 + rets_best) - 1) * 100
    wins = rets_best[rets_best > 0]
    losses = rets_best[rets_best < 0]
    pf_best = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    print(f"total={total_best:.2f}% pf={pf_best:.2f} winrate={100*(rets_best>0).mean():.1f}% n={len(rets_best)}")

    publish_results(f"ema_harvest_improved_{best['trend_filter']}_{int(best['trend_period'])}", rets_best, last_months=24)

    # Monthly CSV for the best config
    tab = rets_best.to_frame("ret")
    tab["date"] = tab.index
    monthly = tab.groupby(tab.index.to_period("M")).agg(
        return_pct=("ret", lambda x: float(np.prod(1 + x) - 1) * 100),
        trades=("ret", "count"),
    )
    monthly.index = monthly.index.to_timestamp("M")
    monthly["win_period"] = monthly["return_pct"] > 0
    monthly = monthly.reset_index().rename(columns={"index": "date"})
    monthly.to_csv(OUT / f"ema_harvest_{best['trend_filter']}_{int(best['trend_period'])}_monthly.csv", index=False)

    ys = year_summary(monthly)
    print(f"\nYears: {ys['n_years']}, Negative years: {ys['neg_years']} {ys['neg_year_list']}")
    print(f"Worst year: {ys['worst_year']:.2f}%  Best year: {ys['best_year']:.2f}%  Total: {ys['total']:.2f}%")
    print(f"Win months: {ys['win_months_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
