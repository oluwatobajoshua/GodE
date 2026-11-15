#!/usr/bin/env python3
"""Detect exit rules that cut winning trades too early.

For every closed trade we record:
  * MFE (max favorable excursion) - best price reached while in the trade
  * Actual exit price
  * Regret = profit left on the table = MFE - exit (in R units)
  * Which exit rule fired

We then rank exit rules by how much winner-profit they surrender. The goal is to
identify exits that repeatedly close trades while they're still in favorable
territory - candidates for removal or relaxation.

Usage
    .\\.venv\\Scripts\\python.exe research/exit_regret.py --symbol EURUSD --tf H1 \
        --entry engulfing --exit trailing
    .\\.venv\\Scripts\\python.exe research/exit_regret.py --symbol EURUSD --tf H1 \
        --entry all --exit all --sweep
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from backtest_core import simulate_stops, trade_metrics, validate_returns, wilder_atr, cost_per_side  # noqa: E402
from ema_cross_harvest import EMA, RSI_P, ema, wilder_rsi  # noqa: E402
from sovereign_entries_exits import (  # noqa: E402
    entry_rules as _entry_rules,
    add_indicators,
)

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


# ---------------------------------------------------------------- trade-level regret tracker

def track_regret(df, pos, stop_atr=1.5, tp_atr=2.0, max_bars=0,
                 structure_filter=False, trailing=False, trail_atr=1.5,
                 rsi_exhaustion=0, breakeven_at_r=0.0, breakeven_buffer=0.0,
                 position_age=0, hi=75.0, lo=25.0, min_hold=0,
                 ema_break=False, ema_buffer=0.0, trend_flip=True):
    """Simulate positions with per-trade regret tracking.

    Returns a list of trade dicts: entry_bar, exit_bar, side, entry_price,
    exit_price, mfe, regret_r (profit left on table in R units), exit_rule.
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    trades = []

    in_pos = 0
    side = 0
    entry_bar = -10 ** 9
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    mfe = 0.0
    breakeven_done = False

    #--- structure tracking
    red_open_ema_above = np.nan
    green_close_ema_below = np.nan

    for t in range(1, n):
        c, o, h, l, r, e, a = close[t], open_[t], high[t], low[t], rsi[t], ema21[t], atr[t]

        if structure_filter:
            is_red = c < o
            is_green = c > o
            if is_red and o > e:
                red_open_ema_above = o
            if is_green and c < e:
                green_close_ema_below = c

        if in_pos != 0:
            held = t - entry_bar
            exit_now = False
            exit_rule = ""

            # update MFE
            if side > 0:
                mfe = max(mfe, c)
            else:
                mfe = min(mfe, c)

            #--- 1. time exit
            if max_bars > 0 and held >= max_bars:
                exit_now = True; exit_rule = "time"
            #--- 2. structure break
            elif structure_filter and side > 0 and not np.isnan(red_open_ema_above):
                if c > o and c <= red_open_ema_above:
                    exit_now = True; exit_rule = "structure_break"
            elif structure_filter and side < 0 and not np.isnan(green_close_ema_below):
                if c < o and c >= green_close_ema_below:
                    exit_now = True; exit_rule = "structure_break"
            #--- 3. trailing stop
            elif trailing and a > 0:
                if side > 0:
                    new_stop = c - trail_atr * a
                    if stop_price <= 0 or new_stop > stop_price:
                        stop_price = new_stop
                    if c <= stop_price:
                        exit_now = True; exit_rule = "trailing"
                else:
                    new_stop = c + trail_atr * a
                    if stop_price <= 0 or new_stop < stop_price:
                        stop_price = new_stop
                    if c >= stop_price:
                        exit_now = True; exit_rule = "trailing"
            #--- 4. RSI exhaustion (shorts only)
            elif rsi_exhaustion > 0 and side < 0 and r < rsi_exhaustion:
                exit_now = True; exit_rule = "rsi_exhaustion"
            #--- 5. breakeven
            elif breakeven_at_r > 0 and not breakeven_done and a > 0:
                stop_dist = abs(entry_price - stop_price)
                if stop_dist > 0:
                    r_mult = (c - entry_price) / stop_dist if side > 0 else (entry_price - c) / stop_dist
                    if r_mult >= breakeven_at_r:
                        breakeven_done = True
                        buf = breakeven_buffer * 0.00001
                        stop_price = entry_price + buf if side > 0 else entry_price - buf
            #--- 6. hard stop / TP
            if not exit_now and stop_price > 0 and tp_price > 0:
                if side > 0:
                    if c <= stop_price:
                        exit_now = True; exit_rule = "stop"
                    elif c >= tp_price:
                        exit_now = True; exit_rule = "take_profit"
                else:
                    if c >= stop_price:
                        exit_now = True; exit_rule = "stop"
                    elif c <= tp_price:
                        exit_now = True; exit_rule = "take_profit"
            #--- 7. RSI high/low sustained
            if not exit_now and t > 1 and held >= min_hold:
                if side > 0 and rsi[t - 1] > hi and r > hi:
                    exit_now = True; exit_rule = "rsi_high"
                elif side < 0 and rsi[t - 1] < lo and r < lo:
                    exit_now = True; exit_rule = "rsi_low"
            #--- 8. EMA break
            if not exit_now and ema_break and a > 0:
                if side > 0 and c < (e - ema_buffer * a) and r < (50.0 - 10.0):
                    exit_now = True; exit_rule = "ema_break"
                elif side < 0 and c > (e + ema_buffer * a) and r > (50.0 + 10.0):
                    exit_now = True; exit_rule = "ema_break"
            #--- 9. trend flip
            if not exit_now and trend_flip:
                if side > 0 and r < lo:
                    exit_now = True; exit_rule = "trend_flip"
                elif side < 0 and r > hi:
                    exit_now = True; exit_rule = "trend_flip"

            if exit_now:
                #--- compute regret in R units
                stop_dist = abs(entry_price - stop_price) if stop_price > 0 else a * stop_atr
                if stop_dist <= 0:
                    stop_dist = a * stop_atr if a > 0 else 1e-6
                if side > 0:
                    gross_r = (mfe - entry_price) / stop_dist
                    realized_r = (c - entry_price) / stop_dist
                else:
                    gross_r = (entry_price - mfe) / stop_dist
                    realized_r = (entry_price - c) / stop_dist
                regret_r = gross_r - realized_r

                trades.append(dict(
                    entry_bar=entry_bar, exit_bar=t, side=side,
                    entry_price=entry_price, exit_price=c,
                    mfe=mfe, regret_r=regret_r, exit_rule=exit_rule,
                    held=held, gross_r=gross_r, realized_r=realized_r,
                ))

                in_pos = 0; side = 0; entry_bar = -10 ** 9
                entry_price = 0.0; stop_price = 0.0; tp_price = 0.0
                mfe = 0.0; breakeven_done = False
        else:
            if pos[t] != 0:
                in_pos = pos[t]; side = pos[t]
                entry_bar = t; entry_price = c
                if a > 0:
                    stop_dist = stop_atr * a
                    if side > 0:
                        stop_price = c - stop_dist
                        tp_price = c + stop_dist * tp_atr
                    else:
                        stop_price = c + stop_dist
                        tp_price = c - stop_dist * tp_atr
                mfe = c
                breakeven_done = False

    #--- close any open position at end
    if in_pos != 0 and stop_price > 0:
        stop_dist = abs(entry_price - stop_price) if stop_price > 0 else atr[-1] * stop_atr
        if stop_dist <= 0:
            stop_dist = 1e-6
        c = close[-1]
        if side > 0:
            gross_r = (mfe - entry_price) / stop_dist
            realized_r = (c - entry_price) / stop_dist
        else:
            gross_r = (entry_price - mfe) / stop_dist
            realized_r = (entry_price - c) / stop_dist
        regret_r = gross_r - realized_r
        trades.append(dict(
            entry_bar=entry_bar, exit_bar=n - 1, side=side,
            entry_price=entry_price, exit_price=c, mfe=mfe,
            regret_r=regret_r, exit_rule="end_of_data", held=n - 1 - entry_bar,
            gross_r=gross_r, realized_r=realized_r,
        ))

    return trades


