#!/usr/bin/env python3
r"""Exhaustive M1 RSI sweep — every plausible parameter combination.

Entry styles:
  trend_mid     : EMA fast/slow trend filter, buy RSI cross UP through mid, sell cross DOWN
  meanrev_lo    : buy RSI cross UP through lo, sell cross DOWN through hi (pure mean reversion)
  pullback      : trend filter + buy RSI dip to pullback_level crossing up, sell rally crossing down
  breakout      : buy RSI cross UP through hi (momentum), sell cross DOWN through lo

Exit styles:
  rsi_mid       : exit on RSI cross back through mid
  slope_flip    : exit on EMA slope flip
  atr_stop_tp   : ATR stop + 2x ATR TP
  time_stop     : exit after N bars
  trailing      : trailing stop at k*ATR from highest/lowest since entry

Filters:
  spread gate p90
  session: London (08-17 UTC), NY (13-22 UTC), Asia (00-08 UTC), London+NY overlap
  DOW: Mon-Fri, exclude Mon/Fri
  EMA alignment: EMA21 > EMA50 for long, < for short

Params:
  RSI period: 7, 9, 14, 21
  EMA fast: 9, 21, 50
  EMA slow: 21, 50, 200
  lo/hi: 20/80, 25/75, 30/70, 35/65
  mid: 50
  pullback level: 40/60, 45/55, 35/65
  deadband: 0, 0.05, 0.10, 0.20
  min_hold: 3, 6, 12, 24
  margin: 0, 1, 3, 5
  ATR mult: 1.0, 1.5, 2.0, 3.0
  time stop: 30, 60, 120, 240 bars
  trailing: 1.0, 1.5, 2.0, 3.0 ATR

Run:
    .\.venv\Scripts\python.exe research\rsi_band_m1_exhaustive.py
"""

from __future__ import annotations

import itertools
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


