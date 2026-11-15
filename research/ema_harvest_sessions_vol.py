#!/usr/bin/env python3>
"""Test session filters + vol filter on best EMA harvest config (SL2.0/TP4.0)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_sessions_vol"
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
    enable_short = params["enable_short"]
    min_consec_short = params["min_consec_short"]
    rsi_entry_max = params["rsi_entry_max"]
    trend_filter = params["trend_filter"]
    rsi_exit_long = params["rsi_exit_long"]
    rsi_exit_short = params["rsi_exit_short"]
    max_hold = params["max_hold"]
    trail_atr = params["trail_atr"]
    sl_atr = params["sl_atr"]
    tp_atr = params["tp_atr"]
    circuit_breaker = params["circuit_breaker"]
    session = params["session"]  # none, london, ny, asian
    vol_filter = params["vol_filter"]

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()
    atr_pct = atr / c  # ATR as % of price

    side = 0
    entry_i = -1
    entry_px = 0.0
    high_px = 0.0
    low_px = 0.0
    consec_long = 0
    consec_short = 0

    trade_rets = []
    trade_times = []
    consec_losses = 0
    paused_until_trade = 0
    trade_idx = 0

    start_i = max(params["ema_period"], params["trend_period"]) + 10

    # Vol filter: ATR p80 threshold computed over design period
    vol_threshold = np.nanpercentile(atr_pct.dropna().values, 80) if vol_filter else None

    for i in range(start_i, n - 1):
        if side == 0:
            # Session filter
            if session:
                hour = df.index[i].hour
                if session == "london" and not (8 <= hour < 17):
                    continue
                if session == "ny" and not (13 <= hour < 22):
                    continue
                if session == "asian" and not (hour < 8 or hour >= 22):
                    continue

            # Vol filter: skip if current ATR pct above p80
            if vol_filter and vol_threshold is not None:
                cur_atr_pct = float(atr_pct.iloc[i])
                if np.isnan(cur_atr_pct) or cur_atr_pct > vol_threshold:
                    continue

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

            if trade_idx >= paused_until_trade:
                if long_allowed and consec_long >= min_consec_long and i >= 1 and c.iloc[i - 1] <= e.iloc[i - 1]:
                    side = 1
                    entry_i = i
                    entry_px = float(c.iloc[i + 1])
                    high_px = entry_px
                    low_px = entry_px
                    consec_long = 0
                    consec_short = 0
                    trade_idx += 1
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
                    trade_idx += 1
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
            entry_atr = float(atr.iloc[entry_i]) if entry_i < len(atr) else 0.001

            if side > 0 and rv >= rsi_exit_long:
                exit_now = True
            if side < 0 and rv <= rsi_exit_short:
                exit_now = True

            if not exit_now:
                if side > 0:
                    sl_px = entry_px - sl_atr * entry_atr
                    tp_px = entry_px + tp_atr * entry_atr
                    if c_i <= sl_px:
                        exit_now = True
                    if c_i >= tp_px:
                        exit_now = True
                else:
                    sl_px = entry_px + sl_atr * entry_atr
                    tp_px = entry_px - tp_atr * entry_atr
                    if c_i >= sl_px:
                        exit_now = True
                    if c_i <= tp_px:
                        exit_now = True

            if not exit_now and trail_atr > 0.0 and bars_held > 0:
                a = entry_atr
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

                if gross < 0:
                    consec_losses += 1
                else:
                    consec_losses = 0

                if circuit_breaker > 0 and consec_losses >= circuit_breaker:
                    paused_until_trade = trade_idx + circuit_breaker * 3

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


def walk_forward(df: pd.DataFrame, params: dict) -> dict:
    n = len(df)
    d1 = int(n * 0.40)
    d2 = int(n * 0.60)
    results = {}
    for label, ddf in (("design", df.iloc[:d1]), ("validation", df.iloc[d1:d2]), ("oos", df.iloc[d2:])):
        rets = simulate_harvest(ddf, params)
        total = float(np.prod(1 + rets) - 1) * 100 if len(rets) else 0
        pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if len(rets) and (rets < 0).any() else 999
        results[label] = {"total_pct": round(total, 2), "pf": round(pf, 2),
                          "n_profitable": int((rets > 0).sum()), "n_total": len(rets),
                          "neg_years": str(year_negatives(rets))}
    return results


def main() -> int:
    df = load_csv("BTCUSD", "H1")
    print(f"=== BTCUSD H1 ({len(df)} bars) ===")

    base_params = {
        "ema_period": 2, "min_consec_long": 1, "min_consec_short": 1,
        "enable_short": False, "rsi_entry_max": 100, "trend_filter": "long_only_above",
        "rsi_exit_long": 70, "rsi_exit_short": 20, "max_hold": 24,
        "sl_atr": 2.0, "tp_atr": 4.0, "circuit_breaker": 5, "trail_atr": 0.0,
        "session": "none", "vol_filter": False, "trend_period": 100,
    }

    results = []

    # Baseline (no filters)
    print("\n=== Baseline (no filters) ===")
    rets = simulate_harvest(df, base_params)
    total = float(np.prod(1 + rets) - 1) * 100 if len(rets) else 0
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if len(rets) and (rets < 0).any() else 999
    print(f"  total={total:.2f}% pf={pf:.2f} n_prof={int((rets>0).sum())} n_total={len(rets)} neg_years={year_negatives(rets)}")

    # Session filters (without cb for cleaner signal)
    print("\n=== Session filters ===")
    for sess in ("london", "ny", "asian"):
        p = dict(base_params, session=sess, circuit_breaker=0)
        rets = simulate_harvest(df, p)
        total = float(np.prod(1 + rets) - 1) * 100 if len(rets) else 0
        pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if len(rets) and (rets < 0).any() else 999
        print(f"  {sess:8s} total={total:>10.2f}% pf={pf:.2f} n_prof={int((rets>0).sum()):4d} n_total={len(rets):4d} neg_years={year_negatives(rets)}")
        results.append({"test": f"session_{sess}", "session": sess,
                        "total_pct": round(total,2), "pf": round(pf,2),
                        "n_profitable": int((rets>0).sum()), "n_total": len(rets),
                        "neg_years": str(year_negatives(rets))})

    # Vol filter
    print("\n=== Vol filter (ATR p80 gate) ===")
    p = dict(base_params, vol_filter=True, circuit_breaker=0)
    rets = simulate_harvest(df, p)
    total = float(np.prod(1 + rets) - 1) * 100 if len(rets) else 0
    pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if len(rets) and (rets < 0).any() else 999
    print(f"  vol_filter total={total:>10.2f}% pf={pf:.2f} n_prof={int((rets>0).sum()):4d} n_total={len(rets):4d} neg_years={year_negatives(rets)}")
    results.append({"test": "vol_filter", "vol_filter": True,
                    "total_pct": round(total,2), "pf": round(pf,2),
                    "n_profitable": int((rets>0).sum()), "n_total": len(rets),
                    "neg_years": str(year_negatives(rets))})

    # Session + vol filter combos (with cb5 like best config)
    print("\n=== Session + vol + cb5 ===")
    for sess in ("london", "ny", "asian", "none"):
        p = dict(base_params, session=sess, circuit_breaker=5,
                 vol_filter=(sess != "none"))
        rets = simulate_harvest(df, p)
        total = float(np.prod(1 + rets) - 1) * 100 if len(rets) else 0
        pf = rets[rets > 0].sum() / abs(rets[rets < 0].sum()) if len(rets) and (rets < 0).any() else 999
        print(f"  sess={sess:8s} total={total:>10.2f}% pf={pf:.2f} n_prof={int((rets>0).sum()):4d} n_total={len(rets):4d} neg_years={year_negatives(rets)}")
        results.append({"test": f"combo_{sess}", "session": sess,
                        "circuit_breaker": 5, "vol_filter": sess != "none",
                        "total_pct": round(total,2), "pf": round(pf,2),
                        "n_profitable": int((rets>0).sum()), "n_total": len(rets),
                        "neg_years": str(year_negatives(rets))})

    # Walk-forward on best combo (NY session, vol filter, cb5)
    print("\n=== Walk-forward: NY + vol + cb5 ===")
    p = dict(base_params, session="ny", circuit_breaker=5, vol_filter=True)
    wf = walk_forward(df, p)
    for label, r in wf.items():
        print(f"  {label:10s} total={r['total_pct']:>10.2f}% pf={r['pf']:.2f} n_prof={r['n_profitable']} n_total={r['n_total']} neg_years={r['neg_years']}")

    results_df = pd.DataFrame(results)
    results_df.to_csv(OUT / "session_vol_results.csv", index=False)

    # Monthly report for best combo
    p = dict(base_params, session="ny", circuit_breaker=5, vol_filter=True)
    rets_best = simulate_harvest(df, p).sort_index()
    publish_results("ema_harvest_ny_vol_cb5", rets_best, last_months=24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
