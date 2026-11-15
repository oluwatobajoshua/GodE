#!/usr/bin/env python3
"""RSI curvature: does the line bending predict the next candle?

The observation being tested, as it was described:

    "when the RSI line is moving at a particular angle and then it curves - say 3
     green candles followed each other on RSI, no curve, but after another candle
     there's a curve. Between two candles there's a value that should be between
     them for the line to maintain a direction, but if that value is small then it
     curves. So once it curves it is a red candle that will follow it, or the
     direction is about to change, or there will be a little pullback,
     retracement or correction."

That "value that should be between them" is exactly the linear extrapolation.
Draw the RSI line through (t-2, r[t-2]) and (t-1, r[t-1]); to keep the same
direction, the next point must land on

        expected[t] = r[t-1] + (r[t-1] - r[t-2]) = 2*r[t-1] - r[t-2]

The gap between where RSI actually lands and where it had to land to stay straight is
the second difference, which is curvature:

        d1[t] = r[t] - r[t-1]            slope (the "angle")
        d2[t] = d1[t] - d1[t-1]          change of slope (the "curve")
        z[t]  = d2[t] / sd(d1, last 100) curvature in units of its own recent
                                          noise, so the threshold is not a static
                                          number and needs no hand-tuning

A positive z is the line bending upward, a negative z is the line bending downward.
The claim is that a downward bend while the line was rising is followed by a red
candle / a pullback / a change of direction.

Three things have to be separated, and this script separates them:

  PART 1  Is a bend followed by a red candle more often than chance?
  PART 2  Is that just the RSI LEVEL in disguise? High RSI decelerates naturally,
          so every bend near 75 would be explained without any curvature effect.
          This is the decisive test: hold the RSI level fixed, vary only the bend.
  PART 3  Does the bend predict a PULLBACK (1-5 bars) or a real DIRECTION CHANGE
          (20 bars)? Those are different trades and only one of them is worth
          acting on.
  PART 4  Money. Exit on a bend instead of at RSI>75, and skip entries where the
          line is already bending down. Net of the real cost model.

Usage
    .\\.venv\\Scripts\\python.exe research/rsi_curvature.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/rsi_curvature.py --symbol EURUSD --tf H1 --sweep
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


def pct(a: float) -> str:
    return f"{100 * a:>+7.3f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--curv-win", type=int, default=100, help="bars to scale curvature by")
    ap.add_argument("--curv-thresh", type=float, default=1.0)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--min-hold", type=int, default=12)
    ap.add_argument("--margin", type=float, default=3.0)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    if args.resample:
        rule = {"H4": "4h", "D1": "1D"}[args.resample]
        df = (df.set_index("time").resample(rule)
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "tick_volume": "sum", "spread": "max"})
                .dropna(subset=["open"]).reset_index())
        tf = args.resample

    df["ema"] = ema(df["close"], EMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

    o = df["open"].to_numpy(float)
    c = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    spread = df["spread"].astype(float).to_numpy()
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    spread_eff = np.maximum(spread, args.floor_pts)
    cost_side = (spread_eff / 2.0 + args.slip_pts + args.comm_pts / 2.0) * point

    # ---------------------------------------------------------------- curvature
    d1 = np.zeros(n)
    d1[1:] = rsi[1:] - rsi[:-1]
    d2 = np.zeros(n)
    d2[1:] = d1[1:] - d1[:-1]
    sd1 = pd.Series(d1).rolling(args.curv_win, min_periods=20).std().to_numpy()
    zcurv = np.where(sd1 > 1e-12, d2 / np.maximum(sd1, 1e-12), 0.0)

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > args.deadband, 1,
                     np.where(slope3 < -args.deadband, -1, 0))
    mid = np.full(n, 50.0)
    hi = np.full(n, 75.0)
    lo = np.full(n, 25.0)

    red = c < o
    green = c > o

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}
    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252}[tf]

    print(f"=== RSI curvature: does the bend predict the next candle?  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"curvature z = (d1[t]-d1[t-1]) / sd(d1, {args.curv_win} bars)   "
          f"a bend is |z| > {args.curv_thresh:.1f}")
    print(f"cost: median {2 * np.median(cost_side) / point:.1f} pts round trip")
    print("")

    # ------------------------------------------------- 1. the bend as a forecast
    print("--- 1. while RSI is RISING, does a downward bend precede a red candle? ---")
    print(f"{'condition':<34}{'bars':>8}{'P(next red)':>13}{'1-bar':>9}{'5-bar':>9}{'20-bar':>9}")
    print("-" * 82)

    def fwd(t: int, h: int) -> float:
        if t + h >= n:
            return np.nan
        return (c[t + h] - c[t]) / c[t]

    rising = d1 > 0
    base_mask = rising & np.isfinite(zcurv)
    buckets = [
        ("rising, bending down hard  (z<-1.5)", base_mask & (zcurv < -1.5)),
        ("rising, bending down       (z<-1)", base_mask & (zcurv < -1.0)),
        ("rising, straight-ish   (|z|<=0.5)", base_mask & (np.abs(zcurv) <= 0.5)),
        ("rising, bending up         (z>+1)", base_mask & (zcurv > 1.0)),
        ("rising, ALL", base_mask),
        ("not rising (all bars)", np.isfinite(zcurv) & ~rising),
    ]
    for name, m in buckets:
        idx = np.flatnonzero(m)
        idx = idx[idx < n - 21]
        if len(idx) < 50:
            continue
        p_red = red[idx + 1].mean()
        f1 = np.nanmean([fwd(t, 1) for t in idx])
        f5 = np.nanmean([fwd(t, 5) for t in idx])
        f20 = np.nanmean([fwd(t, 20) for t in idx])
        print(f"{name:<34}{len(idx):>8}{100 * p_red:>12.1f}%"
              f"{pct(f1):>9}{pct(f5):>9}{pct(f20):>9}")
    print(f"  (unconditional P(red) {100 * red.mean():.1f}%;  "
          f"a 1-bar move of +0.010% is about 1 point on EURUSD)")
    print("")

    # ------------------------------------ 2. is the bend just the RSI level again?
    print("--- 2. DECISIVE: hold the RSI level fixed, vary only the bend ---")
    print(f"{'RSI band':<12}{'bend':<14}{'bars':>8}{'P(next red)':>13}{'5-bar':>9}{'20-bar':>9}")
    print("-" * 65)
    bands = [(45, 55), (55, 65), (65, 75), (75, 85)]
    for a, b in bands:
        inband = (rsi >= a) & (rsi < b) & np.isfinite(zcurv)
        for label, m in (("down (z<-1)", inband & (zcurv < -1.0)),
                         ("flat (|z|<=1)", inband & (np.abs(zcurv) <= 1.0)),
                         ("up (z>+1)", inband & (zcurv > 1.0))):
            idx = np.flatnonzero(m)
            idx = idx[idx < n - 21]
            if len(idx) < 50:
                continue
            print(f"{a:>3}-{b:<8}{label:<14}{len(idx):>8}"
                  f"{100 * red[idx + 1].mean():>12.1f}%"
                  f"{pct(np.nanmean([fwd(t, 5) for t in idx])):>9}"
                  f"{pct(np.nanmean([fwd(t, 20) for t in idx])):>9}")
        print("")
    print("  If the 'down' row is only worse in the high bands, the bend is just the")
    print("  level. If it is worse in EVERY band, the bend carries its own information.")
    print("")

    # ------------------------------------------- 3. their exact observation: k runs
    print("--- 3. the exact observation: k rising RSI bars, then the line bends ---")
    print(f"{'k rising bars':<16}{'bars':>8}{'P(bend next)':>14}{'P(red next)':>13}"
          f"{'5-bar after bend':>18}")
    print("-" * 70)
    run = np.zeros(n, dtype=int)
    for t in range(1, n):
        run[t] = run[t - 1] + 1 if d1[t] > 0 else 0
    for k in (2, 3, 4, 5, 6):
        idx = np.flatnonzero((run == k) & np.isfinite(zcurv))
        idx = idx[idx < n - 6]
        if len(idx) < 50:
            continue
        bend = (d1[idx + 1] < d1[idx])
        after = idx[bend] if bend.any() else idx[:0]
        f5 = np.nanmean([fwd(t, 5) for t in after]) if len(after) else np.nan
        print(f"{k:<16}{len(idx):>8}{100 * bend.mean():>13.1f}%"
              f"{100 * red[idx + 1].mean():>12.1f}%{pct(f5):>18}")
    print("")

    # ------------------------------------------------------- 4. does it pay?
    def simulate(exit_rule, entry_filter, thr, min_hold, margin):
        pos = np.zeros(n, dtype=int)
        state = 0
        entry_bar = -10 ** 9
        for t in range(3, n):
            if not (np.isfinite(rsi[t]) and np.isfinite(zcurv[t])):
                pos[t] = state
                continue
            if state > 0:
                held = t - entry_bar
                if trend[t] < 0:
                    state = 0
                elif exit_rule == "rsi75":
                    if rsi[t] > hi[t] and held >= min_hold:
                        state = 0
                elif exit_rule == "curv":
                    if d1[t] > 0 and zcurv[t] < -thr and held >= min_hold:
                        state = 0
                elif exit_rule == "curv_any":
                    if zcurv[t] < -thr and held >= min_hold:
                        state = 0
                elif exit_rule == "curv_or_rsi75":
                    if held >= min_hold and ((d1[t] > 0 and zcurv[t] < -thr)
                                             or rsi[t] > hi[t]):
                        state = 0
            elif state < 0:
                held = t - entry_bar
                if trend[t] > 0:
                    state = 0
                elif exit_rule == "rsi75":
                    if rsi[t] < lo[t] and held >= min_hold:
                        state = 0
                elif exit_rule == "curv":
                    if d1[t] < 0 and zcurv[t] > thr and held >= min_hold:
                        state = 0
                elif exit_rule == "curv_any":
                    if zcurv[t] > thr and held >= min_hold:
                        state = 0
                elif exit_rule == "curv_or_rsi75":
                    if held >= min_hold and ((d1[t] < 0 and zcurv[t] > thr)
                                             or rsi[t] < lo[t]):
                        state = 0
            else:
                if trend[t] > 0 and rsi[t - 1] <= mid[t - 1] and rsi[t] > mid[t] + margin:
                    blocked = entry_filter == "skip_bend" and zcurv[t] < -thr
                    if not blocked:
                        state = 1
                        entry_bar = t
                elif trend[t] < 0 and rsi[t - 1] >= mid[t - 1] and rsi[t] < mid[t] - margin:
                    blocked = entry_filter == "skip_bend" and zcurv[t] > thr
                    if not blocked:
                        state = -1
                        entry_bar = t
            pos[t] = state
        return pos

    def curve(pos):
        ret = np.zeros(n)
        ret[1:] = pos[:-1] * (c[1:] / c[:-1] - 1.0)
        ret -= np.abs(np.diff(pos, prepend=0)) * (cost_side / c)
        return ret

    def stats(ret):
        eq = np.cumprod(1.0 + ret)
        yrs = max(len(ret) / bars_per_year, 1e-9)
        cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
        peak = np.maximum.accumulate(eq)
        sd = ret.std()
        return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                    dd=float((eq / peak - 1.0).min()),
                    sharpe=float(ret.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0)

    print("--- 4. money: exit on the bend, and skip entries already bending ---")
    print(f"{'exit':<16}{'entry filter':<14}{'thr':>5}{'RT':>6}{'design':>9}"
          f"{'valid':>8}{'oos':>8}{'Sharpe':>8}{'DD':>8}")
    print("-" * 82)
    rows = []
    configs = [("rsi75", "none", 1.0)]
    if args.sweep:
        for rule, thr in itertools.product(("curv", "curv_any", "curv_or_rsi75"),
                                           (0.5, 1.0, 1.5, 2.0, 3.0)):
            for filt in ("none", "skip_bend"):
                configs.append((rule, filt, thr))
        for thr in (1.0, 2.0, 3.0):
            configs.append(("rsi75", "skip_bend", thr))
    else:
        configs.append(("curv", "none", args.curv_thresh))

    for rule, filt, thr in configs:
        pos = simulate(rule, filt, thr, args.min_hold, args.margin)
        ret = curve(pos)
        rt = int(np.abs(np.diff(pos, prepend=0)).sum()) // 2
        sp = {}
        for s in SPLITS:
            m = ((time >= bounds[s][0]) & (time < bounds[s][1])).to_numpy()
            sp[s] = stats(ret[m])
        full = stats(ret)
        rows.append(dict(exit=rule, filt=filt, thr=thr, rt=rt,
                         design=sp["design"]["total"] * 100,
                         validation=sp["validation"]["total"] * 100,
                         oos=sp["oos"]["total"] * 100,
                         sharpe=full["sharpe"], dd=full["dd"] * 100,
                         worst_dv=min(sp["design"]["total"], sp["validation"]["total"]) * 100))
        print(f"{rule:<16}{filt:<14}{thr:>5.1f}{rt:>6}{sp['design']['total'] * 100:>9.2f}"
              f"{sp['validation']['total'] * 100:>8.2f}{sp['oos']['total'] * 100:>8.2f}"
              f"{full['sharpe']:>8.2f}{full['dd'] * 100:>8.2f}")
    print("")

    tbl = pd.DataFrame(rows)
    best = tbl.sort_values("worst_dv", ascending=False).iloc[0]
    surv = tbl[(tbl.design > 0) & (tbl.validation > 0) & (tbl.oos > 0)]
    print(f"--- positive in ALL THREE splits: {len(surv)} of {len(tbl)} ---")
    for _, r in surv.sort_values("worst_dv", ascending=False).head(8).iterrows():
        print(f"  {r.exit:<16}{r.filt:<14}thr {r.thr:.1f}  RT {r.rt:>4.0f}  "
              f"design {r.design:>+6.2f}%  valid {r.validation:>+6.2f}%  oos {r.oos:>+6.2f}%")
    print("")
    print(f"chosen on the worst of design+validation only: {best.exit} / {best.filt} / "
          f"thr {best.thr:.1f}  (design {best.design:+.2f}%, validation "
          f"{best.validation:+.2f}%, oos {best.oos:+.2f}%)")
    print("")

    if not args.no_publish:
        pos = simulate(best.exit, best.filt, best.thr, args.min_hold, args.margin)
        ret = curve(pos)
        s = pd.Series(ret, index=pd.DatetimeIndex(time))
        publish_results(f"rsi_curvature_{sym}_{tf}", s, append_to_bundle=False)
        print(f"published rsi_curvature_{sym}_{tf}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"rsi_curvature_{sym}_{tf}.csv"
    tbl.to_csv(out, index=False)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
