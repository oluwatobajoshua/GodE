#!/usr/bin/env python3
"""Candle-structure exits: how many greens before a red, and which exit wins?

The idea being tested, in the words it was given:

    "if I'm buying, close the trade at the close of a green candle and enter
     again, so we can study the structure - after how many greens did we see a
     red candle. But we don't close after a red candle - we expect that another
     green will come and become bigger than that red, or close above the open of
     the red candle. Test so many exits that can help us have more winning trades
     than losing trades."

So this is an EXIT study, not an entry study. The entry is held fixed at the rule
that already measured gross-positive (RSI9 crossing back up through 50 while the
EMA21 slope is up), and only the exit changes. That is the only way to attribute a
difference to the exit.

PART 1 answers the question literally: given k consecutive green candles, what is
the probability the next candle is red, and does the next candle make a new high?
That is the raw structure the rule is built on.

PART 2 is a tournament of exits, all on the same entries and the same cost model:

  rsi75          baseline - leave when RSI is above 75
  green1         leave at the close of every green candle
  green2/3       leave at the close of the 2nd / 3rd consecutive green
  red1           leave at the close of the first red candle
  red_recover    leave only if, after a red candle, the next candle does NOT close
                 above that red candle's open  <- the actual idea
  red_low        leave only if a candle closes below the previous red's low
  rsi75_recover  leave at RSI>75, but hold through a red if it is reclaimed

Each is run with two re-entry modes, because they mean different things:
  immediate   re-enter the very next bar while the slope still agrees
              (the literal "close and enter again" - pays spread every cycle)
  fresh       re-enter only on a new RSI cross through 50
              (the exit is a real exit)

Cost is the real one: half the quoted spread (floored) + slippage + half
commission, per side. Win rate, average win, average loss and expectancy per trade
are reported, because "more winning trades than losing ones" is the question.

Usage
    .\\.venv\\Scripts\\python.exe research/candle_exit.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/candle_exit.py --symbol GBPUSD --tf H1
"""

from __future__ import annotations

import argparse
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


def exit_long(rule, t, o, c, h, l, rsi, hi):
    if rule == "rsi75":
        return rsi[t] > hi[t]
    if rule == "green1":
        return c[t] > o[t]
    if rule == "green2":
        return c[t] > o[t] and c[t - 1] > o[t - 1]
    if rule == "green3":
        return c[t] > o[t] and c[t - 1] > o[t - 1] and c[t - 2] > o[t - 2]
    if rule == "red1":
        return c[t] < o[t]
    if rule == "red_recover":
        #--- a red candle happened; did this candle reclaim its open?
        return (c[t - 1] < o[t - 1]) and (c[t] <= o[t - 1])
    if rule == "red_low":
        return (c[t - 1] < o[t - 1]) and (c[t] < l[t - 1])
    if rule == "rsi75_recover":
        if c[t - 1] < o[t - 1] and c[t] > o[t - 1]:
            return False           # reclaimed - hold through the pullback
        return rsi[t] > hi[t]
    raise ValueError(rule)


def exit_short(rule, t, o, c, h, l, rsi, lo):
    #--- mirror image: green <-> red, "above the open" <-> "below the open"
    if rule == "rsi75":
        return rsi[t] < lo[t]
    if rule == "green1":
        return c[t] < o[t]
    if rule == "green2":
        return c[t] < o[t] and c[t - 1] < o[t - 1]
    if rule == "green3":
        return c[t] < o[t] and c[t - 1] < o[t - 1] and c[t - 2] < o[t - 2]
    if rule == "red1":
        return c[t] > o[t]
    if rule == "red_recover":
        return (c[t - 1] > o[t - 1]) and (c[t] >= o[t - 1])
    if rule == "red_low":
        return (c[t - 1] > o[t - 1]) and (c[t] > h[t - 1])
    if rule == "rsi75_recover":
        if c[t - 1] > o[t - 1] and c[t] < o[t - 1]:
            return False
        return rsi[t] < lo[t]
    raise ValueError(rule)


