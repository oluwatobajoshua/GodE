#!/usr/bin/env python3
"""Comprehensive EMA harvest improvement sweep — focused on untested dimensions.

Tested previously: EMA period, trend filter, trail ATR, RSI exit, max hold, RSI entry.
Untested to test now:
  - Volatility filter: ATR p80 gate
  - Session filter: London/NY/Asian/all
  - Short side improvements
  - Min consecutive closes interaction

Focus: BTCUSD H1, EMA=2 base.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_all_dims"
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
    enable_short = params["enable_short"]
    rsi_entry_max = params["rsi_entry_max"]
    trend_filter = params["trend_filter"]
    rsi_exit_long = params["rsi_exit_long"]
    rsi_exit_short = params["rsi_exit_short"]
    max_hold = params["max_hold"]
    trail_atr = params["trail_atr"]
    vol_filter = params["vol_filter"]
    session = params["session"]

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()
    atr_p80 = atr.rolling(500).quantile(0.80)

    hour_array = list(df.index.hour)

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
            rsi_i = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
            atr_i = float(atr.iloc[i])
            atr_p80_i = float(atr_p80.iloc[i]) if pd.notna(atr_p80.iloc[i]) else float("inf")
            hour = int(hour_array[i])

            if session == "london" and not (8 <= hour < 17):
                continue
            if session == "ny" and not (13 <= hour < 22):
                continue
            if session == "asian" and not (0 <= hour < 8):
                continue

            if vol_filter and atr_i > atr_p80_i:
                continue

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

            if long_allowed and rsi_entry_max < 100 and rsi_i >= rsi_entry_max:
                long_allowed = False
            if short_allowed and rsi_entry_max < 100 and rsi_i <= (100 - rsi_entry_max):
                short_allowed = False

            if (long_allowed and consec_long >= min_consec_long
                    and i >= 1 and c.iloc[i - 1] <= e.iloc[i - 1]):
                side = 1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                high_px = entry_px
                low_px = entry_px
                consec_long = 0
                consec_short = 0
                continue

            if (enable_short and short_allowed and consec_short >= min_consec_short
                    and i >= 1 and c.iloc[i - 1] >= e.iloc[i - 1]):
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
            c_i = float(c.iloc[i])
            bars_held = i - entry_i

            rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0

            if side > 0 and rv >= rsi_exit_long:
                exit_now = True
            if side < 0 and rv <= rsi_exit_short:
                exit_now = True

            if not exit_now and trail_atr > 0.0 and bars_held > 0:
                a = float(atr.iloc[entry_i]) if pd.notna(atr.iloc[entry_i]) else 0.001
                if side > 0:
                    trail_px = high_px - trail_atr * a
                    if c_i <= trail_px:
                        exit_now = True
                else:
                    trail_px = low_px + trail_atr * a
                    if c_i >= trail_px:
                        exit_now = True

            if not exit_now and max_hold > 0 and bars_held >= max_hold:
                exit_now = True

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


def year_negatives(rets: pd.Series) -> list:
    tab = rets.to_frame("ret")
    tab["date"] = tab.index
    monthly = tab.groupby(tab.index.to_period("M")).agg(
        return_pct=("ret", lambda x: float(np.prod(1 + x) - 1) * 100),
    )
    monthly.index = monthly.index.to_timestamp("M")
    monthly["year"] = monthly.index.year
    g = monthly.groupby("year")["return_pct"].sum()
    return g[g < 0].index.tolist()


def main() -> int:
    df = load_csv("BTCUSD", "H1")
    print(f"=== BTCUSD H1 ({len(df)} bars) ===")

    results = []

    # Focused grid — EMA=2 fixed (best from sweep), test untested dims
    ema_periods = [2]
    min_consec_longs = [1, 2, 3]
    rsi_entry_maxes = [70, 100]
    trend_filters = ["none", "long_only_above"]
    rsi_exit_longs = [70, 80]
    max_holds = [24, 48]
    trail_atrs = [0.0, 2.0]
    enable_shorts = [False]  # shorts proven weak
    min_consec_shorts = [1]
    vol_filters = [False, True]
    sessions = ["all", "london", "ny"]

    count = 0
    for ep in ema_periods:
        for mcl in min_consec_longs:
            for rem in rsi_entry_maxes:
                for tf in trend_filters:
                    for rel in rsi_exit_longs:
                        for mh in max_holds:
                            for ta in trail_atrs:
                                for vf in vol_filters:
                                    for ss in sessions:
                                        p = {
                                            "ema_period": ep,
                                            "min_consec_long": mcl,
                                            "min_consec_short": 1,
                                            "enable_short": False,
                                            "rsi_entry_max": rem,
                                            "trend_filter": tf,
                                            "rsi_exit_long": rel,
                                            "rsi_exit_short": 20,
                                            "max_hold": mh,
                                            "trail_atr": ta,
                                            "vol_filter": vf,
                                            "session": ss,
                                            "trend_period": 100,
                                        }
                                        rets = simulate_harvest(df, p)
                                        if len(rets) < 10:
                                            continue
                                        total = float(np.prod(1 + rets) - 1) * 100
                                        wins = rets[rets > 0]
                                        losses = rets[rets < 0]
                                        pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 999
                                        winrate = 100 * (rets > 0).mean()
                                        n_profitable = int((rets > 0).sum())
                                        n_total = len(rets)
                                        neg_years = year_negatives(rets)
                                        count += 1
                                        results.append({
                                            "ema_period": ep, "min_consec_long": mcl,
                                            "rsi_entry_max": rem, "trend_filter": tf,
                                            "rsi_exit_long": rel, "max_hold": mh,
                                            "trail_atr": ta, "vol_filter": vf,
                                            "session": ss,
                                            "total_pct": round(total, 2), "pf": round(pf, 2),
                                            "winrate": round(winrate, 1),
                                            "n_profitable": n_profitable,
                                            "n_total": n_total,
                                            "neg_years": str(neg_years),
                                        })

    print(f"Total configs tested: {count}")
    results_df = pd.DataFrame(results)
    results_df.to_csv(OUT / "all_dims_sweep.csv", index=False)

    good = results_df[results_df["pf"] >= 1.2]
    print(f"\n=== {len(good)} configs PF>=1.2, sorted by profitable trades ===")
    top = good.sort_values("n_profitable", ascending=False).head(30)
    print(top.to_string(index=False))

    print(f"\n=== {len(good)} configs PF>=1.2, sorted by total return ===")
    top2 = good.sort_values("total_pct", ascending=False).head(15)
    print(top2.to_string(index=False))

    no_neg = good[good["neg_years"] == "[]"]
    if len(no_neg):
        best = no_neg.sort_values("n_profitable", ascending=False).iloc[0]
        print(f"\n=== BEST ZERO NEG YEARS: {best.to_dict()} ===")
    else:
        best_pool = good[good["pf"] >= 1.3]
        if len(best_pool):
            best = best_pool.sort_values("n_profitable", ascending=False).iloc[0]
        else:
            best = good.sort_values("n_profitable", ascending=False).iloc[0]
        print(f"\n=== BEST OVERALL: {best.to_dict()} ===")

    p = {
        "ema_period": int(best["ema_period"]),
        "min_consec_long": int(best["min_consec_long"]),
        "rsi_entry_max": int(best["rsi_entry_max"]),
        "trend_filter": best["trend_filter"],
        "rsi_exit_long": int(best["rsi_exit_long"]),
        "max_hold": int(best["max_hold"]),
        "trail_atr": float(best["trail_atr"]),
        "vol_filter": bool(best["vol_filter"]),
        "session": best["session"],
        "trend_period": 100,
    }
    rets_best = simulate_harvest(df, p).sort_index()
    publish_results("ema_harvest_all_dims_best", rets_best, last_months=24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
