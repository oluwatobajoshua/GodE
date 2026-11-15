#!/usr/bin/env python3
"""How long does a move still have left, and what does being late cost?

The claim being tested:

    "we should look for the signs that tell us how long this will still
     continue - and that is why it is important to enter early, so we
     always capture the beginning of every move"

So there are two separate, measurable questions, and this script keeps them apart:

  1. THE DECAY CURVE. At the bar a direction begins, what is the forward return
     at offset 0, 1, 2, 3, ... 30 bars? If the curve keeps climbing, the move has
     duration and holding is right. If it peaks and rolls over, there is an exit
     horizon and holding past it gives money back. The *marginal* per-bar drift
     is what decides: the last bar you should have been in is the last bar with
     positive marginal drift.

  2. THE COST OF BEING LATE. Enter at offset d, exit at a fixed offset 20.
     The return difference between d=0 and d=1,2,3 is exactly the money that
     "entering early" is worth. If it is large, timing the start matters more
     than picking the direction.

Both are computed only from onsets that are observable at the time: the direction
is the EMA21 slope with a deadband, and an onset is the bar where that slope
changes sign. No swing, level or indicator value from a later bar is used.

Then the exhaustion side: at each offset, does an already-extended RSI / an
already-large distance from the EMA predict that the rest of the move is dead?
That is the "sign it is about to be over" question, and it is answered by
splitting the remaining drift on those conditions.

Finally the exit horizon is FITTED on design and then applied unchanged to
validation and out-of-sample, so a good exit cannot be a fitted one.

Usage
    .\\.venv\\Scripts\\python.exe research/move_decay.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/move_decay.py --symbol USDJPY --resample D1
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
OFFSETS = (0, 1, 2, 3, 5, 8, 10, 15, 20, 30)


def regime_series(slope: np.ndarray, deadband: float) -> np.ndarray:
    """+1 / -1 direction with a deadband, so a wiggle in the slope cannot flip it."""
    reg = np.zeros(len(slope), dtype=int)
    cur = 0
    for i, s in enumerate(slope):
        if s > deadband:
            cur = 1
        elif s < -deadband:
            cur = -1
        reg[i] = cur
    return reg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--cost-pts", type=float, default=12.0)
    ap.add_argument("--max-offset", type=int, default=30)
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
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    reg = regime_series(slope3, args.deadband)

    # ---- onsets: the bar where the direction actually changes. Causal by design.
    onsets = [t for t in range(1, n) if reg[t] != reg[t - 1] and reg[t] != 0]
    onsets = [t for t in onsets if t < n - args.max_offset - 1]

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    def fwd(t: int, j: int) -> float:
        """Net return from close[t] to close[t+j], signed by the direction at t."""
        j = min(j, n - 1 - t)
        s = reg[t]
        return (close[t + j] - close[t]) * s / close[t] - cost_px / close[t]

    print(f"=== move decay and the cost of being late  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   onsets {len(onsets)}   "
          f"deadband {args.deadband}   cost {args.cost_pts:.0f} pts")
    print(f"onset = the bar the EMA21 slope flips sign (observable at that close)")
    print("")

    # ------------------------------------------------------- 1. the decay curve
    print("--- 1. cumulative net return from the onset bar, by bars held ---")
    print(f"{'bars held':<11}" + "".join(f"{'d' + str(o):>8}" for o in OFFSETS))
    print("-" * (11 + 8 * len(OFFSETS)))
    curve_all = {}
    for sp in SPLITS:
        sub = [t for t in onsets if split_of(time.iloc[t]) == sp]
        line = f"{sp:<11}"
        for o in OFFSETS:
            v = np.mean([fwd(t, o) for t in sub]) * 100 if sub else np.nan
            curve_all[(sp, o)] = v
            line += f"{v:>8.3f}"
        print(line)
    print("")

    print("--- marginal drift: what the NEXT bar adds, given you are already in ---")
    print(f"{'from d':<11}" + "".join(f"{'->d' + str(o):>8}" for o in OFFSETS[1:]))
    print("-" * (11 + 8 * (len(OFFSETS) - 1)))
    for sp in SPLITS:
        line = f"{sp:<11}"
        for prev_o, o in zip(OFFSETS[:-1], OFFSETS[1:]):
            prev = curve_all[(sp, prev_o)]
            cur = curve_all[(sp, o)]
            line += f"{cur - prev:>8.3f}"
        print(line)
    print("  the exit horizon is the last offset whose marginal drift is still positive")
    print("")

    # -------------------------------------------------- 2. cost of being late
    print("--- 2. the cost of being late: enter at offset d, exit at a fixed 20 ---")
    print(f"{'enter at':<11}{'design':>9}{'valid':>9}{'oos':>9}{'all':>9}")
    print("-" * 47)
    late_rows = []
    for d in (0, 1, 2, 3, 5, 8):
        row = {}
        for sp in SPLITS:
            sub = [t for t in onsets if split_of(time.iloc[t]) == sp]
            row[sp] = np.mean([fwd(t, 20) - fwd(t, d) for t in sub]) * 100 if sub else np.nan
        allv = np.mean([fwd(t, 20) - fwd(t, d) for t in onsets]) * 100
        late_rows.append(dict(enter_offset=d, **row, all=allv))
        print(f"{'d=' + str(d):<11}{row['design']:>9.3f}{row['validation']:>9.3f}"
              f"{row['oos']:>9.3f}{allv:>9.3f}")
    print("")
    base = late_rows[0]["all"]
    for r in late_rows[1:]:
        print(f"  entering {r['enter_offset']} bar(s) late costs "
              f"{base - r['all']:>5.3f}% of the 20-bar move on average")
    print("")

    # ------------------------------------------- 3. signs it is about to be over
    print("--- 3. does an already-extended move still pay? (remaining 10 bars) ---")
    print("    at each offset, split the remaining drift by whether the move is")
    print("    already stretched (RSI beyond 75/25, or >2 ATR from EMA21)")
    print(f"{'offset':<9}{'stretched n':>12}{'rest %':>9}{'normal n':>10}"
          f"{'rest %':>9}{'gap':>8}")
    print("-" * 57)
    for o in (2, 3, 5, 8, 10):
        stretched, normal = [], []
        for t in onsets:
            if t + o + 10 >= n:
                continue
            s = reg[t]
            rest = (close[t + o + 10] - close[t + o]) * s / close[t + o] - cost_px / close[t + o]
            rsi_ext = (rsi[t + o] - 50) * s > 25
            dist_ext = (close[t + o] - e[t + o]) * s / atr[t + o] > 2.0
            (stretched if (rsi_ext or dist_ext) else normal).append(rest)
        if stretched and normal:
            a, b = np.mean(stretched) * 100, np.mean(normal) * 100
            print(f"{'d' + str(o):<9}{len(stretched):>12}{a:>9.3f}{len(normal):>10}"
                  f"{b:>9.3f}{a - b:>8.3f}")
    print("")

    # ------------------------------- 4. exit horizon fitted on design, then frozen
    print("--- 4. exit horizon fitted on DESIGN only, then applied unchanged ---")
    design_curve = {o: curve_all[("design", o)] for o in OFFSETS}
    best = max(design_curve, key=lambda o: design_curve[o])
    print(f"  design picks: hold {best} bars "
          f"(design {design_curve[best]:.3f}%)")
    for sp in SPLITS:
        print(f"  {sp:<11} hold-{best} {curve_all[(sp, best)]:>7.3f}%   "
              f"hold-20 {curve_all[(sp, 20)]:>7.3f}%   "
              f"difference {curve_all[(sp, best)] - curve_all[(sp, 20)]:>+7.3f}")
    print("")

    # ------------------------------------------------------- 5. calendar proof
    if not args.no_publish:
        for o in (best, 20):
            s = pd.Series([fwd(t, o) for t in onsets],
                          index=pd.DatetimeIndex([time.iloc[t] for t in onsets]))
            publish_results(f"move_decay_{sym}_{tf}_h{o}", s, append_to_bundle=False)
            print(f"published move_decay_{sym}_{tf}_h{o} ({len(s)} onsets)")

    out = RESULTS / f"move_decay_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(time=time.iloc[t], split=split_of(time.iloc[t]),
                       side=int(reg[t]),
                       **{f"d{o}": fwd(t, o) for o in OFFSETS}) for t in onsets]
                 ).to_csv(out, index=False)
    print(f"wrote {out}")
    print("")
    print("Read: if the decay curve climbs and the marginal drift stays positive, the")
    print("move has duration and the job is to hold. If it rolls over at a specific")
    print("offset, that offset is the exit - and part 2 says what being late costs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