def simulate(rule, reenter, rsi, mid, hi, lo, trend, o, c, h, l, n):
    pos = np.zeros(n, dtype=int)
    state = 0
    reenter_at = 10 ** 9
    for t in range(2, n):
        if not (np.isfinite(rsi[t]) and np.isfinite(mid[t]) and np.isfinite(hi[t])
                and np.isfinite(lo[t])):
            pos[t] = state
            continue
        if state > 0:
            if trend[t] < 0:
                state, reenter_at = 0, t + 1
            elif exit_long(rule, t, o, c, h, l, rsi, hi):
                state, reenter_at = 0, t + 1
        elif state < 0:
            if trend[t] > 0:
                state, reenter_at = 0, t + 1
            elif exit_short(rule, t, o, c, h, l, rsi, lo):
                state, reenter_at = 0, t + 1
        else:
            fresh_up = trend[t] > 0 and rsi[t - 1] <= mid[t - 1] and rsi[t] > mid[t]
            fresh_dn = trend[t] < 0 and rsi[t - 1] >= mid[t - 1] and rsi[t] < mid[t]
            again = (reenter == "immediate" and t >= reenter_at and trend[t] != 0)
            if fresh_up:
                state = 1
            elif fresh_dn:
                state = -1
            elif again:
                state = trend[t]
        pos[t] = state
    return pos


def trades_from(pos, c, cost_side):
    """Split the position series into round trips and return per-trade net returns."""
    n = len(pos)
    out = []
    entry_i = None
    for t in range(1, n):
        if pos[t] != 0 and entry_i is None:
            entry_i = t
        elif pos[t] == 0 and entry_i is not None:
            side = pos[entry_i]
            gross = (c[t] - c[entry_i]) * side / c[entry_i]
            net = gross - (cost_side[entry_i] + cost_side[t]) / c[entry_i]
            out.append((entry_i, t, side, net))
            entry_i = None
    if entry_i is not None:
        t = n - 1
        side = pos[entry_i]
        gross = (c[t] - c[entry_i]) * side / c[entry_i]
        net = gross - (cost_side[entry_i] + cost_side[t]) / c[entry_i]
        out.append((entry_i, t, side, net))
    return out


def curve(pos, c, cost_side):
    n = len(c)
    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (c[1:] / c[:-1] - 1.0)
    ret -= np.abs(np.diff(pos, prepend=0)) * (cost_side / c)
    return ret


