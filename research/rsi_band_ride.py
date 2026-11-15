#!/usr/bin/env python3
"""Ride the RSI band: buy the pullback to neutral, leave on extension, re-enter
on the next return to neutral - with bands that move with recent history.

The rule as it was described:

    "when I'm buying, low RSI value, something around 50 but not too high.
     Anything greater than 75 I consider almost over, but I'll enter again
     when the RSI comes down close to 50 again.
     This is also not static - it should be dynamic based on most recent history."

So three things are being claimed, and each is tested separately:

  1. ENTRY at the neutral band (around 50), not at the extreme. In an up-slope,
     buying when RSI crosses back UP through the middle of its own range is
     "entering early in the next leg" rather than chasing an extended one.
  2. EXIT at the upper band (around 75). Extension is treated as the end.
  3. The bands must be DYNAMIC. So the same rules are run twice: once with the
     fixed 50 / 75 numbers, and once with rolling quantiles of the last
     --window bars. If dynamic is genuinely better, it has to beat static in
     design, validation AND out-of-sample - otherwise it is just a different
     set of constants.

Re-entry is enforced by construction: after leaving at the high band, RSI is
above the middle, so the only way back in is for RSI to fall below the middle
and cross back up. That is the "enter again when RSI comes down close to 50".

Everything is decided at the close of the bar it is used on. The rolling
quantile at bar t uses bars up to and including t only. Cost is 12 points per
round trip, charged on every position change.

Usage
    .\\.venv\\Scripts\\python.exe research/rsi_band_ride.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/rsi_band_ride.py --symbol USDJPY --resample D1
    .\\.venv\\Scripts\\python.exe research/rsi_band_ride.py --symbol EURUSD --window 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import (  # noqa: E402
    ATR_P, EMA, EMA_MID, RSI_P, SMMA,
    ema, smma, wilder_atr, wilder_rsi,
)
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


def metrics(ret: np.ndarray, bars_per_year: float) -> dict:
    eq = np.cumprod(1.0 + ret)
    total = float(eq[-1] - 1.0)
    yrs = len(ret) / bars_per_year
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if yrs > 0 and eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    dd = float((eq / peak - 1.0).min())
    sd = ret.std()
    sharpe = float(ret.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0
    return dict(total=total, cagr=float(cagr), dd=dd, sharpe=sharpe)


def simulate(rsi, mid, hi, lo, trend, close, cost_px) -> tuple[np.ndarray, np.ndarray]:
    """One position at a time. Returns (position, band_used) arrays."""
    n = len(close)
    pos = np.zeros(n, dtype=int)
    state = 0
    for t in range(1, n):
        r, m, h, l = rsi[t], mid[t], hi[t], lo[t]
        rp, mp = rsi[t - 1], mid[t - 1]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue

        if state > 0:
            #--- in a long: leave on extension, or if the slope turns down
            if r > h or trend[t] < 0:
                state = 0
        elif state < 0:
            #--- in a short: leave on extension the other way, or if slope turns up
            if r < l or trend[t] > 0:
                state = 0
        else:
            #--- flat: enter on a cross back through the neutral band, with slope
            if trend[t] > 0 and rp <= mp and r > m:
                state = 1
            elif trend[t] < 0 and rp >= mp and r < m:
                state = -1
        pos[t] = state
    return pos


def equity(pos: np.ndarray, close: np.ndarray, cost_px: float) -> np.ndarray:
    """Mark-to-market bar returns, cost charged on every position change."""
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_px / close)
    return ret


def calibrate_q(rsi_s: pd.Series, target: float, window: int,
                lo: float, hi: float, up: bool) -> float:
    """Pick the rolling quantile whose average band equals `target`.

    Without this the comparison is not about adaptivity at all: a rolling p85 of
    RSI9 is about 66, not 75, so "dynamic" would simply mean "exit earlier and
    churn more". Calibrating first makes the only difference the fact that the
    band moves with recent history.
    """
    best_q, best_err = (hi if up else lo), 1e9
    for q in np.arange(lo, hi + 1e-9, 0.005):
        band = rsi_s.rolling(window, min_periods=window // 4).quantile(q)
        err = abs(float(band.mean()) - target)
        if np.isfinite(err) and err < best_err:
            best_err, best_q = err, float(q)
    return best_q


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--window", type=int, default=250,
                    help="bars of recent history used for the dynamic bands")
    ap.add_argument("--mid-q", type=float, default=0.50)
    ap.add_argument("--hi-q", type=float, default=0.85)
    ap.add_argument("--lo-q", type=float, default=0.15)
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--calibrate", action="store_true",
                    help="pick the rolling quantiles so the average dynamic band "
                         "equals the static 25/50/75, making adaptivity the only difference")
    ap.add_argument("--static-mid", type=float, default=50.0)
    ap.add_argument("--static-hi", type=float, default=75.0)
    ap.add_argument("--static-lo", type=float, default=25.0)
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
                      "close": "last", "tick_volume": "sum",
                      "spread": "median"})
                .dropna(subset=["open"]).reset_index())
        tf = args.resample

    df["ema"] = ema(df["close"], EMA)
    df["ema50"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "ema50", "smma", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    rsi_s = df["rsi"]
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > args.deadband, 1,
                     np.where(slope3 < -args.deadband, -1, 0))

    # ---- dynamic bands: rolling quantiles of this symbol's own recent RSI
    hi_q, lo_q = args.hi_q, args.lo_q
    if args.calibrate:
        hi_q = calibrate_q(rsi_s, 75.0, args.window, 0.60, 0.995, up=True)
        lo_q = calibrate_q(rsi_s, 25.0, args.window, 0.005, 0.40, up=False)
    mid = rsi_s.rolling(args.window, min_periods=args.window // 4).quantile(args.mid_q).to_numpy()
    hi = rsi_s.rolling(args.window, min_periods=args.window // 4).quantile(hi_q).to_numpy()
    lo = rsi_s.rolling(args.window, min_periods=args.window // 4).quantile(lo_q).to_numpy()

    # ---- static bands: the same rule with the numbers said out loud
    smid = np.full(n, args.static_mid)
    shi = np.full(n, args.static_hi)
    slo = np.full(n, args.static_lo)

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252}[tf]

    print(f"=== RSI band ride  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   RSI{RSI_P}   "
          f"dynamic window {args.window} bars   cost {args.cost_pts:.0f} pts")
    print(f"entry: RSI crosses UP through the mid band while slope is up "
          f"(mirror for shorts)")
    print(f"exit : RSI above the high band, or slope turns against")
    print("")

    print(f"--- what the dynamic bands actually are ({sym}) ---")
    print(f"{'':<10}{'p15':>8}{'p50':>8}{'p85':>8}")
    for lab, s in (("all", rsi_s), ("design", rsi_s[time < edge1]),
                   ("validation", rsi_s[(time >= edge1) & (time < edge2)]),
                   ("oos", rsi_s[time >= edge2])):
        print(f"{lab:<10}{s.quantile(args.lo_q):>8.1f}{s.quantile(args.mid_q):>8.1f}"
              f"{s.quantile(args.hi_q):>8.1f}")
    print(f"  fixed rule uses 25 / 50 / 75 - so the dynamic rule is only 'dynamic'")
    print(f"  to the extent these numbers differ from those.")
    if args.calibrate:
        print(f"  calibrated quantiles: mid p{args.mid_q * 100:.0f} "
              f"hi p{hi_q * 100:.1f} (mean {np.nanmean(hi):.1f})  "
              f"lo p{lo_q * 100:.1f} (mean {np.nanmean(lo):.1f})")
        print(f"  -> same average levels as 25/50/75, so only the ADAPTATION differs")
    print("")

    runs = {}
    for name, m, h, l in (("dynamic", mid, hi, lo), ("static", smid, shi, slo)):
        pos = simulate(rsi_s.to_numpy(), m, h, l, trend, close, cost_px)
        ret = equity(pos, close, cost_px)
        runs[name] = dict(pos=pos, ret=ret)
        changes = int(np.abs(np.diff(pos, prepend=0)).sum())
        held = int((pos != 0).sum())
        print(f"--- {name} bands ---")
        print(f"  round trips {changes // 2:>5}   bars in market {held:>6} "
              f"({100 * held / n:>4.1f}%)")
        for sp in SPLITS:
            a, b = bounds[sp]
            mask = ((time >= a) & (time < b)).to_numpy()
            mm = metrics(ret[mask], bars_per_year)
            print(f"  {sp:<11} total {mm['total'] * 100:>+7.1f}%   "
                  f"CAGR {mm['cagr'] * 100:>+6.2f}%   Sharpe {mm['sharpe']:>5.2f}   "
                  f"maxDD {mm['dd'] * 100:>6.1f}%")
        mm = metrics(ret, bars_per_year)
        print(f"  {'full':<11} total {mm['total'] * 100:>+7.1f}%   "
              f"CAGR {mm['cagr'] * 100:>+6.2f}%   Sharpe {mm['sharpe']:>5.2f}   "
              f"maxDD {mm['dd'] * 100:>6.1f}%")
        print("")

    print("--- does dynamic beat static? (per split, and it has to be all three) ---")
    wins = 0
    for sp in SPLITS:
        a, b = bounds[sp]
        mask = ((time >= a) & (time < b)).to_numpy()
        d = metrics(runs["dynamic"]["ret"][mask], bars_per_year)
        s = metrics(runs["static"]["ret"][mask], bars_per_year)
        better = d["total"] > s["total"]
        wins += better
        print(f"  {sp:<11} dynamic {d['total'] * 100:>+7.1f}%   "
              f"static {s['total'] * 100:>+7.1f}%   "
              f"{'dynamic better' if better else 'static better'}")
    print(f"  -> dynamic wins {wins}/3 splits. "
          f"{'Claim supported.' if wins == 3 else 'Claim NOT supported - the bands are not the edge.'}")
    print("")

    if not args.no_publish:
        for name in ("dynamic", "static"):
            s = pd.Series(runs[name]["ret"], index=pd.DatetimeIndex(time))
            publish_results(f"rsi_band_{sym}_{tf}_{name}", s, append_to_bundle=False)
            print(f"published rsi_band_{sym}_{tf}_{name}")

    out = RESULTS / f"rsi_band_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(dict(time=time, rsi=rsi_s, mid=mid, hi=hi, lo=lo, trend=trend,
                      pos_dynamic=runs["dynamic"]["pos"],
                      pos_static=runs["static"]["pos"])) \
        .to_csv(out, index=False)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
