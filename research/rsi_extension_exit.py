"""Does exiting when RSI9 is already high (and price stretched from EMA21) pay?

The chart claim, in your words: during a buy, once price is far enough from EMA21
and RSI is already above 75, it may keep going to 80, 85, 90, 95 - but it is
risky, so exiting there locks profit.

That is a precise, falsifiable question and this script answers it three ways:

  1. CONTINUATION
     After the first close above the RSI threshold while price is above EMA21,
     how often does RSI actually reach 80 / 85 / 90 / 95?

  2. HOLD vs EXIT
     Measured in ATR units from the trigger close:
        hold_to_end  - stay in until RSI falls back below (threshold - 5)
        oracle_max   - the best price the episode ever offered (upper bound)

  3. THE STRETCH CONDITION
     Same numbers split by how far price already is from EMA21 at the trigger,
     because "far enough" is the part of your rule that has to earn its keep.

Shorts are mirrored (RSI below 25 / 20 / 15 / 10, price below EMA21).

Cost: one spread charged on every exit (12 pts default).

Usage
    .\\.venv\\Scripts\\python.exe research\\rsi_extension_exit.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research\\rsi_extension_exit.py --symbol EURUSD --resample D1
    .\\.venv\\Scripts\\python.exe research\\rsi_extension_exit.py --symbol USDJPY --tf H1 --stretch 1.5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

EMA = 21
RSI_P = 9
ATR_P = 14
SPLITS = ("design", "validation", "oos")


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    v = series.to_numpy(float)
    n = len(v)
    out = np.full(n, np.nan)
    if n <= period:
        return pd.Series(out, index=series.index)
    g = np.zeros(n)
    l = np.zeros(n)
    for i in range(1, n):
        d = v[i] - v[i - 1]
        g[i] = max(d, 0.0)
        l[i] = max(-d, 0.0)
    ag = g[1 : period + 1].mean()
    al = l[1 : period + 1].mean()
    out[period] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + g[i]) / period
        al = (al * (period - 1) + l[i]) / period
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return pd.Series(out, index=series.index)


def wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    n = len(h)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    out = np.full(n, np.nan)
    if n <= period:
        return pd.Series(out, index=df.index)
    prev = tr[1 : period + 1].mean()
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return pd.Series(out, index=df.index)


def episodes(df: pd.DataFrame, rsi: np.ndarray, level: float, side: int,
             stretch: float, max_bars: int = 60) -> pd.DataFrame:
    """First bar that crosses `level` with price stretched from EMA21.

    The episode ends when RSI falls back by 5 points from the trigger level,
    or after max_bars. Everything after the trigger is future information, which
    is fine here because this is a measurement of what the exit gives up.
    """
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"]
    n = len(df)

    rows = []
    i = 1
    while i < n - 5:
        if side > 0:
            crossed = rsi[i] > level and rsi[i - 1] <= level
            aligned = close[i] > e[i]
            dist = (close[i] - e[i]) / atr[i]
        else:
            crossed = rsi[i] < level and rsi[i - 1] >= level
            aligned = close[i] < e[i]
            dist = (e[i] - close[i]) / atr[i]

        if not (crossed and aligned) or not np.isfinite(atr[i]) or dist < stretch:
            i += 1
            continue

        back = level - 5.0 if side > 0 else level + 5.0
        j = i + 1
        max_rsi = rsi[i]
        best = close[i]
        while j < min(i + max_bars, n):
            max_rsi = max(max_rsi, rsi[j]) if side > 0 else min(max_rsi, rsi[j])
            best = max(best, high[j]) if side > 0 else min(best, low[j])
            done = rsi[j] < back if side > 0 else rsi[j] > back
            if done:
                break
            j += 1

        entry = float(close[i])
        end_px = float(close[j])
        rows.append(dict(
            time=time.iloc[i], side=side, rsi=float(rsi[i]), dist=float(dist),
            entry=entry, atr=float(atr[i]), bars=j - i,
            max_rsi=float(max_rsi),
            hold_atr=(end_px - entry) * side / atr[i],
            best_atr=(best - entry) * side / atr[i],
            reached80=bool(max_rsi >= 80) if side > 0 else bool(max_rsi <= 20),
            reached85=bool(max_rsi >= 85) if side > 0 else bool(max_rsi <= 15),
            reached90=bool(max_rsi >= 90) if side > 0 else bool(max_rsi <= 10),
            reached95=bool(max_rsi >= 95) if side > 0 else bool(max_rsi <= 5),
        ))
        i = j + 1 if j > i else i + 1

    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--stretch", type=float, default=1.0,
                    help="minimum distance from EMA21 in ATR at the trigger")
    ap.add_argument("--cost-pts", type=float, default=12.0)
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
                      "close": "last", "tick_volume": "sum"})
                .dropna().reset_index())
        tf = args.resample

    df["ema"] = ema(df["close"], EMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, ATR_P)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_atr_pts = args.cost_pts * point

    time = df["time"]
    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    print(f"=== RSI{RSI_P} extension exit  {sym} {tf} ===")
    print(f"bars {len(df)}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   "
          f"stretch >= {args.stretch:.1f} ATR from EMA{EMA}   "
          f"cost {args.cost_pts:.0f} pts")
    print("")

    for level in (70.0, 75.0, 80.0, 85.0):
        longs = episodes(df, df["rsi"].to_numpy(float), level, 1, args.stretch)
        shorts = episodes(df, df["rsi"].to_numpy(float), 100.0 - level, -1, args.stretch)
        ep = pd.concat([longs, shorts], ignore_index=True)
        if ep.empty:
            print(f"--- RSI {level:.0f} : no episodes ---")
            continue
        ep["split"] = ep["time"].apply(split_of)
        cost = cost_atr_pts / ep["atr"]

        print(f"--- RSI {level:.0f} (short mirror {100 - level:.0f}) : "
              f"{len(ep)} episodes, {ep['bars'].median():.0f} bars median ---")
        print(f"  continuation   ->80 {ep.reached80.mean() * 100:5.1f}%   "
              f"->85 {ep.reached85.mean() * 100:5.1f}%   "
              f"->90 {ep.reached90.mean() * 100:5.1f}%   "
              f"->95 {ep.reached95.mean() * 100:5.1f}%")
        print(f"  {'split':<12}{'n':>5}{'hold ATR':>10}{'oracle ATR':>12}"
              f"{'win%':>7}{'exit@level':>12}")
        for sp in SPLITS:
            sub = ep[ep.split == sp]
            if sub.empty:
                continue
            hold = sub["hold_atr"] - cost.loc[sub.index]
            print(f"  {sp:<12}{len(sub):>5}{hold.mean():>10.2f}"
                  f"{sub['best_atr'].mean():>12.2f}"
                  f"{(hold > 0).mean() * 100:>7.1f}{0.00:>12.2f}")
        hold_all = ep["hold_atr"] - cost
        print(f"  {'all':<12}{len(ep):>5}{hold_all.mean():>10.2f}"
              f"{ep['best_atr'].mean():>12.2f}"
              f"{(hold_all > 0).mean() * 100:>7.1f}{0.00:>12.2f}")
        print("")

    # ------------------------------------------------ stretch sensitivity
    print(f"--- stretch sensitivity at RSI 75 (hold ATR after the trigger) ---")
    print(f"{'stretch':<10}{'n':>6}{'hold ATR':>10}{'oracle ATR':>12}{'win%':>7}{'->85%':>7}")
    print("-" * 52)
    for st in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0):
        longs = episodes(df, df["rsi"].to_numpy(float), 75.0, 1, st)
        shorts = episodes(df, df["rsi"].to_numpy(float), 25.0, -1, st)
        ep = pd.concat([longs, shorts], ignore_index=True)
        if ep.empty:
            continue
        cost = cost_atr_pts / ep["atr"]
        hold = ep["hold_atr"] - cost
        print(f"{st:<10.1f}{len(ep):>6}{hold.mean():>10.2f}"
              f"{ep['best_atr'].mean():>12.2f}{(hold > 0).mean() * 100:>7.1f}"
              f"{ep.reached85.mean() * 100:>6.1f}%")

    out = RESULTS / f"rsi_extension_exit_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    longs = episodes(df, df["rsi"].to_numpy(float), 75.0, 1, args.stretch)
    shorts = episodes(df, df["rsi"].to_numpy(float), 25.0, -1, args.stretch)
    pd.concat([longs, shorts], ignore_index=True).to_csv(out, index=False)
    print("")
    print(f"wrote {out}")
    print("")
    print("Read it this way: 'exit@level' is always 0.00 by definition - you banked")
    print("at the trigger. The question is whether 'hold ATR' is reliably negative.")
    print("If it is, your rule is right. If it is positive, the exit is leaving money.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
