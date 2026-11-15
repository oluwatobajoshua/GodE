#!/usr/bin/env python3
"""EMA21 rubber band: RSI-extreme entry + divergence-only exit.

Entry: RSI < rsi_min (oversold) → long near EMA21; RSI > rsi_max (overbought) → short near EMA21.
Exit:  RSI divergence only — price makes new swing extreme but RSI fails to confirm.

Usage:
    .\.venv\Scripts\python.exe research\ema21_div2.py --symbol EURUSD --tf H1 --sweep
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


def entry_ema_rsi_extreme(df, ema_period=21, entry_atr=0.3, rsi_period=14,
                          rsi_max=70.0, rsi_min=30.0, require_green=True,
                          ema_filter=True):
    """Enter when RSI is at extreme (oversold/overbought).

    Long: RSI < rsi_min, price near EMA from below, green, (ema_filter: close > ema)
    Short: RSI > rsi_max, price near EMA from above, red, (ema_filter: close < ema)
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    state = 0

    for t in range(1, n):
        c, o, r, e, a = close[t], open_[t], rsi[t], ema[t], atr[t]
        if not (np.isfinite(e) and np.isfinite(a) and a > 0 and np.isfinite(r)):
            pos[t] = state
            continue
        entry_lo = e - entry_atr * a
        entry_hi = e + entry_atr * a
        green = bool(c > o)
        red = bool(c < o)
        above_ema = bool(c > e)
        below_ema = bool(c < e)

        if state == 0:
            # long: RSI oversold, price near EMA from below, green, above EMA (trend filter)
            long_ok = bool(entry_lo <= c <= e) and green and bool(r < rsi_min)
            if ema_filter:
                long_ok = long_ok and above_ema
            # short: RSI overbought, price near EMA from above, red, below EMA
            short_ok = bool(e <= c <= entry_hi) and red and bool(r > rsi_max)
            if ema_filter:
                short_ok = short_ok and below_ema

            if long_ok:
                pos[t] = 1
                state = 1
            elif short_ok:
                pos[t] = -1
                state = -1
            else:
                pos[t] = 0
        elif state > 0:
            pos[t] = 1
        else:
            pos[t] = -1
    return pos


def exit_divergence_only(df, lookback=5):
    """Return boolean array: True at bar where RSI divergence occurs.

    Bearish div (exit long): close makes new lookback high but RSI lower than RSI at that high.
    Bullish div (exit short): close makes new lookback low but RSI higher than RSI at that low.
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    div = np.zeros(n, dtype=bool)
    for t in range(lookback + 1, n):
        c = close[t]
        r = rsi[t]
        start = t - lookback
        hi = close[start:t].max()
        lo = close[start:t].min()
        hi_idx = start + int(np.argmax(close[start:t]))
        lo_idx = start + int(np.argmin(close[start:t]))
        if c > hi and r < rsi[hi_idx]:
            div[t] = True  # bearish div → exit long
        elif c < lo and r > rsi[lo_idx]:
            div[t] = True  # bullish div → exit short
    return div


def run(symbol, tf, ema_period=21, entry_atr=0.3, rsi_period=14,
        rsi_max=70.0, rsi_min=30.0, lookback=5, require_green=True,
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

    pos_raw = entry_ema_rsi_extreme(df, ema_period=ema_period, entry_atr=entry_atr,
                                    rsi_period=rsi_period, rsi_max=rsi_max,
                                    rsi_min=rsi_min, require_green=require_green)
    div = exit_divergence_only(df, lookback=lookback)

    close = df["close"].to_numpy(float)
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9

    for t in range(1, n):
        sig = int(pos_raw[t])
        if state == 0:
            if sig != 0:
                state = sig
                entry_bar = t
                pos[t] = state
        else:
            if div[t]:
                # Only exit if divergence matches side: long exits on bearish div
                # (price new lookback high), short exits on bullish div (price new low)
                look = max(0, t - lookback)
                hi = close[look:t].max() if look < t else close[t]
                lo = close[look:t].min() if look < t else close[t]
                is_bearish = bool(close[t] > hi)
                is_bullish = bool(close[t] < lo)
                if (state > 0 and is_bearish) or (state < 0 and is_bullish):
                    state = 0
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

    tag = f"ema_div2_{ema_period}_{entry_atr}_{rsi_max}_{rsi_min}_lb{lookback}"
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
    ap.add_argument("--rsi-max", type=float, default=70.0)
    ap.add_argument("--rsi-min", type=float, default=30.0)
    ap.add_argument("--lookback", type=int, default=5)
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
                for rmax, rmin in ((70.0, 30.0), (75.0, 25.0), (65.0, 35.0)):
                    for lb in (3, 5, 8, 12):
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