def load_csv(symbol: str, tf: str = "M1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df.sort_values("time").reset_index(drop=True)


def stats(ret, pos):
    n = len(ret)
    if n == 0:
        return dict(total=0.0, dd=0.0, sharpe=0.0, rt=0)
    eq = np.cumprod(1.0 + ret)
    yrs = max(n / (24 * 60 * 252), 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    sharpe = float(ret.mean() / sd * np.sqrt(24 * 60 * 252)) if sd > 0 else 0.0
    rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
    return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                dd=float((eq / peak - 1.0).min()),
                sharpe=sharpe, rt=rt)


def session_mask(time, session):
    """UTC hour-based session filter."""
    hour = time.dt.hour
    if session == "london":
        return (hour >= 8) & (hour < 17)
    if session == "ny":
        return (hour >= 13) & (hour < 22)
    if session == "asia":
        return (hour >= 0) & (hour < 8)
    if session == "lon_ny":
        return ((hour >= 8) & (hour < 17)) | ((hour >= 13) & (hour < 22))
    if session == "lon_only_excl_ny":
        return ((hour >= 8) & (hour < 13))
    return pd.Series([True] * len(time))


def dow_mask(time, dow):
    if dow == "mon_fri":
        return time.dt.dayofweek.isin([0, 4])
    if dow == "tue_thu":
        return time.dt.dayofweek.isin([1, 3])
    if dow == "midweek":
        return time.dt.dayofweek.isin([1, 2, 3])
    return pd.Series([True] * len(time))


def simulate(df, close, atr, rsi, e_fast, e_slow, spread_ok, sess, dow_ok,
             style, exit_style, lo, hi, mid, pull_lo, pull_hi,
             db, min_hold, margin, atr_mult, time_stop, trail_mult,
             use_align, cost_side):
    n = len(df)
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10**9
    entry_price = 0.0
    trail_high = 0.0
    trail_low = 10**18

    slope = np.zeros(n)
    slope[3:] = (e_fast[3:] - e_fast[:-3]) / np.maximum(atr[3:], 1e-12)

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        tr = slope[t]
        eal = e_fast[t] > e_slow[t] if use_align else True

        if not sess.iloc[t] or not dow_ok.iloc[t]:
            pos[t] = 0
            continue

        # ---- exits ----
        if state > 0:
            held = t - entry_bar
            px = close[t]
            exited = False
            if exit_style == "atr_stop_tp":
                stop_px = entry_price - atr_mult * atr[t]
                tp_px = entry_price + 2.0 * atr_mult * atr[t]
                if px <= stop_px or px >= tp_px:
                    state = 0; exited = True
            if not exited and exit_style == "time_stop" and held >= time_stop:
                state = 0; exited = True
            if not exited and exit_style == "trailing":
                trail_high = max(trail_high, px)
                if px <= trail_high - trail_mult * atr[t]:
                    state = 0; exited = True
            if not exited and exit_style == "rsi_mid" and held >= min_hold:
                if tr < -db or (rp >= mid and r < mid):
                    state = 0; exited = True
            if not exited and exit_style == "slope_flip" and held >= min_hold:
                if tr < -db:
                    state = 0; exited = True

        elif state < 0:
            held = t - entry_bar
            px = close[t]
            exited = False
            if exit_style == "atr_stop_tp":
                stop_px = entry_price + atr_mult * atr[t]
                tp_px = entry_price - 2.0 * atr_mult * atr[t]
                if px >= stop_px or px <= tp_px:
                    state = 0; exited = True
            if not exited and exit_style == "time_stop" and held >= time_stop:
                state = 0; exited = True
            if not exited and exit_style == "trailing":
                trail_low = min(trail_low, px)
                if px >= trail_low + trail_mult * atr[t]:
                    state = 0; exited = True
            if not exited and exit_style == "rsi_mid" and held >= min_hold:
                if tr > db or (rp <= mid and r > mid):
                    state = 0; exited = True
            if not exited and exit_style == "slope_flip" and held >= min_hold:
                if tr > db:
                    state = 0; exited = True

        # ---- entries ----
        if state == 0 and spread_ok[t]:
            trail_high = close[t]
            trail_low = close[t]
            if style == "trend_mid":
                if tr > db and eal and rp <= mid and r > mid:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif tr < -db and not eal and rp >= mid and r < mid:
                    state = -1; entry_bar = t; entry_price = close[t]
            elif style == "meanrev_lo":
                if rp <= lo + margin and r > lo + margin:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif rp >= hi - margin and r < hi - margin:
                    state = -1; entry_bar = t; entry_price = close[t]
            elif style == "pullback":
                if tr > db and eal and rp <= pull_lo + margin and r > pull_lo + margin:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif tr < -db and not eal and rp >= pull_hi - margin and r < pull_hi - margin:
                    state = -1; entry_bar = t; entry_price = close[t]
            elif style == "breakout":
                if tr > db and eal and rp <= hi - margin and r > hi - margin:
                    state = 1; entry_bar = t; entry_price = close[t]
                elif tr < -db and not eal and rp >= lo + margin and r < lo + margin:
                    state = -1; entry_bar = t; entry_price = close[t]

        pos[t] = state

    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    return ret, pos


def main() -> int:
    # Parameter grids
    rsi_periods = [7, 9, 14, 21]
    ema_fast_opts = [9, 21, 50]
    ema_slow_opts = [21, 50, 200]
    lo_hi_opts = [(20, 80), (25, 75), (30, 70), (35, 65)]
    pull_opts = [(40, 60), (45, 55), (35, 65)]
    dbs = [0.0, 0.05, 0.10, 0.20]
    min_holds = [3, 6, 12, 24]
    margins = [0.0, 1.0, 3.0, 5.0]
    atr_mults = [1.0, 1.5, 2.0, 3.0]
    time_stops = [30, 60, 120, 240]
    trail_mults = [1.0, 1.5, 2.0, 3.0]
    sessions = ["none", "london", "ny", "asia", "lon_ny", "lon_only_excl_ny"]
    dows = ["all", "mon_fri", "tue_thu", "midweek"]
    styles = ["trend_mid", "meanrev_lo", "pullback", "breakout"]
    exit_styles = ["rsi_mid", "slope_flip", "atr_stop_tp", "time_stop", "trailing"]
    align_opts = [False, True]

    all_rows = []

    for sym in SYMBOLS:
        df = load_csv(sym, "M1")
        n = len(df)
        close = df["close"].to_numpy(float)
        atr = wilder_atr(df, 14).to_numpy(float)
        spread = df["spread"].astype(float).to_numpy()
        time = df["time"]

        point = 0.001 if sym.endswith("JPY") else 0.00001
        spread_eff = np.maximum(spread, 1.0)
        cost_side = (spread_eff / 2.0 + 1.0 + 3.5) * point

        q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
        spread_ok_base = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

        count = 0
        for rp in rsi_periods:
            rsi = wilder_rsi(df["close"], rp).to_numpy(float)
            for ef in ema_fast_opts:
                for es in ema_slow_opts:
                    if ef >= es:
                        continue
                    e_fast = ema(df["close"], ef).to_numpy(float)
                    e_slow = ema(df["close"], es).to_numpy(float)
                    for lo, hi in lo_hi_opts:
                        mid = (lo + hi) / 2.0
                        for pl, ph in pull_opts:
                            for db in dbs:
                                for mh in min_holds:
                                    for mg in margins:
                                        for am in atr_mults:
                                            for ts in time_stops:
                                                for tm in trail_mults:
                                                    for sess in sessions:
                                                        for dow in dows:
                                                            for style in styles:
                                                                for ex in exit_styles:
                                                                    for al in align_opts:
                                                                        # Skip incompatible combos
                                                                        if style == "meanrev_lo" and al:
                                                                            continue
                                                                        if ex == "time_stop" and ts is None:
                                                                            continue
                                                                        if ex == "trailing" and style not in ["trend_mid", "breakout"]:
                                                                            continue
                                                                        sess_mask = session_mask(time, sess) if sess != "none" else pd.Series([True]*n)
                                                                        dow_mask_s = dow_mask(time, dow) if dow != "all" else pd.Series([True]*n)
                                                                        spread_ok = spread_ok_base & sess_mask.to_numpy() & dow_mask_s.to_numpy()
                                                                        ret, pos = simulate(
                                                                            df, close, atr, rsi, e_fast, e_slow,
                                                                            spread_ok, sess_mask, dow_mask_s,
                                                                            style, ex, lo, hi, mid, pl, ph,
                                                                            db, mh, mg, am, ts, tm, al, cost_side)
                                                                        st = stats(ret, pos)
                                                                        all_rows.append(dict(
                                                                            sym=sym, rsi_p=rp, ema_fast=ef, ema_slow=es,
                                                                            lo=lo, hi=hi, mid=mid, pull_lo=pl, pull_hi=ph,
                                                                            db=db, min_hold=mh, margin=mg,
                                                                            atr_mult=am, time_stop=ts, trail_mult=tm,
                                                                            session=sess, dow=dow, style=style,
                                                                            exit=ex, align=al,
                                                                            rt=st["rt"], net_total=st["total"] * 100,
                                                                            net_sharpe=st["sharpe"], net_dd=st["dd"] * 100,
                                                                        ))
                                                                        count += 1

        tbl = pd.DataFrame([r for r in all_rows if r["sym"] == sym])
        print(f"\n=== {sym} M1 exhaustive ({count} configs so far) ===")
        if len(tbl) > 0:
            best = tbl.loc[tbl["net_total"].idxmax()]
            print(f"configs: {len(tbl)}, best total: {best['net_total']:+.2f}% "
                  f"({best['style']}/{best['exit']} rsi_p={best['rsi_p']} "
                  f"ema={best['ema_fast']}/{best['ema_slow']} "
                  f"db={best['db']} mh={best['min_hold']} margin={best['margin']} "
                  f"sess={best['session']} dow={best['dow']} align={best['align']} "
                  f"RT={best['rt']} Sharpe={best['net_sharpe']:.2f} DD={best['net_dd']:.2f}%)")
            g = tbl.sort_values("net_total", ascending=False).head(10)
            for _, r in g.iterrows():
                print(f"  {r.style:12s}/{r.exit:12s} rsi_p={r.rsi_p} ema={r.ema_fast}/{r.ema_slow} "
                      f"lo/hi={r.lo}/{r.hi} db={r.db} mh={r.min_hold} mg={r.margin} "
                      f"sess={r.session} dow={r.dow} al={r.align} "
                      f"RT={r.rt} total={r.net_total:+.2f}% Sharpe={r.net_sharpe:.2f} DD={r.net_dd:.2f}%")

    out = pd.DataFrame(all_rows)
    out_path = RESULTS / "rsi_band_m1_exhaustive.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path} ({len(out)} total rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
