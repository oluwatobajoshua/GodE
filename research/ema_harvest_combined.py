#!/usr/bin/env python3
"""EMA harvest combined sweep — trail_atr x rsi_exit x max_hold.

Goal: maximize profitable trades count while keeping PF>=1.2 and
eliminating negative years (2018, 2021, 2022, 2026).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_combined"
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
    exit_on_ema = params["exit_on_ema"]
    rsi_exit_long = params["rsi_exit_long"]
    max_hold = params["max_hold"]
    trail_atr = params["trail_atr"]

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()

    side = 0
    entry_i = -1
    entry_px = 0.0
    high_px = 0.0
    low_px = 0.0
    consec_long = 0

    trade_rets = []
    trade_times = []

    start_i = max(params["ema_period"], params["trend_period"]) + 10

    for i in range(start_i, n - 1):
        if side == 0:
            if c.iloc[i] > e.iloc[i]:
                consec_long += 1
            elif c.iloc[i] < e.iloc[i]:
                consec_long = 0
            else:
                consec_long = 0

            c_i = float(c.iloc[i])
            te_i = float(trend_e.iloc[i])
            long_allowed = c_i > te_i

            if long_allowed and consec_long >= min_consec_long and i >= 1 and c.iloc[i - 1] <= e.iloc[i - 1]:
                side = 1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                high_px = entry_px
                low_px = entry_px
                consec_long = 0
                continue

        else:
            high_px = max(high_px, float(h.iloc[i]))
            low_px = min(low_px, float(l.iloc[i]))

            exit_now = False

            c_i = float(c.iloc[i])
            bars_held = i - entry_i

            if exit_on_ema and c_i < float(e.iloc[i]):
                exit_now = True

            rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
            if rv >= rsi_exit_long:
                exit_now = True

            if trail_atr > 0 and bars_held > 0:
                a = float(atr.iloc[entry_i]) if pd.notna(atr.iloc[entry_i]) else 0.001
                trail_px = high_px - trail_atr * a
                if c_i <= trail_px:
                    exit_now = True

            if bars_held >= max_hold:
                exit_now = True

            if exit_now:
                exit_px = c_i
                gross = (exit_px - entry_px) / entry_px
                trade_rets.append(gross)
                trade_times.append(df.index[i])
                side = 0

    if side != 0 and entry_i >= 0:
        exit_px = float(c.iloc[n - 1])
        gross = (exit_px - entry_px) / entry_px
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

    base = {
        "ema_period": 2,
        "min_consec_long": 1,
        "exit_on_ema": False,
        "rsi_exit_long": 80,
        "max_hold": 48,
        "trail_atr": 0.0,
        "trend_period": 100,
    }

    results = []

    # Combined sweep: trail_atr x rsi_exit_long x max_hold
    for trail in (0.0, 1.0, 1.5, 2.0, 3.0):
        for rel in (70, 75, 80, 85, 90):
            for mh in (24, 48, 72, 96):
                p = dict(base, trail_atr=trail, rsi_exit_long=rel, max_hold=mh)
                rets = simulate_harvest(df, p)
                if len(rets) == 0:
                    continue
                total = float(np.prod(1 + rets) - 1) * 100
                wins = rets[rets > 0]
                losses = rets[rets < 0]
                pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 999
                winrate = 100 * (rets > 0).mean()
                n_profitable = int((rets > 0).sum())
                n_total = len(rets)
                neg_years = year_negatives(rets)
                results.append({
                    "trail_atr": trail, "rsi_exit_long": rel, "max_hold": mh,
                    "total_pct": round(total, 2), "pf": round(pf, 2),
                    "winrate": round(winrate, 1), "n_profitable": n_profitable,
                    "n_total": n_total, "neg_years": str(neg_years),
                })

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUT / "combined_sweep.csv", index=False)

    # Filter: PF >= 1.2, then sort by profitable trades
    good = results_df[results_df["pf"] >= 1.2]
    print(f"\n=== {len(good)} configs with PF>=1.2, sorted by profitable trades ===")
    top = good.sort_values("n_profitable", ascending=False).head(20)
    print(top.to_string(index=False))

    print(f"\n=== {len(good)} configs with PF>=1.2, sorted by total return ===")
    top2 = good.sort_values("total_pct", ascending=False).head(20)
    print(top2.to_string(index=False))

    # Best balance: PF>=1.3, maximize profitable trades
    best = good[good["pf"] >= 1.3].sort_values("n_profitable", ascending=False).iloc[0]
    print(f"\n=== BEST BALANCE: PF>=1.3, max profitable trades ===")
    print(f"trail={best['trail_atr']} rel={best['rsi_exit_long']} mh={best['max_hold']}")
    print(f"total={best['total_pct']}% pf={best['pf']} winrate={best['winrate']}% n_prof={best['n_profitable']} n_total={best['n_total']} neg_years={best['neg_years']}")

    p = dict(base, trail_atr=float(best["trail_atr"]), rsi_exit_long=int(best["rsi_exit_long"]), max_hold=int(best["max_hold"]))
    rets_best = simulate_harvest(df, p).sort_index()
    publish_results("ema_harvest_combined_best", rets_best, last_months=24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
