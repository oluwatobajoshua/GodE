#!/usr/bin/env python3
"""EMA21 rubber band with divergence exit.

Enter close to EMA21 (green kiss in uptrend, red kiss in downtrend) + RSI filter.
Exit ONLY on RSI divergence: price makes new swing extreme but RSI fails to confirm.

Usage:
    .\.venv\Scripts\python.exe research\ema21_divergence.py --symbol EURUSD --tf H1 --sweep
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from backtest_core import simulate_stops, trade_metrics, wilder_atr, cost_per_side  # noqa: E402
from ema_cross_harvest import ema, wilder_rsi  # noqa: E402
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"


def add_indicators(df, rsi_period=14, ema_period=21, atr_p=14):
    df = df.copy()
    df["ema"] = ema(df["close"], ema_period)
    df["rsi"] = wilder_rsi(df["close"], rsi_period)
    df["atr"] = wilder_atr(df, atr_p)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)
    return df


def entry_ema_rubberband(df, ema_period=21, entry_atr=0.3, rsi_period=14,
                         rsi_max=65.0, rsi_min=35.0, require_green=True):
    """Enter close to EMA21 in trend direction + RSI filter."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    state = 0  # 0 flat, 1 long, -1 short

    for t in range(1, n):
        c, o, r, e, a = close[t], open_[t], rsi[t], ema[t], atr[t]
        if not (np.isfinite(e) and np.isfinite(a) and a > 0 and np.isfinite(r)):
            pos[t] = state
            continue
        entry_lo = e - entry_atr * a
        entry_hi = e + entry_atr * a
        green = bool(c > o)
        red = bool(c < o)

        if state == 0:
            if bool(entry_lo <= c <= e) and green and bool(r < rsi_max):
                pos[t] = 1
                state = 1
            elif bool(e <= c <= entry_hi) and red and bool(r > rsi_min):
                pos[t] = -1
                state = -1
            else:
                pos[t] = 0
        elif state > 0:
            pos[t] = 1
        else:
            pos[t] = -1
    return pos


