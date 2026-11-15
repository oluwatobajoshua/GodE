#!/usr/bin/env python3
"""Comprehensive M1 RSI research — multiple entry/exit styles on fxmopol.tpl params.

Tests:
  A. Trend follow: EMA21>EMA50 long bias, buy RSI cross UP through 50; sell cross DOWN through 50
  B. Mean reversion: buy RSI cross UP through 20, sell cross DOWN through 80
  C. Trend + pullback: EMA21>EMA50, buy RSI dip to ~40 then cross up; sell rally to ~60 then cross down

Exit: RSI cross back through 50 (reverse), or time stop, or ATR stop/TP.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ema, wilder_atr, wilder_rsi

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY"]
EMA_FAST, EMA_SLOW, RSI_P = 21, 50, 9


def load_csv(symbol: str, tf: str = "M1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df.sort_values("time").reset_index(drop=True)


def stats(ret):
    n = len(ret)
    if n == 0:
        return dict(total=0.0, dd=0.0, sharpe=0.0, rt=0)
    eq = np.cumprod(1.0 + ret)
    yrs = max(n / (24 * 60 * 252), 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    rt = int((np.abs(np.diff(np.r_[0, np.sign(ret)])) > 0).sum()) if n > 1 else 0
    return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                dd=float((eq / peak - 1.0).min()),
                sharpe=float(ret.mean() / sd * np.sqrt(24 * 60 * 252)), rt=rt)


def simulate(df, close, atr, rsi, e21, e50, spread_ok, cost_side,
             style, lo, hi, mid, db, min_hold, margin,
             use_atr_stop, atr_mult, time_stop):
    n = len(df)
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10**9
    entry_price = 0.0

    slope = np.zeros(n)
    slope[3:] = (e21[3:] - e21[:-3]) / np.maximum(atr[3:], 1e-12)

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        tr = slope[t]
        eal = e21[t] > e50[t]  # EMA alignment

        # ---- exits ----
        if state > 0:
            held = t - entry_bar
            px = close[t]
            # ATR stop / TP
            if use_atr_stop:
                stop_px = entry_price - atr_mult * atr[t]
                tp_px = entry_price + 2.0 * atr_mult * atr[t]
                if px <= stop_px or px >= tp_px:
                    state = 0; exit_bar = t; continue
            # time stop
            if time_stop and held >= time_stop:
                state = 0; exit_bar = t; continue
            # RSI exit: cross down through mid, or slope flip
            if held >= min_hold:
                if tr < -db:
                    state = 0; exit_bar = t; continue
                if rp >= mid and r < mid:
                    state = 0; exit_bar = t; continue

        elif state < 0:
            held = t - entry_bar
            px = close[t]
            if use_atr_stop:
                stop_px = entry_price + atr_mult * atr[t]
                tp_px = entry_price - 2.0 * atr_mult * atr[t]
                if px >= stop_px or px <= tp_px:
                    state = 0; exit_bar = t; continue
            if time_stop and held >= time_stop:
                state = 0; exit_bar = t; continue
            if held >= min_hold:
                if tr > db:
                    state = 0; exit_bar = t; continue
                if rp <= mid and r > mid:
                    state = 0; exit_bar = t; continue

        # ---- entries (only if flat) ----
        if state == 0 and spread_ok[t]:
            if style == "trend_mid":  # A
                if tr > db and eal and rp <= mid and r > mid:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif tr < -db and not eal and rp >= mid and r < mid:
                    state = -1; entry_bar = t; entry_price = close[t]
            elif style == "meanrev":  # B
                if rp <= lo + margin and r > lo + margin:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif rp >= hi - margin and r < hi - margin:
                    state = -1; entry_bar = t; entry_price = close[t]
            elif style == "pullback":  # C
                if tr > db and eal and rp <= 40 + margin and r > 40 + margin:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif tr < -db and not eal and rp >= 60 - margin and r < 60 - margin:
                    state = -1; entry_bar = t; entry_price = close[t]

        pos[t] = state

    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret, pos


def main() -> int:
    all_rows = []
    for sym in SYMBOLS:
        df = load_csv(sym, "M1")
        n = len(df)
        close = df["close"].to_numpy(float)
        atr = wilder_atr(df, 14).to_numpy(float)
        rsi = wilder_rsi(df["close"], RSI_P).to_numpy(float)
        spread = df["spread"].astype(float).to_numpy()
        e21 = ema(df["close"], EMA_FAST).to_numpy(float)
        e50 = ema(df["close"], EMA_SLOW).to_numpy(float)

        point = 0.001 if sym.endswith("JPY") else 0.00001
        spread_eff = np.maximum(spread, 1.0)
        cost_side = (spread_eff / 2.0 + 1.0 + 3.5) * point

        q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
        spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

        lo, hi, mid = 20, 80, 50

        for style in ["trend_mid", "meanrev", "pullback"]:
            for db in [0.0, 0.05, 0.10]:
                for min_hold in [3, 6, 12]:
                    for margin in [0.0, 1.0, 3.0]:
                        for use_atr in [False, True]:
                            for ts in [None, 60, 240]:
                                ret, pos = simulate(
                                    df, close, atr, rsi, e21, e50, spread_ok, cost_side,
                                    style, lo, hi, mid, db, min_hold, margin,
                                    use_atr, 1.5, ts)
                                st = stats(ret)
                                all_rows.append(dict(
                                    sym=sym, style=style, db=db, min_hold=min_hold,
                                    margin=margin, atr_stop=use_atr, time_stop=ts,
                                    rt=st["rt"], net_total=st["total"] * 100,
                                    net_sharpe=st["sharpe"], net_dd=st["dd"] * 100,
                                ))

        tbl = pd.DataFrame([r for r in all_rows if r["sym"] == sym])
        best = tbl.loc[tbl["net_total"].idxmax()]
        print(f"\n=== {sym} M1 ===")
        print(f"configs: {len(tbl)}, best total: {best['net_total']:+.2f}% "
              f"({best['style']} db={best['db']} mh={best['min_hold']} "
              f"margin={best['margin']} atr={best['atr_stop']} ts={best['time_stop']} "
              f"RT={best['rt']} Sharpe={best['net_sharpe']:.2f} DD={best['net_dd']:.2f}%)")
        g = tbl.sort_values("net_total", ascending=False).head(5)
        for _, r in g.iterrows():
            print(f"  {r.style:11s} db={r.db} mh={r.min_hold} margin={r.margin} "
                  f"atr={r.atr_stop} ts={r.time_stop} RT={r.rt} "
                  f"total={r.net_total:+.2f}% Sharpe={r.net_sharpe:.2f} DD={r.net_dd:.2f}%")

    out = pd.DataFrame(all_rows)
    out_path = RESULTS / "rsi_band_m1_comprehensive.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