def analyze_regret(trades, label, symbol):
    """Print regret analysis: which exit rules cut winners short."""
    if not trades:
        print(f"=== {label} ===  no trades")
        print()
        return

    df = pd.DataFrame(trades)
    n = len(df)

    #--- regret stats on ALL trades
    total_regret = df["regret_r"].sum()
    mean_regret = df["regret_r"].mean()

    #--- KEY: winning trades that got cut short (realized profit > 0 but MFE higher)
    winners_cut = df[(df["realized_r"] > 0) & (df["regret_r"] > 0)]
    winners_cut_regret = winners_cut["regret_r"].sum()

    #--- losing trades stopped out (realized profit <= 0)
    losers_stopped = df[df["realized_r"] <= 0]
    losers_regret = losers_stopped["regret_r"].sum()

    #--- per exit rule, ALL trades
    rule_stats = df.groupby("exit_rule").agg(
        count=("regret_r", "size"),
        total_regret=("regret_r", "sum"),
        mean_regret=("regret_r", "mean"),
        max_regret=("regret_r", "max"),
    ).sort_values("total_regret", ascending=False)

    #--- per exit rule, WINNERS CUT SHORT only
    if len(winners_cut) > 0:
        winner_rule_stats = winners_cut.groupby("exit_rule").agg(
            count=("regret_r", "size"),
            total_regret=("regret_r", "sum"),
            mean_regret=("regret_r", "mean"),
        ).sort_values("total_regret", ascending=False)
    else:
        winner_rule_stats = pd.DataFrame()

    print(f"=== {label} ===")
    print(f"  total trades={n}  total_regret={total_regret:+.2f}R  mean_regret={mean_regret:+.2f}R")
    print(f"  winning trades cut short: {len(winners_cut)} "
          f"({100 * len(winners_cut) / n:.1f}% of all trades)")
    print(f"  regret on winners cut short: {winners_cut_regret:+.2f}R "
          f"({100 * winners_cut_regret / total_regret:.1f}% of total regret)")
    print(f"  regret on losers stopped out: {losers_regret:+.2f}R "
          f"({100 * losers_regret / total_regret:.1f}% of total regret)")
    print()
    print("  Regret by exit rule (ALL trades, sorted by total regret):")
    print(f"  {'rule':<18} {'count':>7} {'totalR':>9} {'meanR':>8} {'maxR':>8}")
    for rule, row in rule_stats.iterrows():
        print(f"  {rule:<18} {int(row['count']):>7} {row['total_regret']:>+9.2f} "
              f"{row['mean_regret']:>+8.2f} {row['max_regret']:>+8.2f}")
    print()
    if len(winner_rule_stats) > 0:
        print("  Regret by exit rule (WINNERS CUT SHORT only):")
        print(f"  {'rule':<18} {'count':>7} {'totalR':>9} {'meanR':>8}")
        for rule, row in winner_rule_stats.iterrows():
            print(f"  {rule:<18} {int(row['count']):>7} {row['total_regret']:>+9.2f} "
                  f"{row['mean_regret']:>+8.2f}")
    print()

    #--- worst offenders: winning trades with highest regret
    if len(winners_cut) > 0:
        worst = winners_cut.nlargest(10, "regret_r")
        print("  Top 10 WINNING trades by regret (profit left on table):")
        print(f"  {'rule':<18} {'side':>5} {'regretR':>8} {'held':>6} {'entry':>9} {'exit':>9} {'mfe':>9}")
        for _, tr in worst.iterrows():
            side_s = "L" if tr["side"] > 0 else "S"
            print(f"  {tr['exit_rule']:<18} {side_s:>5} {tr['regret_r']:>+8.2f} {tr['held']:>6} "
                  f"{tr['entry_price']:>9.5f} {tr['exit_price']:>9.5f} {tr['mfe']:>9.5f}")
    print()
    return rule_stats, winner_rule_stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--entry", default="rsi_band",
                    help="entry rule or 'all'")
    ap.add_argument("--exit", default="rsi_high",
                    help="exit rule or 'all'")
    ap.add_argument("--sweep", action="store_true",
                    help="sweep all entry x exit combinations")
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--tp-atr", type=float, default=2.0)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    ap.add_argument("--mid", type=float, default=50.0)
    ap.add_argument("--hi", type=float, default=75.0)
    ap.add_argument("--lo", type=float, default=25.0)
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--min-hold", type=int, default=0)
    ap.add_argument("--deadband", type=float, default=0.10)
    ap.add_argument("--max-bars", type=int, default=0)
    ap.add_argument("--trailing", action="store_true")
    ap.add_argument("--trail-atr", type=float, default=1.5)
    ap.add_argument("--rsi-exhaustion", type=int, default=0)
    ap.add_argument("--breakeven-at-r", type=float, default=0.0)
    ap.add_argument("--breakeven-buffer", type=float, default=0.0)
    ap.add_argument("--position-age", type=int, default=0)
    ap.add_argument("--ema-break", action="store_true")
    ap.add_argument("--ema-buffer", type=float, default=0.0)
    ap.add_argument("--trend-flip", action="store_true", default=True)
    ap.add_argument("--structure-filter", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    data_dir = Path(args.data_dir) if args.data_dir else DATA
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    path = data_dir / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
    df = add_indicators(df)

    close = df["close"].to_numpy(float)
    time = df["time"]
    n = len(df)
    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252}[tf]

    print(f"=== Exit regret analysis  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print()

    entry_names = list(_entry_rules.keys())
    exit_names = ["rsi_high", "structure_break", "time", "trailing",
                  "rsi_exhaustion", "breakeven", "ema_break", "trend_flip"]

    def run(entry_name, exit_name):
        raw_pos = _entry_rules[entry_name](df, args.mid, args.hi, args.lo,
                                           args.margin, args.deadband)
        trades = track_regret(df, raw_pos,
                              stop_atr=args.stop_atr, tp_atr=args.tp_atr,
                              max_bars=args.max_bars,
                              structure_filter=(exit_name == "structure_break" or args.structure_filter),
                              trailing=args.trailing or (exit_name == "trailing"),
                              trail_atr=args.trail_atr,
                              rsi_exhaustion=args.rsi_exhaustion or (exit_name == "rsi_exhaustion"),
                              breakeven_at_r=args.breakeven_at_r or (exit_name == "breakeven"),
                              breakeven_buffer=args.breakeven_buffer,
                              position_age=args.position_age,
                              hi=args.hi, lo=args.lo, min_hold=args.min_hold,
                              ema_break=args.ema_break or (exit_name == "ema_break"),
                              ema_buffer=args.ema_buffer,
                              trend_flip=args.trend_flip or (exit_name == "trend_flip"))
        label = f"{entry_name} + {exit_name}"
        analyze_regret(trades, label, sym)
        return trades

    if args.sweep:
        print(f"--- sweeping {len(entry_names)} entries x {len(exit_names)} exits ---\n")
        all_rule_stats = {}
        for en in entry_names:
            for ex in exit_names:
                trades = run(en, ex)
                if trades:
                    df_t = pd.DataFrame(trades)
                    rule_stats = df_t.groupby("exit_rule")["regret_r"].sum()
                    for rule, val in rule_stats.items():
                        all_rule_stats.setdefault(rule, 0.0)
                        all_rule_stats[rule] += val
        print("=== AGGREGATE REGRET BY EXIT RULE (all entries combined) ===")
        print(f"  {'rule':<18} {'total_regret_R':>15}")
        for rule, val in sorted(all_rule_stats.items(), key=lambda x: x[1]):
            print(f"  {rule:<18} {val:>+15.2f}")
    else:
        run(args.entry, args.exit)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
