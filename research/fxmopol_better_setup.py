#!/usr/bin/env python3
"""Research a better setup using ALL fxmopol template indicators: EMA21, EMA50, SMMA100, RSI9, Fractals.

Tests parameter combinations across multiple symbols and timeframes using the
backtest_core.py contract (per-bar returns via simulate_stops).

Run:
    .\.venv\Scripts\python.exe research\fxmopol_better_setup.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_core import simulate_stops, trade_metrics, validate_returns, wilder_atr
from fxmopol_chart_core import (
    build_stack, stack_long, stack_short, market_context_at,
    allow_trend_entry, allow_pullback_add, trend_phase_at,
    stop_hunt_reclaim, fractal_low, fractal_high,
    pullback_long, retest_short,
    ema21_cross_close_long, ema21_cross_close_short,
    consecutive_closes_side,
)

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "fxmopol_better_setup"
OUT.mkdir(parents=True, exist_ok=True)

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}
COST_BPS = 3.0
MAX_HOLD = 72


def load_h1(sym: str) -> pd.DataFrame:
    for p in (DATA / "histdata" / f"{sym}_H1.csv", DATA / f"{sym}_H1.csv"):
        if p.exists():
            d = pd.read_csv(p, parse_dates=["time"])
            d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
            return d.set_index("time").sort_index()
    raise FileNotFoundError(sym)


def split_for_time(t: pd.Timestamp) -> str:
    for name, (a, b) in SPLITS.items():
        if t >= pd.Timestamp(a) and t < pd.Timestamp(b):
            return name
    return "design"


def metrics_from_trades(trades: list[float]) -> dict:
    if not trades:
        return {"n": 0, "winrate": 0, "exp_bps": 0, "total_pct": 0, "pf": 0}
    arr = np.asarray(trades)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    total = float(np.prod(1 + arr) - 1) * 100
    return {
        "n": int(arr.size),
        "winrate": round(100 * (arr > 0).mean(), 1),
        "exp_bps": round(arr.mean() * 10000, 2),
        "total_pct": round(total, 2),
        "pf": round(pf, 2),
    }


def build_position_series(h1: pd.DataFrame, stk: dict, params: dict) -> pd.Series:
    """Build a position series (+1/-1/0) using fxmopol template indicators."""
    c, o, h, l = stk["close"], stk["open"], stk["high"], stk["low"]
    e21, e50, s100, r, atr = stk["ema21"], stk["ema50"], stk["smma100"], stk["rsi"], stk["atr"]
    n = len(h1)
    pos = pd.Series(0, index=h1.index, dtype=int)

    rsi_buy_hi = params["rsi_buy_hi"]
    rsi_buy_lo = params["rsi_buy_lo"]
    rsi_sell_hi = params["rsi_sell_hi"]
    rsi_sell_lo = params["rsi_sell_lo"]
    rsi_exit_long = params["rsi_exit_long"]
    rsi_exit_short = params["rsi_exit_short"]
    require_smma = params["require_smma"]
    require_fan = params["require_fan"]
    use_fractal_entry = params["use_fractal_entry"]
    use_fractal_exit = params["use_fractal_exit"]
    min_hold = params["min_hold"]
    session_filter = params["session_filter"]
    stop_atr = params["stop_atr"]
    tp_atr = params["tp_atr"]
    regime_bars = params["regime_bars"]

    side = 0
    entry_i = -1
    entry_px = 0.0
    stop_px = 0.0
    tp_px = 0.0

    for i in range(110, n - 1):
        # Session filter
        if session_filter:
            hour = h1.index[i].hour
            dow = h1.index[i].weekday()
            if session_filter == "london_am" and not (7 <= hour <= 11):
                continue
            if session_filter == "overlap" and not (13 <= hour <= 16):
                continue
            if session_filter == "tue_thu" and dow not in (1, 3):
                continue

        # --- Entry logic ---
        if side == 0:
            long_ctx, short_ctx = market_context_at(i, c, e21, e50, s100, atr, r, 1), market_context_at(i, c, e21, e50, s100, atr, r, -1)
            long_qual = long_ctx[1]
            short_qual = short_ctx[1]

            # Long entry conditions
            long_ok = False
            if long_qual in ("GOOD_TREND", "BAD_TREND"):
                rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
                if rsi_buy_lo <= rv <= rsi_buy_hi:
                    if require_smma and c.iloc[i] <= s100.iloc[i]:
                        pass
                    else:
                        if use_fractal_entry:
                            if fractal_low(l, i - 1):
                                long_ok = True
                        else:
                            long_ok = True

            # Short entry conditions
            short_ok = False
            if short_qual in ("GOOD_TREND", "BAD_TREND"):
                rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
                if rsi_sell_lo <= rv <= rsi_sell_hi:
                    if require_smma and c.iloc[i] >= s100.iloc[i]:
                        pass
                    else:
                        if use_fractal_entry:
                            if fractal_high(h, i - 1):
                                short_ok = True
                        else:
                            short_ok = True

            if long_ok and not short_ok:
                side = 1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                a = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.001
                stop_px = entry_px - stop_atr * a
                tp_px = entry_px + tp_atr * a
            elif short_ok and not long_ok:
                side = -1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                a = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.001
                stop_px = entry_px + stop_atr * a
                tp_px = entry_px - tp_atr * a

        # --- Exit logic ---
        elif side != 0:
            bars_held = i - entry_i
            c_i = float(c.iloc[i])
            exit_now = False

            if side > 0:
                # Stop / TP
                if c_i <= stop_px or c_i >= tp_px:
                    exit_now = True
                # RSI extreme exit
                rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
                if rv >= rsi_exit_long:
                    exit_now = True
                # EMA21 break
                if c_i < float(e21.iloc[i]):
                    exit_now = True
                # Fractal exit: higher high then close below EMA21
                if use_fractal_exit and bars_held >= min_hold:
                    if fractal_high(h, i - 1) and c_i < float(e21.iloc[i]):
                        exit_now = True
                # Time stop
                if bars_held >= MAX_HOLD:
                    exit_now = True
            else:
                if c_i >= stop_px or c_i <= tp_px:
                    exit_now = True
                rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
                if rv <= rsi_exit_short:
                    exit_now = True
                if c_i > float(e21.iloc[i]):
                    exit_now = True
                if use_fractal_exit and bars_held >= min_hold:
                    if fractal_low(l, i - 1) and c_i > float(e21.iloc[i]):
                        exit_now = True
                if bars_held >= MAX_HOLD:
                    exit_now = True

            if exit_now and bars_held >= min_hold:
                exit_px = c_i
                if side > 0:
                    gross = (exit_px - entry_px) / entry_px - COST_BPS / 10000.0
                else:
                    gross = (entry_px - exit_px) / entry_px - COST_BPS / 10000.0
                # Mark entry bar and exit bar in position series
                pos.iloc[entry_i + 1:i + 1] = side
                side = 0

    # Close any open position at end
    if side != 0 and entry_i >= 0:
        pos.iloc[entry_i + 1:] = side

    return pos


def run_symbol(sym: str, params_list: list[dict]) -> list[dict]:
    h1 = load_h1(sym)
    stk = build_stack(h1)
    results = []

    for params in params_list:
        pos = build_position_series(h1, stk, params)
        h1_cols = h1.reset_index()
        rets, trades, tix = simulate_stops(h1_cols, pos, sym, stop_atr=params["stop_atr"], tp_atr=params["tp_atr"])

        # Validate returns
        try:
            validate_returns(rets, h1["close"].to_numpy(), np.abs(pos.to_numpy()) > 0, label=f"{sym}")
        except ValueError:
            continue

        # Build per-trade returns from simulate_stops
        trade_list = trades.tolist() if trades.size else []
        m = metrics_from_trades(trade_list)

        # Split metrics — rets index is the time index
        split_rows = {}
        splits = pd.Series([split_for_time(t) for t in rets.index], index=rets.index)
        for split_name in ("design", "validation", "OOS"):
            sub = rets[splits == split_name]
            if len(sub) > 0:
                cum = float((1 + sub).prod() - 1) * 100
                split_rows[split_name] = round(cum, 2)
            else:
                split_rows[split_name] = 0.0

        all_positive = all(split_rows[s] > 0 for s in ("design", "validation", "OOS"))

        results.append({
            "symbol": sym,
            **params,
            "n_trades": m["n"],
            "winrate": m["winrate"],
            "exp_bps": m["exp_bps"],
            "total_pct": m["total_pct"],
            "pf": m["pf"],
            "design_pct": split_rows["design"],
            "validation_pct": split_rows["validation"],
            "oos_pct": split_rows["OOS"],
            "all_positive": all_positive,
        })

    return results


def param_grid() -> list[dict]:
    grids = []
    buy_zones = [(55, 35), (50, 30)]
    sell_zones = [(45, 65), (50, 70)]
    exit_levels = [(80, 20), (75, 25)]
    stop_tp = [(1.5, 2.0)]

    for bz in buy_zones:
        for sz in sell_zones:
            for el in exit_levels:
                for require_smma in (True, False):
                    for require_fan in (True, False):
                        for use_fractal_entry in (True, False):
                            for use_fractal_exit in (True, False):
                                for min_hold in (6, 12):
                                    for session_filter in (None, "london_am"):
                                        for stp, tp in stop_tp:
                                            for regime_bars in (3, 5):
                                                grids.append({
                                                    "rsi_buy_hi": bz[0], "rsi_buy_lo": bz[1],
                                                    "rsi_sell_hi": sz[0], "rsi_sell_lo": sz[1],
                                                    "rsi_exit_long": el[0], "rsi_exit_short": el[1],
                                                    "require_smma": require_smma, "require_fan": require_fan,
                                                    "use_fractal_entry": use_fractal_entry, "use_fractal_exit": use_fractal_exit,
                                                    "min_hold": min_hold, "session_filter": session_filter,
                                                    "stop_atr": stp, "tp_atr": tp,
                                                    "regime_bars": regime_bars,
                                                })
    return grids


def main() -> int:
    syms = ("EURUSD",)
    params_list = param_grid()
    print(f"Testing {len(params_list)} configs x {len(syms)} symbols = {len(params_list) * len(syms)} backtests")

    all_results = []
    for sym in syms:
        print(f"\n=== {sym} ===")
        res = run_symbol(sym, params_list)
        all_results.extend(res)
        # Show top 5 by OOS for this symbol
        res_sorted = sorted(res, key=lambda x: x["oos_pct"], reverse=True)
        for r in res_sorted[:5]:
            flag = " ***" if r["all_positive"] else ""
            print(f"  OOS={r['oos_pct']:+7.2f}% D={r['design_pct']:+7.2f}% V={r['validation_pct']:+7.2f}% "
                  f"n={r['n_trades']:3d} wr={r['winrate']:5.1f}% pf={r['pf']:.2f} "
                  f"buy({r['rsi_buy_lo']}-{r['rsi_buy_hi']}) sell({r['rsi_sell_lo']}-{r['rsi_sell_hi']}) "
                  f"exit({r['rsi_exit_short']}/{r['rsi_exit_long']}) hold={r['min_hold']} "
                  f"smma={r['require_smma']} fractal={r['use_fractal_entry']} sess={r['session_filter']}{flag}")

    # Save all results
    df = pd.DataFrame(all_results)
    df.to_csv(OUT / "fxmopol_better_setup_all.csv", index=False)

    # Show all-positive configs
    pos = df[df["all_positive"]].copy()
    if len(pos):
        pos = pos.sort_values("oos_pct", ascending=False)
        pos.to_csv(OUT / "fxmopol_better_setup_all_positive.csv", index=False)
        print(f"\n=== {len(pos)} configs positive in ALL THREE splits ===")
        print(pos.head(10).to_string(index=False))
    else:
        print("\n=== NO configs positive in all three splits ===")

    # Show top 10 by OOS regardless
    top = df.sort_values("oos_pct", ascending=False).head(10)
    print("\n=== Top 10 by OOS ===")
    print(top[["symbol", "design_pct", "validation_pct", "oos_pct", "n_trades", "winrate", "pf",
               "rsi_buy_lo", "rsi_buy_hi", "rsi_sell_lo", "rsi_sell_hi",
               "rsi_exit_short", "rsi_exit_long", "min_hold", "require_smma",
               "use_fractal_entry", "session_filter", "stop_atr", "tp_atr"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
