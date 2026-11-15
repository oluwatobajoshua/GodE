#!/usr/bin/env python3
"""M1 RSI parameter sensitivity — test each parameter independently on M1.

Baseline: RSI9, EMA21/50, trend_mid entry, rsi_mid exit, no session filter,
          db=0.1, min_hold=6, margin=0, no ATR stop, no time stop, align=True.

Sweeps each parameter individually to see what moves the needle.
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


def load_csv(symbol: str, tf: str = "M1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df.sort_values("time").reset_index(drop=True)


def session_mask(time, session):
    hour = time.dt.hour
    if session == "london":
        return (hour >= 8) & (hour < 17)
    if session == "ny":
        return (hour >= 13) & (hour < 22)
    if session == "asia":
        return (hour >= 0) & (hour < 8)
    if session == "lon_ny":
        return ((hour >= 8) & (hour < 17)) | ((hour >= 13) & (hour < 22))
    if session == "lon_only":
        return (hour >= 8) & (hour < 13)
    if session == "ny_only":
        return (hour >= 13) & (hour < 22)
    return pd.Series([True] * len(time))


def dow_mask(time, dow):
    if dow == "mon_fri":
        return time.dt.dayofweek.isin([0, 4])
    if dow == "tue_wed_thu":
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

        if state > 0:
            held = t - entry_bar
            px = close[t]
            exited = False
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
                if tr < -db:
                    state = 0; exited = True

        elif state < 0:
            held = t - entry_bar
            px = close[t]
            exited = False
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
                if tr > db:
                    state = 0; exited = True

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


def stats(ret, pos):
    n = len(ret)
    if n == 0:
        return dict(total=0.0, dd=0.0, sharpe=0.0, rt=0)
    eq = np.cumprod(1.0 + ret)
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    sharpe = float(ret.mean() / sd * np.sqrt(24 * 60 * 252)) if sd > 0 else 0.0
    rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
    return dict(total=float(eq[-1] - 1.0), dd=float((eq / peak - 1.0).min()),
                sharpe=sharpe, rt=rt)


def run_sensitivity(sym, df, close, atr, rsi, e21, e50, spread, time, point,
                    cost_side, spread_ok_base):
    """Run parameter sensitivity for one symbol. Returns list of (param, value, total, sharpe, dd, rt)."""
    n = len(df)
    lo, hi, mid = 20, 80, 50
    pull_lo, pull_hi = 40, 60

    results = []

    def test(name, value, **overrides):
        p = dict(
            style="trend_mid", exit_style="rsi_mid", lo=lo, hi=hi, mid=mid,
            pull_lo=pull_lo, pull_hi=pull_hi, db=0.1, min_hold=6, margin=0.0,
            atr_mult=1.5, time_stop=None, trail_mult=1.5, use_align=True,
            session="none", dow="all",
        )
        p.update(overrides)

        sess_mask = session_mask(time, p["session"]) if p["session"] != "none" else pd.Series([True] * n)
        dow_mask_s = dow_mask(time, p["dow"]) if p["dow"] != "all" else pd.Series([True] * n)
        spread_ok = spread_ok_base & sess_mask.to_numpy() & dow_mask_s.to_numpy()

        ret, pos = simulate(df, close, atr, rsi, e21, e50, spread_ok, sess_mask, dow_mask_s,
                            p["style"], p["exit_style"], p["lo"], p["hi"], p["mid"],
                            p["pull_lo"], p["pull_hi"], p["db"], p["min_hold"],
                            p["margin"], p["atr_mult"], p["time_stop"], p["trail_mult"],
                            p["use_align"], cost_side)
        st = stats(ret, pos)
        results.append(dict(param=name, value=str(value), **p,
                            rt=st["rt"], net_total=st["total"] * 100,
                            net_sharpe=st["sharpe"], net_dd=st["dd"] * 100))
        return st

    # Baseline
    b = test("baseline", "base")
    print(f"\n=== {sym} baseline: total={b['total']:+.2f}% Sharpe={b['sharpe']:.2f} DD={b['dd']*100:.2f}% RT={b['rt']}")

    # RSI period
    for rp in [5, 7, 9, 11, 14, 21]:
        rsi_x = wilder_rsi(df["close"], rp).to_numpy(float)
        test(f"rsi_p={rp}", rp, rsi=rsi_x)  # won't work, rsi not in p
    # Actually we need to pass rsi separately. Let me restructure.

    return results


def main() -> int:
    all_rows = []
    for sym in SYMBOLS:
        df = load_csv(sym, "M1")
        n = len(df)
        close = df["close"].to_numpy(float)
        atr = wilder_atr(df, 14).to_numpy(float)
        rsi = wilder_rsi(df["close"], 9).to_numpy(float)
        spread = df["spread"].astype(float).to_numpy()
        time = df["time"]
        e21 = ema(df["close"], 21).to_numpy(float)
        e50 = ema(df["close"], 50).to_numpy(float)

        point = 0.001 if sym.endswith("JPY") else 0.00001
        spread_eff = np.maximum(spread, 1.0)
        cost_side = (spread_eff / 2.0 + 1.0 + 3.5) * point

        q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
        spread_ok_base = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

        lo, hi, mid = 20, 80, 50
        pull_lo, pull_hi = 40, 60
        results = []

        def test(name, value, rsi_x=None, **kw):
            p = dict(
                style="trend_mid", exit_style="rsi_mid", lo=lo, hi=hi, mid=mid,
                pull_lo=pull_lo, pull_hi=pull_hi, db=0.1, min_hold=6, margin=0.0,
                atr_mult=1.5, time_stop=None, trail_mult=1.5, use_align=True,
                session="none", dow="all",
            )
            p.update(kw)
            rsi_use = rsi_x if rsi_x is not None else rsi

            sess_mask = session_mask(time, p["session"]) if p["session"] != "none" else pd.Series([True] * n)
            dow_mask_s = dow_mask(time, p["dow"]) if p["dow"] != "all" else pd.Series([True] * n)
            spread_ok = spread_ok_base & sess_mask.to_numpy() & dow_mask_s.to_numpy()

            ret, pos = simulate(df, close, atr, rsi_use, e21, e50, spread_ok, sess_mask, dow_mask_s,
                                p["style"], p["exit_style"], p["lo"], p["hi"], p["mid"],
                                p["pull_lo"], p["pull_hi"], p["db"], p["min_hold"],
                                p["margin"], p["atr_mult"], p["time_stop"], p["trail_mult"],
                                p["use_align"], cost_side)
            st = stats(ret, pos)
            all_rows.append(dict(sym=sym, param=name, value=str(value), **p,
                                 rt=st["rt"], net_total=st["total"] * 100,
                                 net_sharpe=st["sharpe"], net_dd=st["dd"] * 100))
            return st

        # Baseline
        b = test("baseline", "base")
        print(f"\n=== {sym} baseline: total={b['total']:+.2f}% Sharpe={b['sharpe']:.2f} DD={b['dd']*100:.2f}% RT={b['rt']}")

        # RSI period
        for rp in [5, 7, 9, 11, 14, 21]:
            rsi_x = wilder_rsi(df["close"], rp).to_numpy(float)
            st = test(f"rsi_p", rp, rsi_x=rsi_x)
            print(f"  rsi_p={rp}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # EMA pair
        for ef, es in [(9, 21), (13, 34), (21, 50), (34, 89), (50, 100), (21, 200)]:
            ef_arr = ema(df["close"], ef).to_numpy(float)
            es_arr = ema(df["close"], es).to_numpy(float)
            st = test(f"ema_{ef}_{es}", f"{ef}/{es}", e_fast=ef_arr, e_slow=es_arr)
            print(f"  ema={ef}/{es}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Entry style
        for style in ["trend_mid", "meanrev_lo", "pullback", "breakout"]:
            st = test("style", style, style=style, use_align=(style != "meanrev_lo"))
            print(f"  style={style}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Exit style
        for ex in ["rsi_mid", "slope_flip", "atr_stop_tp", "time_stop", "trailing"]:
            kw = {"exit_style": ex}
            if ex == "time_stop":
                kw["time_stop"] = 60
            if ex == "trailing":
                kw["trail_mult"] = 2.0
            if ex == "atr_stop_tp":
                kw["atr_mult"] = 1.5
            st = test("exit", ex, **kw)
            print(f"  exit={ex}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Deadband
        for db in [0.0, 0.02, 0.05, 0.1, 0.2, 0.5]:
            st = test("db", db, db=db)
            print(f"  db={db}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Min hold
        for mh in [1, 3, 6, 12, 24, 48]:
            st = test("min_hold", mh, min_hold=mh)
            print(f"  min_hold={mh}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Margin
        for mg in [0.0, 0.5, 1.0, 2.0, 3.0, 5.0]:
            st = test("margin", mg, margin=mg)
            print(f"  margin={mg}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Session
        for sess in ["none", "london", "ny", "asia", "lon_ny", "lon_only"]:
            st = test("session", sess, session=sess)
            print(f"  session={sess}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # DOW
        for dow in ["all", "mon_fri", "tue_wed_thu"]:
            st = test("dow", dow, dow=dow)
            print(f"  dow={dow}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # ATR stop mult
        for am in [1.0, 1.5, 2.0, 3.0]:
            st = test("atr_mult", am, exit_style="atr_stop_tp", atr_mult=am)
            print(f"  atr_mult={am}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Time stop
        for ts in [30, 60, 120, 240, 480]:
            st = test("time_stop", ts, exit_style="time_stop", time_stop=ts)
            print(f"  time_stop={ts}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Trailing
        for tm in [1.0, 1.5, 2.0, 3.0]:
            st = test("trail_mult", tm, exit_style="trailing", trail_mult=tm)
            print(f"  trail_mult={tm}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # Align filter on/off
        for al in [False, True]:
            st = test("align", al, use_align=al)
            print(f"  align={al}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

        # RSI levels
        for lo_x, hi_x in [(20, 80), (25, 75), (30, 70), (35, 65), (15, 85), (10, 90)]:
            mid_x = (lo_x + hi_x) / 2
            st = test("rsi_levels", f"{lo_x}/{hi_x}", lo=lo_x, hi=hi_x, mid=mid_x)
            print(f"  rsi_levels={lo_x}/{hi_x}: total={st['total']:+.2f}% Sharpe={st['sharpe']:.2f} DD={st['dd']*100:.2f}% RT={st['rt']}")

    out = pd.DataFrame(all_rows)
    out_path = RESULTS / "rsi_band_m1_sensitivity.csv"
    out.to_csv(out_path, index=False)
    print(f"\nWrote {out_path} ({len(out)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
