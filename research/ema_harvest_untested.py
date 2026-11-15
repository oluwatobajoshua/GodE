#!/usr/bin/env python3>
"""Test untested dimensions one at a time on best base config.

Base: EMA=2, trend=long_only_above EMA100, min_consec=1, rsi_entry=100(disabled),
      rsi_exit=70, max_hold=24, trail_atr=2.0
Untested: vol_filter (ATR p80), session (London/NY/Asian), short-side improvements
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_untested"
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

    hour_list = list(df.index.hour)

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
            hour = hour_list[i]

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

    base = {
        "ema_period": 2,
        "min_consec_long": 1,
        "min_consec_short": 1,
        "enable_short": False,
        "rsi_entry_max": 100,
        "trend_filter": "long_only_above",
        "rsi_exit_long": 70,
        "rsi_exit_short": 20,
        "max_hold": 24,
        "trail_atr": 2.0,
        "vol_filter": False,
        "session": "all",
        "trend_period": 100,
    }

    results = []

    # 1. Baseline
    print("\n1. Baseline (long_only_above EMA100, trail=2.0, max_hold=24, rsi_exit=70)")
    rets = simulate_harvest(df, base)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "baseline", **base, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 2. Vol filter ATR p80
    p = dict(base, vol_filter=True)
    print("\n2. Vol filter (ATR p80 gate)")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "vol_filter_p80", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 3. Session: London only
    p = dict(base, session="london")
    print("\n3. Session: London only (8-17 UTC)")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "session_london", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 4. Session: NY only
    p = dict(base, session="ny")
    print("\n4. Session: NY only (13-22 UTC)")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "session_ny", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 5. Session: Asian only
    p = dict(base, session="asian")
    print("\n5. Session: Asian only (0-8 UTC)")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "session_asian", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 6. Vol filter + London
    p = dict(base, vol_filter=True, session="london")
    print("\n6. Vol filter + London session")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "vol+london", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 7. Shorts enabled with min_consec_short=3 (weaker confirmation)
    p = dict(base, enable_short=True, min_consec_short=3, trend_filter="both_sides")
    print("\n7. Shorts enabled, min_consec_short=3, both_sides trend")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "shorts_mcs3_both", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    # 8. Shorts enabled with min_consec_short=5
    p = dict(base, enable_short=True, min_consec_short=5, trend_filter="both_sides")
    print("\n8. Shorts enabled, min_consec_short=5, both_sides trend")
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum())
    neg = year_negatives(rets)
    print(f"   total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={neg}")
    results.append({"config": "shorts_mcs5_both", **p, "total_pct": round(total,2), "pf": round(pf,2), "n_profitable": int((rets>0).sum()), "n_total": len(rets), "neg_years": str(neg)})

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUT / "untested_dims.csv", index=False)

    # Best
    good = results_df[results_df["pf"] >= 1.2]
    if len(good):
        best = good.sort_values("n_profitable", ascending=False).iloc[0]
        print(f"\n=== BEST UNTESTED DIM: {best['config']} ===")
        print(best.to_string())
        p_best = {k: best[k] for k in ["ema_period","min_consec_long","rsi_entry_max","trend_filter","rsi_exit_long","max_hold","trail_atr","vol_filter","session","trend_period","enable_short","min_consec_short","rsi_exit_short"]}
        rets_best = simulate_harvest(df, p_best).sort_index()
        publish_results("ema_harvest_untested_best", rets_best, last_months=24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
