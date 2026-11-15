#!/usr/bin/env python3
"""Focused per-symbol M1 optimization — targeted grid, design/OOS split."""

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


def load_csv(symbol: str, tf: str = "M1") -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{symbol}_{tf}.csv")
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df.sort_values("time").reset_index(drop=True)


def simulate(df, close, atr, rsi, e_fast, e_slow, spread_ok, sess_mask, dow_mask,
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
        if not sess_mask.iloc[t] or not dow_mask.iloc[t]:
            pos[t] = 0; continue

        if state > 0:
            held = t - entry_bar; px = close[t]; exited = False
            if exit_style == "atr_stop_tp":
                if px <= entry_price - atr_mult * atr[t] or px >= entry_price + 2 * atr_mult * atr[t]:
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
                if tr < -db: state = 0; exited = True

        elif state < 0:
            held = t - entry_bar; px = close[t]; exited = False
            if exit_style == "atr_stop_tp":
                if px >= entry_price + atr_mult * atr[t] or px <= entry_price - 2 * atr_mult * atr[t]:
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
                if tr > db: state = 0; exited = True

        if state == 0 and spread_ok[t]:
            trail_high = close[t]; trail_low = close[t]
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


def stats(ret, pos):
    n = len(ret)
    if n == 0: return dict(total=0.0, dd=0.0, sharpe=0.0, rt=0)
    eq = np.cumprod(1.0 + ret)
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    sharpe = float(ret.mean() / sd * np.sqrt(24 * 60 * 252)) if sd > 0 else 0.0
    rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
    return dict(total=float(eq[-1] - 1.0), dd=float((eq / peak - 1.0).min()),
                sharpe=sharpe, rt=rt)


def session_mask(time, session):
    hour = time.dt.hour
    if session == "london": return (hour >= 8) & (hour < 17)
    if session == "ny": return (hour >= 13) & (hour < 22)
    if session == "asia": return (hour >= 0) & (hour < 8)
    if session == "lon_ny": return ((hour >= 8) & (hour < 17)) | ((hour >= 13) & (hour < 22))
    if session == "lon_only": return (hour >= 8) & (hour < 13)
    return pd.Series([True] * len(time))


def optimize_symbol(sym, df):
    n = len(df)
    split = int(n * 0.7)  # 70% design, 30% OOS
    design = df.iloc[:split].reset_index(drop=True)
    oos = df.iloc[split:].reset_index(drop=True)

    close_d = design["close"].to_numpy(float)
    atr_d = wilder_atr(design, 14).to_numpy(float)
    spread_d = design["spread"].astype(float).to_numpy()
    time_d = design["time"]

    close_o = oos["close"].to_numpy(float)
    atr_o = wilder_atr(oos, 14).to_numpy(float)
    spread_o = oos["spread"].astype(float).to_numpy()
    time_o = oos["time"]

    point = 0.001 if sym.endswith("JPY") else 0.00001

    def cost_side(spread_arr):
        spread_eff = np.maximum(spread_arr, 1.0)
        return (spread_eff / 2.0 + 1.0 + 3.5) * point

    cs_d = cost_side(spread_d)
    cs_o = cost_side(spread_o)

    q_d = pd.Series(spread_d).rolling(500, min_periods=100).quantile(0.90)
    spread_ok_d_base = (spread_d <= q_d.to_numpy()) | ~np.isfinite(q_d.to_numpy())
    q_o = pd.Series(spread_o).rolling(500, min_periods=100).quantile(0.90)
    spread_ok_o_base = (spread_o <= q_o.to_numpy()) | ~np.isfinite(q_o.to_numpy())

    # Focused grid — only the most promising per symbol
    # Each symbol gets its own parameter search
    if sym == "EURUSD":
        rsi_ps = [9, 14]
        ema_pairs = [(21, 50), (13, 34)]
        styles = ["pullback", "trend_mid"]
        exits = ["rsi_mid", "time_stop", "atr_stop_tp"]
        dbs = [0.1, 0.2]
        mhs = [6, 12]
        margins = [0.0, 1.0, 3.0]
        sessions = ["none", "london", "lon_only"]
    elif sym == "GBPUSD":
        rsi_ps = [14, 21]
        ema_pairs = [(21, 50), (13, 34)]
        styles = ["pullback", "meanrev_lo"]
        exits = ["rsi_mid", "time_stop", "trailing"]
        dbs = [0.1, 0.2]
        mhs = [3, 6]
        margins = [1.0, 2.0, 3.0]
        sessions = ["london", "ny", "lon_only"]
    else:  # USDJPY
        rsi_ps = [14, 21]
        ema_pairs = [(21, 50), (13, 34)]
        styles = ["pullback", "breakout"]
        exits = ["rsi_mid", "atr_stop_tp", "time_stop"]
        dbs = [0.1]
        mhs = [3, 6]
        margins = [0.0, 3.0]
        sessions = ["lon_only", "ny"]

    best_design = None
    best_total = -999
    tested = 0

    for rsi_p in rsi_ps:
        rsi_d = wilder_rsi(design["close"], rsi_p).to_numpy(float)
        rsi_o = wilder_rsi(oos["close"], rsi_p).to_numpy(float)
        for ef, es in ema_pairs:
            e_fast_d = ema(design["close"], ef).to_numpy(float)
            e_slow_d = ema(design["close"], es).to_numpy(float)
            e_fast_o = ema(oos["close"], ef).to_numpy(float)
            e_slow_o = ema(oos["close"], es).to_numpy(float)
            for style in styles:
                for exit_style in exits:
                    for db in dbs:
                        for mh in mhs:
                            for margin in margins:
                                for sess in sessions:
                                    for al in [True, False]:
                                        if style == "meanrev_lo" and al:
                                            continue
                                        tested += 1
                                        lo, hi, mid = 20, 80, 50
                                        pull_lo, pull_hi = 40, 60
                                        sess_d = session_mask(time_d, sess) if sess != "none" else pd.Series([True] * len(design))
                                        sess_o = session_mask(time_o, sess) if sess != "none" else pd.Series([True] * len(oos))
                                        dow_d = pd.Series([True] * len(design))
                                        dow_o = pd.Series([True] * len(oos))
                                        sp_d = spread_ok_d_base & sess_d.to_numpy() & dow_d.to_numpy()
                                        sp_o = spread_ok_o_base & sess_o.to_numpy() & dow_o.to_numpy()
                                        kw_ts = 240 if exit_style == "time_stop" else None
                                        kw_tm = 2.0 if exit_style == "trailing" else 1.5
                                        kw_am = 1.5 if exit_style == "atr_stop_tp" else 1.5
                                        ret_d, pos_d = simulate(design, close_d, atr_d, rsi_d,
                                                              e_fast_d, e_slow_d, sp_d, sess_d, dow_d,
                                                              style, exit_style, lo, hi, mid, pull_lo, pull_hi,
                                                              db, mh, margin, kw_am, kw_ts, kw_tm, al, cs_d)
                                        st_d = stats(ret_d, pos_d)
                                        if st_d["rt"] < 3:
                                            continue
                                        ret_o, pos_o = simulate(oos, close_o, atr_o, rsi_o,
                                                              e_fast_o, e_slow_o, sp_o, sess_o, dow_o,
                                                              style, exit_style, lo, hi, mid, pull_lo, pull_hi,
                                                              db, mh, margin, kw_am, kw_ts, kw_tm, al, cs_o)
                                        st_o = stats(ret_o, pos_o)
                                        if st_d["total"] > best_total:
                                            best_total = st_d["total"]
                                            best_design = dict(sym=sym, rsi_p=rsi_p, ef=ef, es=es,
                                                              style=style, exit=exit_style, db=db,
                                                              mh=mh, margin=margin, sess=sess, align=al,
                                                              design_total=st_d["total"] * 100,
                                                              design_sharpe=st_d["sharpe"],
                                                              design_dd=st_d["dd"] * 100,
                                                              design_rt=st_d["rt"],
                                                              oos_total=st_o["total"] * 100,
                                                              oos_sharpe=st_o["sharpe"],
                                                              oos_dd=st_o["dd"] * 100,
                                                              oos_rt=st_o["rt"])

    print(f"\n{'='*60}")
    print(f"{sym} M1 per-symbol optimization ({tested} configs tested)")
    print(f"{'='*60}")
    if best_design:
        bd = best_design
        print(f"  BEST: rsi_p={bd['rsi_p']} ema={bd['ef']}/{bd['es']} style={bd['style']} "
              f"exit={bd['exit']} db={bd['db']} mh={bd['mh']} margin={bd['margin']} "
              f"sess={bd['sess']} align={bd['align']}")
        print(f"  DESIGN: total={bd['design_total']:+.2f}% Sharpe={bd['design_sharpe']:.2f} "
              f"DD={bd['design_dd']:.2f}% RT={bd['design_rt']}")
        print(f"  OOS:    total={bd['oos_total']:+.2f}% Sharpe={bd['oos_sharpe']:.2f} "
              f"DD={bd['oos_dd']:.2f}% RT={bd['oos_rt']}")
        return bd
    else:
        print("  No config with >=3 trades on design split")
        return None


def main() -> int:
    all_best = []
    for sym in SYMBOLS:
        df = load_csv(sym, "M1")
        print(f"\n{sym}: {len(df)} bars, {df['time'].iloc[0]} to {df['time'].iloc[-1]}")
        bd = optimize_symbol(sym, df)
        if bd:
            all_best.append(bd)

    print("\n" + "="*60)
    print("SUMMARY — Best per-symbol M1 configs (OOS validation)")
    print("="*60)
    for bd in all_best:
        print(f"  {bd['sym']:8s} rsi_p={bd['rsi_p']} ema={bd['ef']}/{bd['es']} "
              f"{bd['style']:12s}/{bd['exit']:12s} db={bd['db']} mh={bd['mh']} "
              f"mg={bd['margin']} sess={bd['sess']} al={bd['align']} | "
              f"Design: {bd['design_total']:+.2f}% ({bd['design_rt']} RT) | "
              f"OOS: {bd['oos_total']:+.2f}% ({bd['oos_rt']} RT) Sharpe={bd['oos_sharpe']:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