def stats(ret, bars_per_year):
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
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    ap.add_argument("--deadband", type=float, default=0.10)
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
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
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

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > args.deadband, 1,
                     np.where(slope3 < -args.deadband, -1, 0))
    mid = np.full(n, 50.0)
    hi = np.full(n, 75.0)
    lo = np.full(n, 25.0)

    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252}[tf]

    print(f"=== candle-structure exits  {sym} {tf} ===")
    print(f"bars {n}  {time.iloc[0]:%Y-%m-%d} -> {time.iloc[-1]:%Y-%m-%d}  "
          f"entry fixed at RSI9 cross of 50 + EMA21 slope; only the exit changes")
    print(f"cost: median {2 * np.median(cost_side) / point:.1f} pts round trip")
    print("")

    # ---------------------------------------------------- 1. the raw structure
    green = (c > o)
    red = (c < o)
    print("--- 1. how many greens before a red? (the raw structure) ---")
    print(f"{'k greens in a row':<20}{'bars':>8}{'P(next red)':>13}"
          f"{'P(next new high)':>18}{'mean next %':>13}")
    print("-" * 72)
    run = np.zeros(n, dtype=int)
    for t in range(1, n):
        run[t] = run[t - 1] + 1 if green[t] else 0
    for k in (1, 2, 3, 4, 5, 6):
        idx = [t for t in range(k, n - 1) if run[t] == k]
        if not idx:
            continue
        p_red = np.mean([red[t + 1] for t in idx])
        p_hi = np.mean([h[t + 1] > h[t] for t in idx])
        mr = np.mean([(c[t + 1] - c[t]) / c[t] for t in idx]) * 100
        print(f"{k:<20}{len(idx):>8}{100 * p_red:>12.1f}%{100 * p_hi:>17.1f}%"
              f"{mr:>13.3f}")
    print(f"  (unconditional: P(red) {100 * red.mean():.1f}%, "
          f"P(new high) {100 * np.mean(h[1:] > h[:-1]):.1f}%)")
    print("")

    # ---------------------------------------------------- 2. exit tournament
    rules = ("rsi75", "green1", "green2", "green3", "red1",
             "red_recover", "red_low", "rsi75_recover")
    modes = ("immediate", "fresh")

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    def split_of(ts):
        return "design" if ts < edge1 else ("validation" if ts < edge2 else "oos")

    print("--- 2. exit tournament: same entries, only the exit differs ---")
    print(f"{'exit rule':<15}{'re-entry':<11}{'trades':>7}{'win%':>7}{'avgW%':>8}"
          f"{'avgL%':>8}{'exp%':>8}{'design':>8}{'valid':>7}{'oos':>7}{'Sharpe':>7}")
    print("-" * 93)
    results = []
    for rule in rules:
        for mode in modes:
            pos = simulate(rule, mode, rsi, mid, hi, lo, trend, o, c, h, l, n)
            ret = curve(pos, c, cost_side)
            tr = trades_from(pos, c, cost_side)
            if not tr:
                continue
            pnl = np.array([x[3] for x in tr])
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            sp = {s: stats(ret[((time >= bounds[s][0]) & (time < bounds[s][1])).to_numpy()],
                           bars_per_year) for s in SPLITS}
            full = stats(ret, bars_per_year)
            row = dict(rule=rule, mode=mode, trades=len(tr),
                       win=100 * (pnl > 0).mean(),
                       avg_w=100 * wins.mean() if len(wins) else 0.0,
                       avg_l=100 * losses.mean() if len(losses) else 0.0,
                       exp=100 * pnl.mean(),
                       design=sp["design"]["total"] * 100,
                       validation=sp["validation"]["total"] * 100,
                       oos=sp["oos"]["total"] * 100,
                       sharpe=full["sharpe"], dd=full["dd"] * 100)
            results.append(row)
            print(f"{rule:<15}{mode:<11}{len(tr):>7}{row['win']:>7.1f}"
                  f"{row['avg_w']:>8.3f}{row['avg_l']:>8.3f}{row['exp']:>8.3f}"
                  f"{row['design']:>8.2f}{row['validation']:>7.2f}"
                  f"{row['oos']:>7.2f}{row['sharpe']:>7.2f}")
    print("")

    tbl = pd.DataFrame(results)
    pos3 = tbl[(tbl.design > 0) & (tbl.validation > 0) & (tbl.oos > 0)]
    print(f"--- exits positive in ALL THREE splits: {len(pos3)} of {len(tbl)} ---")
    if len(pos3):
        for _, r in pos3.sort_values("exp", ascending=False).iterrows():
            print(f"  {r.rule:<15}{r['mode']:<11} exp {r.exp:>+7.3f}%  win {r.win:>5.1f}%  "
                  f"trades {r.trades:>5.0f}  design {r.design:>+6.2f}%  "
                  f"valid {r.validation:>+6.2f}%  oos {r.oos:>+6.2f}%")
    else:
        print("  none")
    print("")
    print("  exp% is the average net return per round trip - the number that decides")
    print("  whether 'more winners than losers' actually pays after the spread.")
    print("")

    if not args.no_publish and len(pos3):
        best = pos3.sort_values("exp", ascending=False).iloc[0]
        pos = simulate(best.rule, best["mode"], rsi, mid, hi, lo, trend, o, c, h, l, n)
        ret = curve(pos, c, cost_side)
        s = pd.Series(ret, index=pd.DatetimeIndex(time))
        publish_results(f"candle_exit_{sym}_{tf}_{best.rule}_{best['mode']}", s,
                        append_to_bundle=False)
        print(f"published candle_exit_{sym}_{tf}_{best.rule}_{best['mode']}")

    out = RESULTS / f"candle_exit_{sym}_{tf}.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    tbl.to_csv(out, index=False)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
