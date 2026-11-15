"""Ride the structure: hold while it keeps making new highs, leave when a pullback
fails to take out the high, re-enter when it takes the high again.

The rule in the words it was given:

  - while the market keeps making higher highs and higher lows, stay in
  - if it pulls back and the next push does NOT take out the previous high,
    close everything
  - then watch for the best place to re-enter, and re-enter as soon as the
    structure proves itself again (a new higher high)
  - keep riding until the direction itself changes

Everything is built on confirmed swings (a k-bar fractal is only known k bars
later), so the exit and the re-entry are both decisions that could actually have
been taken at that close.

Compared against:
  hold      one entry at the start of the leg, hold to the end of the direction
  struct    the rule above: exit on failed high / lower low, re-enter on new high
  struct_f  same, but re-entry fires as soon as price closes above the last
            confirmed high instead of waiting for a new confirmed swing

Cost: 12 pts per round trip, charged on every completed cycle.

Usage
    .\\.venv\\Scripts\\python.exe research/structure_ride.py --symbol EURUSD
    .\\.venv\\Scripts\\python.exe research/structure_ride.py --symbol USDJPY --resample D1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_pyramid import (  # noqa: E402
    EMA, EMA_MID, SMMA,
    book_curve, ema, legs_of, regime_series, seg_metrics, smma,
    wilder_atr, wilder_rsi,
)

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


def swings(high: np.ndarray, low: np.ndarray, k: int):
    n = len(high)
    out: dict[int, list[tuple[str, float]]] = {}
    for i in range(k, n - k):
        wh = high[i - k:i + k + 1]
        wl = low[i - k:i + k + 1]
        if high[i] >= wh.max() and high[i] > wh[:k].max():
            out.setdefault(i + k, []).append(("H", float(high[i])))
        if low[i] <= wl.min() and low[i] < wl[:k].min():
            out.setdefault(i + k, []).append(("L", float(low[i])))
    return out


def ride_leg(a: int, b: int, side: int, close: np.ndarray, atr: np.ndarray,
             ev: dict[int, list[tuple[str, float]]], n: int,
             mode: str, stop_atr: float) -> list[dict]:
    """One direction leg, one position at a time (serial re-entry, never stacked)."""
    units: list[dict] = []

    def open_at(t: int):
        units.append(dict(entry_i=t, entry=float(close[t]),
                          stop=float(close[t] - side * stop_atr * atr[t]),
                          exit_i=None, exit=None, kind="sat", w=1.0))

    def close_at(t: int, px: float):
        for u in units:
            if u["exit"] is None:
                u["exit_i"] = t
                u["exit"] = float(px)

    open_at(a)
    last_h = last_l = None
    pulled_back = False
    exits = {"failed_high": 0, "lower_low": 0}

    for t in range(a + 1, min(b + 1, n)):
        for kind, price in ev.get(t, []):
            holding = any(u["exit"] is None for u in units)

            if holding:
                if kind == "H":
                    if last_h is not None and price < last_h and pulled_back:
                        close_at(t, close[t])
                        exits["failed_high"] += 1
                    last_h = price
                    pulled_back = False
                else:
                    if last_l is not None and price < last_l:
                        close_at(t, close[t])
                        exits["lower_low"] += 1
                    last_l = price
                    pulled_back = True
            else:
                if kind == "H" and last_h is not None and price > last_h:
                    open_at(t)
                    last_h = price
                    pulled_back = False
                elif kind == "L":
                    last_l = price
                    pulled_back = True
                continue

            if holding:
                continue

        if mode == "struct_f" and not any(u["exit"] is None for u in units):
            if last_h is not None and close[t] > last_h and atr[t] > 0:
                open_at(t)

        for u in units:
            if u["exit"] is not None:
                continue
            hit = (close[t] <= u["stop"]) if side > 0 else (close[t] >= u["stop"])
            if hit:
                u["exit_i"] = t
                u["exit"] = u["stop"]

    for u in units:
        if u["exit"] is None:
            u["exit_i"] = min(b + 1, n - 1)
            u["exit"] = float(close[u["exit_i"]])
    return units, exits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--resample", default="", choices=["", "H4", "D1"])
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--stop-atr", type=float, default=1.5)
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
                      "close": "last", "tick_volume": "sum",
                      "spread": "median"})
                .dropna(subset=["open"]).reset_index())
        tf = args.resample

    df["ema"] = ema(df["close"], EMA)
    df["ema50"] = ema(df["close"], EMA_MID)
    df["smma"] = smma(df["close"], SMMA)
    df["rsi"] = wilder_rsi(df["close"], 9)
    df["atr"] = wilder_atr(df, 14)
    df = df.dropna(subset=["ema", "ema50", "smma", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    e = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    time = df["time"]
    n = len(df)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_px = args.cost_pts * point

    slope3 = np.zeros(n)
    slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
    reg = regime_series(slope3, args.deadband)
    legs = legs_of(reg)
    ev = swings(high, low, args.k)

    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2), "oos": (edge2, t1)}

    p0 = float(close[0])
    avail = sum(abs(close[b] - close[a]) / p0 for a, b in legs)

    print(f"=== riding the structure  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}   direction legs {len(legs)}   "
          f"fractal k={args.k} (confirmed {args.k} bars late)")
    print(f"exit on: pullback then failed high, or lower low.  "
          f"re-entry on: new high.  cost {args.cost_pts:.0f} pts/cycle")
    print("")

    results = {}
    recs_by_mode: dict[str, list[dict]] = {}
    for mode in ("hold", "struct", "struct_f"):
        recs = []
        ex_tot = {"failed_high": 0, "lower_low": 0}
        cycles = 0
        for a, b in legs:
            if mode == "hold":
                units = [dict(entry_i=a, entry=float(close[a]),
                              stop=float(close[a] - reg[a] * args.stop_atr * atr[a]),
                              exit_i=None, exit=None, kind="sat", w=1.0)]
                for u in units:
                    u["exit_i"] = min(b + 1, n - 1)
                    u["exit"] = float(close[u["exit_i"]])
                ex = {"failed_high": 0, "lower_low": 0}
            else:
                units, ex = ride_leg(a, b, int(reg[a]), close, atr, ev, n,
                                     mode, args.stop_atr)
            for k2 in ex_tot:
                ex_tot[k2] += ex[k2]
            cycles += max(len(units) - 1, 0)
            recs.append(dict(start=a, end=b, reg=int(reg[a]), units=units))
        eq = book_curve(df, recs, p0, cost_px, 1.0)
        pnl = float(eq[-1] - 1.0)
        nunits = sum(len(r["units"]) for r in recs)
        results[mode] = eq
        recs_by_mode[mode] = recs
        print(f"--- {mode:<9} entries {nunits:>5}  re-entries/leg {cycles / len(legs):>4.2f}  "
              f"total {pnl * 100:>+7.1f}%  capture {100 * pnl / avail if avail else 0:>5.1f}%")
        if mode != "hold":
            print(f"      exits: failed high {ex_tot['failed_high']}   "
                  f"lower low {ex_tot['lower_low']}")
        for sp in SPLITS:
            m = seg_metrics(time, eq, *bounds[sp])
            print(f"      {sp:<11} return {m['ret'] * 100:>+7.1f}%  "
                  f"CAGR {m['cagr'] * 100:>+6.2f}%  Sharpe {m['sharpe']:>5.2f}  "
                  f"maxDD {m['dd'] * 100:>6.1f}%")
        print("")

    # ---------------------------------------------------------------- control
    # If the struct gain is only "spend less time in the market", then leaving
    # after the same average number of bars - with no structure rule at all -
    # should capture most of it. If it does, structure adds nothing.
    holds = [u["exit_i"] - u["entry_i"]
             for r in recs_by_mode["struct"] for u in r["units"]]
    cut = max(1, int(round(float(np.mean(holds)))))
    recs = []
    for a, b in legs:
        j = min(a + cut, b + 1, n - 1)
        recs.append(dict(start=a, end=b, reg=int(reg[a]), units=[
            dict(entry_i=a, entry=float(close[a]),
                 stop=float(close[a] - reg[a] * args.stop_atr * atr[a]),
                 exit_i=j, exit=float(close[j]), kind="sat", w=1.0)]))
    eq_cut = book_curve(df, recs, p0, cost_px, 1.0)
    results["timecut"] = eq_cut
    pnl = float(eq_cut[-1] - 1.0)
    print(f"--- {'timecut':<9} entries {len(legs):>5}  exit after {cut} bars "
          f"(= mean struct holding), no structure rule")
    print(f"      total {pnl * 100:>+7.1f}%  capture {100 * pnl / avail if avail else 0:>5.1f}%")
    for sp in SPLITS:
        m = seg_metrics(time, eq_cut, *bounds[sp])
        print(f"      {sp:<11} return {m['ret'] * 100:>+7.1f}%  "
              f"CAGR {m['cagr'] * 100:>+6.2f}%  Sharpe {m['sharpe']:>5.2f}  "
              f"maxDD {m['dd'] * 100:>6.1f}%")
    print("")

    print("--- the rule only helps if the struct row beats hold in every split ---")
    for sp in SPLITS:
        h = seg_metrics(time, results["hold"], *bounds[sp])
        s = seg_metrics(time, results["struct"], *bounds[sp])
        c = seg_metrics(time, results["timecut"], *bounds[sp])
        print(f"  {sp:<11} hold {h['ret'] * 100:>+7.1f}%  ->  struct "
              f"{s['ret'] * 100:>+7.1f}%   ({s['ret'] * 100 - h['ret'] * 100:+.1f} pts)"
              f"   |  same-time control {c['ret'] * 100:>+7.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