def exit_divergence(df, side_series, lookback=5, rsi_period=14):
    """Exit on RSI divergence only.

    Long exit: price closes above prior swing high (lookback bars back)
               but RSI at current bar < RSI at swing high bar.
    Short exit: price closes below prior swing low but RSI > RSI at swing low.
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)

    exit_at = np.full(n, False, dtype=bool)

    # Track swing highs/lows
    for t in range(lookback + 1, n):
        c = close[t]
        r = rsi[t]
        if t >= n:
            break
        window_high = t - lookback
        window_low = t - lookback
        # find swing high in lookback window
        prev_high_idx = t - 1
        prev_low_idx = t - 1
        hi_val = close[t - 1]
        lo_val = close[t - 1]
        for j in range(max(0, t - lookback), t):
            if close[j] >= hi_val:
                hi_val = close[j]
                prev_high_idx = j
            if close[j] <= lo_val:
                lo_val = close[j]
                prev_low_idx = j
        if c > hi_val and rsi[prev_high_idx] > r:
            exit_at[t] = True  # bearish divergence on long
        elif c < lo_val and rsi[prev_low_idx] < r:
            exit_at[t] = True  # bullish divergence on short
    return exit_at


def run(symbol, tf, ema_period=21, entry_atr=0.3, rsi_period=14,
        rsi_max=65.0, rsi_min=35.0, lookback=5, require_green=True,
        stop_atr=1.5, tp_atr=2.0, comm_pts=7.0, slip_pts=1.0, floor_pts=1.0,
        publish=True, sweep_mode=False):
    path = DATA / f"{symbol}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    df = add_indicators(df, rsi_period=rsi_period, ema_period=ema_period, atr_p=14)

    n = len(df)
    t0, t1 = df["time"].iloc[0], df["time"].iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80

    pos_raw = entry_ema_rubberband(df, ema_period=ema_period, entry_atr=entry_atr,
                                   rsi_period=rsi_period, rsi_max=rsi_max,
                                   rsi_min=rsi_min, require_green=require_green)

    exit_at = exit_divergence(df, pos_raw, lookback=lookback)

    # Build final position series honoring divergence exits
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_price = 0.0
    entry_bar = -10 ** 9

    for t in range(1, n):
        sig = int(pos_raw[t])
        if state == 0:
            if sig != 0:
                state = sig
                entry_price = df["close"].to_numpy(float)[t]
                entry_bar = t
                pos[t] = state
        else:
            # divergence exit (only fires if it matches current side)
            div_exit = exit_at[t]
            if div_exit:
                # only exit if divergence direction matches our side
                c = df["close"].to_numpy(float)[t]
                r = df["rsi"].to_numpy(float)[t]
                # determine if this was a bearish (long exit) or bullish (short exit) div
                # by checking price relative to recent window
                look = max(0, t - 5)
                hi = df["close"].iloc[look:t].max() if look < t else c
                lo = df["close"].iloc[look:t].min() if look < t else c
                is_bearish = bool(c > hi)
                is_bullish = bool(c < lo)
                if (state > 0 and is_bearish) or (state < 0 and is_bullish):
                    state = 0
                    entry_price = 0.0
                    entry_bar = -10 ** 9
                    pos[t] = 0
                else:
                    pos[t] = state
            else:
                pos[t] = state

    rets, trades, tix = simulate_stops(df, pd.Series(pos, index=df.index), symbol,
                                       comm_pts=comm_pts, slip_pts=slip_pts,
                                       floor_pts=floor_pts, stop_atr=stop_atr,
                                       tp_atr=tp_atr)

    total = float((1 + rets.to_numpy()).prod() - 1)

    def period_ret(t_start, t_end):
        r = rets.to_numpy()
        idx = rets.index
        mask = (idx >= t_start) & (idx <= t_end)
        if not mask.any():
            return 0.0
        return float((1 + r[mask]).prod() - 1)

    d_ret = period_ret(t0, edge1)
    v_ret = period_ret(edge1, edge2)
    o_ret = period_ret(edge2, t1)
    m = trade_metrics(trades)
    all3 = d_ret > 0 and v_ret > 0 and o_ret > 0

    tag = f"ema_div_{ema_period}_{entry_atr}_{rsi_max}_{rsi_min}_lb{lookback}"
    print(f"=== {tag} {symbol} {tf} ===")
    print(f"  bars={n} trades={m['trades']} PF={m['pf']} WR={m['winrate']}%")
    print(f"  design {d_ret:+.2f}%  validation {v_ret:+.2f}%  OOS {o_ret:+.2f}%  full {total:+.2f}%  all3={all3}")
    print()

    if publish and not sweep_mode:
        publish_results(tag, rets, append_to_bundle=False)

    return {"tag": tag, "symbol": symbol, "tf": tf, "ema_period": ema_period,
            "entry_atr": entry_atr, "rsi_max": rsi_max, "rsi_min": rsi_min,
            "lookback": lookback, "design": d_ret, "validation": v_ret,
            "oos": o_ret, "full": total, "trades": m["trades"], "pf": m["pf"],
            "winrate": m["winrate"], "all3": all3}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--sweep", action="store_true", help="sweep parameter grid")
    ap.add_argument("--ema-period", type=int, default=21)
    ap.add_argument("--entry-atr", type=float, default=0.3)
    ap.add_argument("--rsi-period", type=int, default=14)
    ap.add_argument("--rsi-max", type=float, default=65.0)
    ap.add_argument("--rsi-min", type=float, default=35.0)
    ap.add_argument("--lookback", type=int, default=5,
                    help="bars back to find swing for divergence")
    ap.add_argument("--no-green", action="store_true")
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--tp-atr", type=float, default=2.0)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()

    if args.sweep:
        results = []
        for ema_p in (14, 21, 50):
            for entry_a in (0.2, 0.3, 0.5, 0.8):
                for rmax, rmin in ((65.0, 35.0), (70.0, 30.0), (60.0, 40.0)):
                    for lb in (3, 5, 8):
                        r = run(sym, tf, ema_period=ema_p, entry_atr=entry_a,
                                rsi_period=args.rsi_period, rsi_max=rmax, rsi_min=rmin,
                                lookback=lb, require_green=not args.no_green,
                                stop_atr=args.stop_atr, tp_atr=args.tp_atr,
                                comm_pts=args.comm_pts, slip_pts=args.slip_pts,
                                floor_pts=args.floor_pts,
                                publish=False, sweep_mode=True)
                        results.append(r)
        print("=== SWEEP SUMMARY (sorted by OOS desc) ===")
        print(f"  {'ema':>4} {'entry':>6} {'rmax':>5} {'rmin':>5} {'lb':>4} "
              f"{'design':>8} {'valid':>8} {'OOS':>8} {'full':>8} {'trades':>7} {'PF':>5} {'all3':>5}")
        for r in sorted(results, key=lambda x: x["oos"], reverse=True):
            print(f"  {r['ema_period']:>4} {r['entry_atr']:>6.1f} {r['rsi_max']:>5.0f} "
                  f"{r['rsi_min']:>5.0f} {r['lookback']:>4} "
                  f"{r['design']:>+8.2f} {r['validation']:>+8.2f} {r['oos']:>+8.2f} "
                  f"{r['full']:>+8.2f} {r['trades']:>7} "
                  f"{str(r['pf']):>5} {str(r['all3']):>5}")
    else:
        run(sym, tf, ema_period=args.ema_period, entry_atr=args.entry_atr,
            rsi_period=args.rsi_period, rsi_max=args.rsi_max, rsi_min=args.rsi_min,
            lookback=args.lookback, require_green=not args.no_green,
            stop_atr=args.stop_atr, tp_atr=args.tp_atr,
            comm_pts=args.comm_pts, slip_pts=args.slip_pts,
            floor_pts=args.floor_pts, publish=not args.no_publish)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
