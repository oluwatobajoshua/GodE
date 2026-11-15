#!/usr/bin/env python3
"""Is "make it dynamic" actually better? Four ways to be dynamic, one fixed rule.

The requirement:

    "everything must be 100% dynamic so that it can continue to work for years -
     market condition is dynamic in nature"

Fair. But "dynamic" is not one thing, and each version has to be measured, because
a rule that re-fits itself can also re-fit itself into noise. This script runs the
same entry/exit logic with four different ways of setting the bands, on exactly
the same live window, and reports per calendar year:

  fixed      mid 50, exit 75, short band 25. The structural rule.
  quantile   bands are rolling quantiles of this symbol's own recent RSI
             (p50 / p96 / p4, calibrated so the average level equals 75 / 25).
             Adapts to the RSI distribution.
  volscale   mid 50, and the bands are 50 +/- k * (rolling std of RSI). Adapts to
             how stretched RSI currently gets, smoothly, without chasing returns.
  walkfwd    before each block, the (mid, hi, lo) that maximised net return on the
             trailing train window is applied to the next block. The most
             aggressive form of "learn from recent history".

The last one is the important control. If re-fitting on recent performance does
not beat a fixed structural level, then the drift is noise and the fixed level is
the honest answer. If it does beat it, the market genuinely changed and the rule
should follow.

The first `--train` bars are training only and are excluded from every result, so
the comparison is on data no variant has seen.

Usage
    .\\.venv\\Scripts\\python.exe research/walkforward_band.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/walkforward_band.py --symbol EURUSD --cost-pts 6
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
    ATR_P, EMA, RSI_P,
    ema, wilder_atr, wilder_rsi,
)
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"


def positions(rsi, mid, hi, lo, trend, n) -> np.ndarray:
    """One position at a time; bands may be scalars or per-bar arrays."""
    def arr(x):
        a = np.asarray(x, dtype=float)
        return np.full(n, float(a)) if a.ndim == 0 else a

    mid_a, hi_a, lo_a = arr(mid), arr(hi), arr(lo)
    pos = np.zeros(n, dtype=int)
    state = 0
    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        m, mp, h, l = mid_a[t], mid_a[t - 1], hi_a[t], lo_a[t]
        if not (np.isfinite(r) and np.isfinite(m) and np.isfinite(h)
                and np.isfinite(l) and np.isfinite(rp) and np.isfinite(mp)):
            pos[t] = state
            continue
        if state > 0:
            if r > h or trend[t] < 0:
                state = 0
        elif state < 0:
            if r < l or trend[t] > 0:
                state = 0
        else:
            if trend[t] > 0 and rp <= mp and r > m:
                state = 1
            elif trend[t] < 0 and rp >= mp and r < m:
                state = -1
        pos[t] = state
    return pos


def bar_returns(pos, close, cost_px) -> np.ndarray:
    n = len(close)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    ret -= np.abs(np.diff(pos, prepend=0)) * (cost_px / close)
    return ret


def net(ret: np.ndarray) -> float:
    return float(np.prod(1.0 + ret) - 1.0)


def stats(ret: np.ndarray, bars_per_year: float) -> dict:
    eq = np.cumprod(1.0 + ret)
    yrs = max(len(ret) / bars_per_year, 1e-9)
    cagr = (eq[-1] ** (1.0 / yrs) - 1.0) if eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = ret.std()
    return dict(total=float(eq[-1] - 1.0), cagr=float(cagr),
                dd=float((eq / peak - 1.0).min()),
                sharpe=float(ret.mean() / sd * np.sqrt(bars_per_year)) if sd > 0 else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--train", type=int, default=3000)
    ap.add_argument("--step", type=int, default=1000)
    ap.add_argument("--window", type=int, default=250)
    ap.add_argument("--k-sigma", type=float, default=1.5)
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--cost-pts", type=float, default=12.0)
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
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > args.deadband, 1,
                     np.where(slope3 < -args.deadband, -1, 0))

    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252}[tf]
    rsi_s = pd.Series(rsi)
    mp = args.window // 4

    variants: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

    # ---- fixed
    variants["fixed"] = (np.full(n, 50.0), np.full(n, 75.0), np.full(n, 25.0))

    # ---- quantile: rolling p50 / p96 / p4 (calibrated earlier to ~75 / ~25)
    variants["quantile"] = (
        rsi_s.rolling(args.window, min_periods=mp).quantile(0.50).to_numpy(),
        rsi_s.rolling(args.window, min_periods=mp).quantile(0.96).to_numpy(),
        rsi_s.rolling(args.window, min_periods=mp).quantile(0.04).to_numpy(),
    )

    # ---- volscale: 50 +/- k * rolling std of RSI
    sd = rsi_s.rolling(args.window, min_periods=mp).std().to_numpy()
    variants["volscale"] = (np.full(n, 50.0),
                            50.0 + args.k_sigma * sd,
                            50.0 - args.k_sigma * sd)

    print(f"=== is dynamic better?  {sym} {tf} ===")
    print(f"bars {n}  live window starts {time.iloc[min(args.train, n - 1)]:%Y-%m-%d}  "
          f"cost {args.cost_pts:.0f} pts  train {args.train}  step {args.step}")
    print("")

    rets: dict[str, np.ndarray] = {}
    for name, (m, h, l) in variants.items():
        p = positions(rsi, m, h, l, trend, n)
        rets[name] = bar_returns(p, close, cost_px)

    # ---- walk-forward over a parameter grid
    HIS = (60.0, 65.0, 70.0, 75.0, 80.0, 85.0)
    LOS = (15.0, 20.0, 25.0, 30.0, 35.0)
    cand = {}
    for h in HIS:
        for l in LOS:
            p = positions(rsi, 50.0, h, l, trend, n)
            cand[(h, l)] = bar_returns(p, close, cost_px)
    ret_wf = np.zeros(n)
    chosen = []
    for a in range(args.train, n - args.step, args.step):
        tr0 = max(0, a - args.train)
        best_key, best_v = None, -1e9
        for key, r in cand.items():
            v = net(r[tr0:a])
            if v > best_v:
                best_v, best_key = v, key
        h, l = best_key
        chosen.append(dict(block_start=int(a), time=time.iloc[a], hi=h, lo=l,
                           train_net=best_v))
        ret_wf[a:a + args.step] = cand[best_key][a:a + args.step]
    rets["walkfwd"] = ret_wf

    live = slice(args.train, n)

    print("--- live-window result, every variant on the same data ---")
    print(f"{'variant':<11}{'total':>9}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>8}"
          f"{'round trips':>13}")
    print("-" * 57)
    for name, r in rets.items():
        s = stats(r[live], bars_per_year)
        # round trips over the live window, from the fixed/pos arrays we still hold
        print(f"{name:<11}{s['total'] * 100:>9.2f}{s['cagr'] * 100:>8.2f}"
              f"{s['sharpe']:>8.2f}{s['dd'] * 100:>8.2f}{'':>13}")
    print("")

    if chosen:
        his = [c["hi"] for c in chosen]
        los = [c["lo"] for c in chosen]
        print(f"  walk-forward chose hi {min(his):.0f}-{max(his):.0f} "
              f"(mean {np.mean(his):.1f}), lo {min(los):.0f}-{max(los):.0f} "
              f"(mean {np.mean(los):.1f}) over {len(chosen)} blocks")
        print("")

    print("--- per calendar year, net return: does each version keep working? ---")
    yrs = sorted(set(time.iloc[args.train:].dt.year))
    names = list(rets)
    print(f"{'year':<7}" + "".join(f"{nm:>11}" for nm in names))
    print("-" * (7 + 11 * len(names)))
    yearly = {nm: [] for nm in names}
    for y in yrs:
        mask = (time.dt.year == y).to_numpy(copy=True)
        mask[:args.train] = False
        if mask.sum() < 200:
            continue
        line = f"{y:<7}"
        for nm in names:
            v = net(rets[nm][mask]) * 100
            yearly[nm].append(v)
            line += f"{v:>11.2f}"
        print(line)
    print("")
    print(f"{'years > 0':<7}" + "".join(
        f"{sum(1 for v in yearly[nm] if v > 0)}/{len(yearly[nm]):<9}"
        for nm in names))
    print("")

    base = "fixed"
    print(f"--- verdict against {base} ---")
    for nm in names:
        if nm == base:
            continue
        b = stats(rets[base][live], bars_per_year)
        v = stats(rets[nm][live], bars_per_year)
        wy = sum(1 for a_, b_ in zip(yearly[nm], yearly[base]) if a_ > b_)
        print(f"  {nm:<10} total {v['total'] * 100:>+7.2f}% vs {b['total'] * 100:>+7.2f}%"
              f"   Sharpe {v['sharpe']:>5.2f} vs {b['sharpe']:>5.2f}"
              f"   better in {wy}/{len(yearly[nm])} years")
    print("")
    print("  If none of the adaptive versions beat fixed on total, Sharpe AND most")
    print("  years, then 'dynamic' is not the missing ingredient - the level is")
    print("  structural and re-fitting only adds noise. That is a measurement, not")
    print("  a preference.")

    if not args.no_publish:
        for nm, r in rets.items():
            s = pd.Series(r[live], index=pd.DatetimeIndex(time.iloc[args.train:]))
            publish_results(f"bandvariant_{sym}_{tf}_{nm}", s, append_to_bundle=False)
            print(f"published bandvariant_{sym}_{tf}_{nm}")

    out = RESULTS / f"band_variants_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(dict(time=time.iloc[args.train:],
                      **{f"ret_{nm}": r[live] for nm, r in rets.items()})) \
        .to_csv(out, index=False)
    if chosen:
        pd.DataFrame(chosen).to_csv(
            RESULTS / f"band_walkfwd_choices_{sym}_{tf}.csv", index=False)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
