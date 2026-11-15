#!/usr/bin/env python3
"""EMA21 rubber band (trend pullback) research.

Enter CLOSE to EMA21 (green candle kissing support in uptrend, red candle
kissing resistance in downtrend) + RSI filter. Exit FAR from EMA21
(price extended away). Re-enter close, exit far again.

Usage:
    .\.venv\Scripts\python.exe research\ema21_rubberband.py --symbol EURUSD --tf H1 --sweep
    .\.venv\Scripts\python.exe research\ema21_rubberband.py --symbol EURUSD --tf H1 \
        --ema-period 21 --entry-atr 0.3 --exit-atr 2.0 --rsi-max 65 --rsi-min 35
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

from backtest_core import simulate_stops, trade_metrics, validate_returns, wilder_atr, cost_per_side  # noqa: E402
from ema_cross_harvest import EMA, RSI_P, ema, wilder_rsi  # noqa: E402
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


# ---------------------------------------------------------------- indicators

def add_indicators(df, rsi_period=14, ema_period=21, atr_p=14):
    df = df.copy()
    df["ema"] = ema(df["close"], ema_period)
    df["rsi"] = wilder_rsi(df["close"], rsi_period)
    df["atr"] = wilder_atr(df, atr_p)
    df["ema_prev"] = df["ema"].shift(1)
    df = df.dropna(subset=["ema", "rsi", "atr", "ema_prev"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------- entry rule

def entry_ema_rubberband(df, ema_period=21, entry_atr=0.3, exit_atr=2.0,
                         rsi_period=14, rsi_max=65.0, rsi_min=35.0,
                         require_green=True, require_slope=True):
    """EMA21 rubber band: enter close to EMA in trend direction, exit far.

    Long: close within entry_atr*ATR below EMA, green candle, EMA slope up,
          RSI < rsi_max.
    Short: close within entry_atr*ATR above EMA, red candle, EMA slope down,
           RSI > rsi_min.
    Exit: long when close >= EMA + exit_atr*ATR; short when close <= EMA - exit_atr*ATR.
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    ema_prev = df["ema_prev"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    state = 0  # 0 flat, 1 long, -1 short
    entry_count = 0

    for t in range(1, n):
        c, o, r, e, a = close[t], open_[t], rsi[t], ema[t], atr[t]
        if not (np.isfinite(e) and np.isfinite(a) and a > 0 and np.isfinite(r)):
            pos[t] = state
            continue

        entry_lo = e - entry_atr * a
        entry_hi = e + entry_atr * a
        exit_lo = e - exit_atr * a
        exit_hi = e + exit_atr * a
        slope_up = bool(e > ema_prev[t])
        slope_down = bool(e < ema_prev[t])
        green = bool(c > o)
        red = bool(c < o)

        if state == 0:
            #--- long entry: close near EMA from below, green, RSI not overbought
            long_ok = bool(entry_lo <= c <= e) and green and bool(r < rsi_max)
            if require_slope:
                long_ok = long_ok and slope_up
            #--- short entry: close near EMA from above, red, RSI not oversold
            short_ok = bool(e <= c <= entry_hi) and red and bool(r > rsi_min)
            if require_slope:
                short_ok = short_ok and slope_down

            if long_ok:
                pos[t] = 1
                state = 1
                entry_count += 1
            elif short_ok:
                pos[t] = -1
                state = -1
                entry_count += 1
            else:
                pos[t] = 0
        elif state > 0:
            #--- long exit: close far above EMA
            if bool(c >= exit_hi):
                pos[t] = 0
                state = 0
            else:
                pos[t] = 1
        else:
            #--- short exit: close far below EMA
            if bool(c <= exit_lo):
                pos[t] = 0
                state = 0
            else:
                pos[t] = -1

    return pos


# ---------------------------------------------------------------- backtest

def run(symbol, tf, ema_period=21, entry_atr=0.3, exit_atr=2.0,
        rsi_period=14, rsi_max=65.0, rsi_min=35.0,
        require_green=True, require_slope=False,
        stop_atr=1.5, tp_atr=2.0, comm_pts=7.0, slip_pts=1.0, floor_pts=1.0,
        publish=True):
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

    pos = entry_ema_rubberband(df, ema_period=ema_period, entry_atr=entry_atr,
                               exit_atr=exit_atr, rsi_period=rsi_period,
                               rsi_max=rsi_max, rsi_min=rsi_min,
                               require_green=require_green,
                               require_slope=require_slope)

    #--- DEBUG
    print(f"  DEBUG ema_col_valid={int(df['ema'].notna().sum())} rsi_col_valid={int(df['rsi'].notna().sum())} "
          f"pos unique={sorted(set(pos.tolist()))[:5]} nonzero={int((pos != 0).sum())} len={len(pos)}")
    #--- manual check at first few bars
    close = df["close"].to_numpy(float); open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float); ema = df["ema"].to_numpy(float); atr = df["atr"].to_numpy(float)
    for t in range(1, 6):
        c, o, r, e, a = close[t], open_[t], rsi[t], ema[t], atr[t]
        entry_lo_t = e - entry_atr * a
        long_ok = (entry_lo_t <= c <= e) and (c > o) and r < rsi_max
        short_ok = (e <= c <= entry_lo_t + 2*entry_atr*a) and (c < o) and r > rsi_min
        print(f"  DEBUG bar {t}: close={c:.5f} ema={e:.5f} entry_lo={entry_lo_t:.5f} "
              f"open={o:.5f} rsi={r:.1f} atr={a:.5f} long_ok={long_ok} short_ok={short_ok}")

    #--- diagnostics
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema = df["ema"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    entry_lo = ema - entry_atr * atr
    near_ema = (entry_lo <= close) & (close <= ema) & (atr > 0)
    green = close > open_
    rsi_ok = rsi < rsi_max
    long_cand = near_ema & green & rsi_ok
    print(f"  diag: near_ema={int(near_ema.sum())} green={int(green.sum())} "
          f"rsi<{rsi_max}={int(rsi_ok.sum())} "
          f"long_cand={int(long_cand.sum())}  pos_nonzero={int((pos != 0).sum())}")

    validate_returns(pd.Series(0.0, index=df.index), df["close"], label="pos_series")

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

    print(f"=== EMA rubberband {symbol} {tf} | EMA{ema_period} entryATR={entry_atr} "
          f"exitATR={exit_atr} RSI{max(rsi_min,rsi_max) if False else rsi_max}/{rsi_min} "
          f"stop={stop_atr}TP={tp_atr} ===")
    print(f"  bars={n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}  trades={m['trades']}  "
          f"PF={m['pf']}  WR={m['winrate']}%")
    print(f"  design   {d_ret:+.2f}%  validation {v_ret:+.2f}%  OOS {o_ret:+.2f}%  "
          f"full {total:+.2f}%")
    all3 = d_ret > 0 and v_ret > 0 and o_ret > 0
    print(f"  all_3_splits={all3}")
    print()

    if publish:
        publish_results(f"ema_rubberband_{ema_period}_{entry_atr}_{exit_atr}",
                        rets, append_to_bundle=False)

    return {
        "symbol": symbol, "tf": tf, "ema_period": ema_period,
        "entry_atr": entry_atr, "exit_atr": exit_atr,
        "rsi_period": rsi_period, "rsi_max": rsi_max, "rsi_min": rsi_min,
        "stop_atr": stop_atr, "tp_atr": tp_atr,
        "design": d_ret, "validation": v_ret,         "oos": o_ret,
        "full": total, "trades": m["trades"], "pf": m["pf"], "winrate": m["winrate"],
        "all3": all3,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--sweep", action="store_true",
                    help="sweep parameter grid")
    ap.add_argument("--ema-period", type=int, default=21)
    ap.add_argument("--entry-atr", type=float, default=0.3)
    ap.add_argument("--exit-atr", type=float, default=2.0)
    ap.add_argument("--rsi-period", type=int, default=14)
    ap.add_argument("--rsi-max", type=float, default=65.0)
    ap.add_argument("--rsi-min", type=float, default=35.0)
    ap.add_argument("--no-green", action="store_true",
                    help="do not require green/red candle")
    ap.add_argument("--no-slope", action="store_true",
                    help="do not require EMA slope (default: slope not required)")
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
                for exit_a in (1.5, 2.0, 2.5, 3.0):
                    for rmax, rmin in ((65.0, 35.0), (70.0, 30.0), (60.0, 40.0)):
                        r = run(sym, tf, ema_period=ema_p, entry_atr=entry_a,
                                exit_atr=exit_a, rsi_period=args.rsi_period,
                                rsi_max=rmax, rsi_min=rmin,
                                require_green=not args.no_green,
                                require_slope=not args.no_slope,
                                stop_atr=args.stop_atr, tp_atr=args.tp_atr,
                                comm_pts=args.comm_pts, slip_pts=args.slip_pts,
                                floor_pts=args.floor_pts,
                                publish=not args.no_publish)
                        results.append(r)

        print("=== SWEEP SUMMARY (sorted by OOS desc) ===")
        print(f"  {'ema':>4} {'entry':>6} {'exit':>6} {'rmax':>5} {'rmin':>5} "
              f"{'design':>8} {'valid':>8} {'OOS':>8} {'full':>8} {'trades':>7} {'PF':>5} {'all3':>5}")
        for r in sorted(results, key=lambda x: x["oos"], reverse=True):
            print(f"  {r['ema_period']:>4} {r['entry_atr']:>6.1f} {r['exit_atr']:>6.1f} "
                  f"{r['rsi_max']:>5.0f} {r['rsi_min']:>5.0f} "
                  f"{r['design']:>+8.2f} {r['validation']:>+8.2f} {r['oos']:>+8.2f} "
                  f"{r['full']:>+8.2f} {r['trades']:>7} "
                  f"{str(r['pf']):>5} {str(r['all3']):>5}")
    else:
        run(sym, tf, ema_period=args.ema_period, entry_atr=args.entry_atr,
            exit_atr=args.exit_atr, rsi_period=args.rsi_period,
            rsi_max=args.rsi_max, rsi_min=args.rsi_min,
            require_green=not args.no_green, require_slope=not args.no_slope,
            stop_atr=args.stop_atr, tp_atr=args.tp_atr,
            comm_pts=args.comm_pts, slip_pts=args.slip_pts,
            floor_pts=args.floor_pts, publish=not args.no_publish)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
