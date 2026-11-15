#!/usr/bin/env python3
"""Comprehensive backtest of all SovereignRSIBand entry/exit rules.

Tests every entry and exit rule implemented in EA/Include/SovereignRSIBand.mqh
that has NOT yet been validated on real broker tape. Reports design/validation/
OOS splits plus trade-level PF, winrate and expectancy per trade.

Each entry rule can be tested standalone or combined with each exit rule.

Usage
    .\\.venv\\Scripts\\python.exe research/sovereign_entries_exits.py --symbol EURUSD --tf H1 \
        --entry rsi_band --exit rsi_high
    .\\.venv\\Scripts\\python.exe research/sovereign_entries_exits.py --symbol EURUSD --tf H1 \
        --entry all --exit all --sweep
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

def add_indicators(df, rsi_period=9, ema_fast=21, ema_slow=50, atr_p=14):
    df = df.copy()
    df["ema21"] = ema(df["close"], ema_fast)
    df["ema50"] = ema(df["close"], ema_slow)
    df["rsi"] = wilder_rsi(df["close"], rsi_period)
    df["atr"] = wilder_atr(df, atr_p)
    df = df.dropna(subset=["ema21", "ema50", "rsi", "atr"]).reset_index(drop=True)
    return df


# ---------------------------------------------------------------- entry rules

def entry_rsi_band(df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10):
    """RSI band entry: trend + RSI cross through mid in trend direction."""
    n = len(df)
    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    prev_ema = np.empty(n); prev_ema[0] = np.nan; prev_ema[1:] = ema21[:-1]
    slope3 = np.zeros(n); slope3[3:] = (ema21[3:] - ema21[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > deadband, 1, np.where(slope3 < -deadband, -1, 0))

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        if trend[t] > 0 and rp <= mid and r > mid + margin:
            pos[t] = 1
        elif trend[t] < 0 and rp >= mid and r < mid - margin:
            pos[t] = -1
    return pos


def entry_early_trend(df, hold=2, rsi_max=50.0, rsi_min=50.0, size_mult=0.5, deadband=0.10):
    """Early trend change: price closed above/below EMA21 and held N bars
    before EMA21/EMA50 align, reduced size + RSI recovery filter."""
    n = len(df)
    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)
    ema50 = df["ema50"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    above_streak = 0
    below_streak = 0
    aligned_up = False
    aligned_down = False

    for t in range(1, n):
        #--- track EMA alignment
        if ema21[t] > ema50[t]:
            aligned_up = True
            aligned_down = False
        elif ema21[t] < ema50[t]:
            aligned_down = True
            aligned_up = False

        #--- track streaks of close vs EMA21
        if close[t] > ema21[t]:
            above_streak += 1
            below_streak = 0
        elif close[t] < ema21[t]:
            below_streak += 1
            above_streak = 0
        else:
            above_streak = 0
            below_streak = 0

        #--- early long: close above EMA21 for N bars, not yet aligned, RSI recovering
        if (not aligned_up) and above_streak >= hold:
            if rsi[t] < rsi_max and rsi[t] > rsi[t - 1]:
                pos[t] = 1

        #--- early short: close below EMA21 for N bars, not yet aligned, RSI recovering
        if (not aligned_down) and below_streak >= hold:
            if rsi[t] > rsi_min and rsi[t] < rsi[t - 1]:
                pos[t] = -1
    return pos


def entry_green_red(df, look=1, tolerance=0.002, rsi_level=60, max_candles=3,
                    wait_for_opposite=True):
    """Green-red entry: counter-trend candle near EMA21, RSI in zone,
    then opposite candle confirms."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    armed_long = False
    armed_short = False
    armed_bar = 0
    count = 0

    for t in range(1, n):
        o, c, r, e = open_[t], close[t], rsi[t], ema21[t]
        is_red = c < o
        is_green = c > o
        near_ema_long = c >= e * (1.0 - tolerance)   # long: red near/below EMA21
        near_ema_short = c <= e * (1.0 + tolerance)  # short: green near/above EMA21
        rsi_long_zone = r > 50.0 and r < (100.0 - rsi_level)
        rsi_short_zone = r > rsi_level and r < 50.0

        cur = 0  # this bar's time identifier

        #--- LONG arm: red candle near EMA21, RSI in [50, 100-level]
        if is_red and near_ema_long and rsi_long_zone:
            if not armed_long or armed_bar != t:
                armed_long = True; armed_bar = t; count = 1
            else:
                count += 1
            if count >= max_candles and not wait_for_opposite:
                pos[t] = 1; armed_long = False; count = 0
        elif armed_long and not is_red:
            # green confirmed
            pos[t] = 1; armed_long = False; count = 0

        #--- SHORT arm: green candle near EMA21, RSI in [level, 50]
        if is_green and near_ema_short and rsi_short_zone:
            if not armed_short or armed_bar != t:
                armed_short = True; armed_bar = t; count = 1
            else:
                count += 1
            if count >= max_candles and not wait_for_opposite:
                pos[t] = -1; armed_short = False; count = 0
        elif armed_short and not is_green:
            pos[t] = -1; armed_short = False; count = 0

    return pos


def entry_red_above_ema(df, tolerance=0.002, rsi_level=60):
    """Red candle above EMA21, RSI in [50, 100-level] -> buy immediately."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    for t in range(1, n):
        curr_red = close[t] < open_[t]
        red_above = close[t] > ema21[t]
        rsi_in = rsi[t] > 50.0 and rsi[t] < (100.0 - rsi_level)
        if curr_red and red_above and rsi_in:
            pos[t] = 1
    return pos


def entry_green_below_ema(df, tolerance=0.002, rsi_level=60):
    """Green candle below EMA21, RSI in [level, 50] -> sell immediately."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    for t in range(1, n):
        curr_green = close[t] > open_[t]
        green_below = close[t] < ema21[t]
        rsi_in = rsi[t] > rsi_level and rsi[t] < 50.0
        if curr_green and green_below and rsi_in:
            pos[t] = -1
    return pos


def entry_three_consecutive(df, count_needed=3, tolerance=0.002):
    """N consecutive red candles above EMA21 -> buy; N consecutive green below -> sell."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    red_count = 0
    green_count = 0
    last_bar = 0

    for t in range(1, n):
        curr_red = close[t] < open_[t]
        curr_green = close[t] > open_[t]
        red_above = close[t] > ema21[t]
        green_below = close[t] < ema21[t]

        if curr_red and red_above:
            if last_bar != t:
                red_count += 1; last_bar = t
        else:
            red_count = 0

        if curr_green and green_below:
            if last_bar != t:
                green_count += 1; last_bar = t
        else:
            green_count = 0

        if red_count >= count_needed:
            pos[t] = 1; red_count = 0
        if green_count >= count_needed:
            pos[t] = -1; green_count = 0
    return pos


def entry_reflex(df, min_streak=3, deadband=0.10):
    """Reflex: streak above/below EMA21, cross opposite, immediate reversal back."""
    n = len(df)
    close = df["close"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    slope3 = np.zeros(n); slope3[3:] = (ema21[3:] - ema21[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > deadband, 1, np.where(slope3 < -deadband, -1, 0))

    above_streak = 0
    below_streak = 0
    crossed_down = False
    crossed_up = False

    for t in range(1, n):
        c = close[t]; e = ema21[t]
        below = c < e
        above = c > e
        green = c > df["open"].to_numpy(float)[t]
        red = c < df["open"].to_numpy(float)[t]

        #--- LONG reflex: streak above EMA21, red cross down, then green above
        if crossed_down and above and green:
            pos[t] = 1
            crossed_down = False
            above_streak = 0
        if crossed_down and not (above and green):
            crossed_down = False
            above_streak = 0
        if not crossed_down and above_streak >= min_streak and below and red:
            crossed_down = True
        if above:
            above_streak += 1
        else:
            above_streak = 0

        #--- SHORT reflex: streak below EMA21, green cross up, then red below
        if crossed_up and below and red:
            pos[t] = -1
            crossed_up = False
            below_streak = 0
        if crossed_up and not (below and red):
            crossed_up = False
            below_streak = 0
        if not crossed_up and below_streak >= min_streak and above and green:
            crossed_up = True
        if below:
            below_streak += 1
        else:
            below_streak = 0
    return pos


def entry_ema_trend(df, consec=2, slope_bars=5, slope_min=0.0, body_min=0.5,
                    rsi_level=60, tolerance=0.002, htf=False):
    """EMA21 trend entry: consecutive rejections at EMA21, strong slope,
    strong body, RSI in zone."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    consec_count = 0
    last_bar = 0

    for t in range(1, n):
        o, c, h, l, r, e, a = open_[t], close[t], high[t], low[t], rsi[t], ema21[t], atr[t]
        curr_red = c < o
        curr_green = c > o
        ema_up = ema21[t] > ema21[t - 1]
        ema_down = ema21[t] < ema21[t - 1]
        near_ema_long = c >= e * (1.0 - tolerance)
        near_ema_short = c <= e * (1.0 + tolerance)
        body = abs(c - o)
        body_strong_long = body >= body_min * a if a > 0 else False
        body_strong_short = body >= body_min * a if a > 0 else False

        #--- slope
        if t >= slope_bars:
            ema_slope = (e - ema21[t - slope_bars]) / slope_bars
        else:
            ema_slope = 0.0
        slope_strong_up = ema_slope > slope_min * a if a > 0 else False
        slope_strong_down = ema_slope < -slope_min * a if a > 0 else False

        rsi_ok_long = r > 50.0 and r < (100.0 - rsi_level)
        rsi_ok_short = r > rsi_level and r < 50.0

        #--- LONG: red candle, EMA up, near EMA21, RSI ok, body strong, slope strong
        rej_long = curr_red and near_ema_long and body_strong_long and rsi_ok_long
        if rej_long:
            if last_bar != t:
                consec_count += 1; last_bar = t
        else:
            consec_count = 0
        consec_ok = consec_count >= consec

        if curr_red and ema_up and near_ema_long and rsi_ok_long and body_strong_long and slope_strong_up and consec_ok:
            pos[t] = 1
            consec_count = 0

        #--- SHORT: green candle, EMA down, near EMA21, RSI ok, body strong, slope strong
        rej_short = curr_green and near_ema_short and body_strong_short and rsi_ok_short
        if rej_short:
            if last_bar != t:
                consec_count += 1; last_bar = t
        else:
            consec_count = 0
        consec_ok = consec_count >= consec

        if curr_green and ema_down and near_ema_short and rsi_ok_short and body_strong_short and slope_strong_down and consec_ok:
            pos[t] = -1
            consec_count = 0
    return pos


def entry_bearish_engulfing(df, rsi_level=70):
    """Bearish engulfing: prev green fully engulfed by current red, RSI > level."""
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    for t in range(2, n):
        #--- current red engulfs prev green
        curr_red = close[t] < open_[t]
        prev_green = close[t - 1] > open_[t - 1]
        engulfs = open_[t] < close[t - 1] and close[t] > open_[t - 1]
        if curr_red and prev_green and engulfs and rsi[t] > rsi_level:
            pos[t] = -1
    return pos


# Module-level entry_rules dict for import by other research scripts.
# All wrappers share the signature (df, mid, hi, lo, margin, deadband)
# so callers can invoke them uniformly.
entry_rules = {
    "rsi_band": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_rsi_band(df, mid, hi, lo, margin, deadband),
    "early_trend": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_early_trend(df),
    "green_red": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_green_red(df),
    "red_above_ema": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_red_above_ema(df),
    "green_below_ema": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_green_below_ema(df),
    "three_consecutive": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_three_consecutive(df),
    "reflex": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_reflex(df),
    "ema_trend": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_ema_trend(df),
    "engulfing": lambda df, mid=50.0, hi=75.0, lo=25.0, margin=0.0, deadband=0.10:
        entry_bearish_engulfing(df),
}

# ---------------------------------------------------------------- exit rules

def apply_exits(df, pos, stop_atr=1.5, tp_atr=2.0, max_bars=0,
                structure_filter=False, trailing=False, trail_atr=1.5,
                rsi_exhaustion=0, breakeven_at_r=0.0, breakeven_buffer=0.0,
                position_age=0, hi=75.0, lo=25.0, min_hold=0,
                ema_break=False, ema_buffer=0.0, trend_flip=True):
    """Apply exit rules to a position series. Returns modified pos series.

    Exit rules are applied bar by bar: if any exit condition fires, position
    is closed at that bar's close.
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    ema21 = df["ema21"].to_numpy(float)
    atr = df["atr"].to_numpy(float)

    out = pos.copy()

    #--- state tracking
    in_pos = 0
    side = 0
    entry_bar = -10 ** 9
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    mfe = 0.0  # max favorable excursion
    breakeven_done = False

    #--- structure tracking
    red_open_ema_above = np.nan
    green_close_ema_below = np.nan

    for t in range(1, n):
        c, o, h, l, r, e, a = close[t], open_[t], high[t], low[t], rsi[t], ema21[t], atr[t]

        #--- update structure trackers
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

            #--- 1. time exit
            if max_bars > 0 and held >= max_bars:
                exit_now = True
            #--- 2. structure break
            elif structure_filter and side > 0 and not np.isnan(red_open_ema_above):
                if c > o and c <= red_open_ema_above:   # green fails to reclaim
                    exit_now = True
            elif structure_filter and side < 0 and not np.isnan(green_close_ema_below):
                if c < o and c >= green_close_ema_below:  # red fails to break
                    exit_now = True
            #--- 3. trailing stop
            elif trailing and a > 0:
                if side > 0:
                    new_stop = c - trail_atr * a
                    if stop_price <= 0 or new_stop > stop_price:
                        stop_price = new_stop
                    if c <= stop_price:
                        exit_now = True
                else:
                    new_stop = c + trail_atr * a
                    if stop_price <= 0 or new_stop < stop_price:
                        stop_price = new_stop
                    if c >= stop_price:
                        exit_now = True
            #--- 4. RSI exhaustion (shorts only)
            elif rsi_exhaustion > 0 and side < 0 and r < rsi_exhaustion:
                exit_now = True
            #--- 5. breakeven (move SL to entry + buffer at +N x R)
            elif breakeven_at_r > 0 and not breakeven_done and a > 0:
                stop_dist = abs(entry_price - stop_price)
                if stop_dist > 0:
                    r_multiple = (c - entry_price) / stop_dist if side > 0 else (entry_price - c) / stop_dist
                    if r_multiple >= breakeven_at_r:
                        breakeven_done = True
                        stop_price = entry_price + (breakeven_buffer * 0.00001 if side > 0 else -breakeven_buffer * 0.00001)
            #--- 6. hard stop / TP
            if not exit_now and stop_price > 0 and tp_price > 0:
                if side > 0 and (c <= stop_price or c >= tp_price):
                    exit_now = True
                elif side < 0 and (c >= stop_price or c <= tp_price):
                    exit_now = True
            #--- 7. position age (same as time exit but from open time)
            # (handled by max_bars above)
            #--- 8. RSI high/low sustained exit
            if not exit_now and t > 1 and held >= min_hold:
                if side > 0 and rsi[t - 1] > hi and r > hi:
                    exit_now = True
                elif side < 0 and rsi[t - 1] < lo and r < lo:
                    exit_now = True
            #--- 9. EMA break exit
            if not exit_now and ema_break and a > 0:
                if side > 0 and c < (e - ema_buffer * a) and r < (50.0 - 10.0):
                    exit_now = True
                elif side < 0 and c > (e + ema_buffer * a) and r > (50.0 + 10.0):
                    exit_now = True
            #--- 10. trend flip exit
            if not exit_now and trend_flip:
                if side > 0 and r < lo:
                    exit_now = True
                elif side < 0 and r > hi:
                    exit_now = True

            if exit_now:
                out[t] = 0
                in_pos = 0
                side = 0
                entry_bar = -10 ** 9
                entry_price = 0.0
                stop_price = 0.0
                tp_price = 0.0
                mfe = 0.0
                breakeven_done = False
        else:
            #--- position just opened at this bar (pos[t] != 0)
            if pos[t] != 0:
                in_pos = pos[t]
                side = pos[t]
                entry_bar = t
                entry_price = c
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
        out[t] = in_pos
    return out


# ---------------------------------------------------------------- evaluation

def split_mask(time, t0, edge1, edge2):
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2),
              "oos": (edge2, time.iloc[-1])}
    return {sp: ((time >= a) & (time < b)).to_numpy() for sp, (a, b)
            in bounds.items()}


def evaluate(ret, pos, trades, time, bars_per_year, t0, edge1, edge2,
             label, symbol):
    masks = split_mask(time, t0, edge1, edge2)
    tm = trade_metrics(trades)
    print(f"=== {label} ===")
    print(f"  trades={tm['trades']}  PF={tm['pf']}  winrate={tm['winrate']}%")
    for sp in SPLITS:
        m = masks[sp]
        if m.sum() < 50:
            print(f"  {sp:<11} too few bars ({int(m.sum())})")
            continue
        eq = (1.0 + ret[m]).prod() - 1.0
        yrs = max(m.sum() / bars_per_year, 1e-9)
        cagr = (1.0 + eq) ** (1.0 / yrs) - 1.0
        peak = np.maximum.accumulate(np.concatenate(([1.0], np.cumprod(1.0 + ret[m]))))
        dd = (np.cumprod(1.0 + ret[m]) / peak[1:] - 1.0).min()
        sd = ret[m].std()
        sharpe = ret[m].mean() / sd * np.sqrt(bars_per_year) if sd > 0 else 0.0
        print(f"  {sp:<11} {eq * 100:>+7.2f}%  CAGR {cagr * 100:>+6.2f}%  "
              f"Sharpe {sharpe:>5.2f}  maxDD {dd * 100:>6.2f}%")
    eq_all = (1.0 + ret).prod() - 1.0
    print(f"  {'full':<11} {eq_all * 100:>+7.2f}%  "
          f"trades/yr {tm['trades'] / max(len(time) / bars_per_year, 1):.1f}")
    print()
    return tm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--data-dir", default="")
    # entry selection
    ap.add_argument("--entry", default="rsi_band",
                    help="entry rule: rsi_band, early_trend, green_red, red_above_ema, "
                         "green_below_ema, three_consecutive, reflex, ema_trend, engulfing, all")
    ap.add_argument("--exit", default="rsi_high",
                    help="exit rule: rsi_high, structure_break, time, trailing, "
                         "rsi_exhaustion, breakeven, ema_break, trend_flip, all")
    ap.add_argument("--sweep", action="store_true",
                    help="sweep all entry x exit combinations")
    # cost model
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--tp-atr", type=float, default=2.0)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    # entry knobs
    ap.add_argument("--mid", type=float, default=50.0)
    ap.add_argument("--hi", type=float, default=75.0)
    ap.add_argument("--lo", type=float, default=25.0)
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--min-hold", type=int, default=0)
    ap.add_argument("--deadband", type=float, default=0.10)
    # exit knobs
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

    point = 0.001 if sym.endswith("JPY") else 0.00001

    print(f"=== Sovereign entry/exit research  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"cost model: spread/2 (floor {args.floor_pts} pts) + "
          f"{args.slip_pts} pts slip + {args.comm_pts / 2:.1f} pts half-comm / side")
    print()

    #--- build entry signal
    entry_rules = {
        "rsi_band": lambda: entry_rsi_band(df, args.mid, args.hi, args.lo,
                                           args.margin, args.deadband),
        "early_trend": lambda: entry_early_trend(df),
        "green_red": lambda: entry_green_red(df),
        "red_above_ema": lambda: entry_red_above_ema(df),
        "green_below_ema": lambda: entry_green_below_ema(df),
        "three_consecutive": lambda: entry_three_consecutive(df),
        "reflex": lambda: entry_reflex(df),
        "ema_trend": lambda: entry_ema_trend(df),
        "engulfing": lambda: entry_bearish_engulfing(df),
    }

    exit_rules = ["rsi_high", "structure_break", "time", "trailing",
                  "rsi_exhaustion", "breakeven", "ema_break", "trend_flip"]

    def run(entry_name, exit_name, label):
        entry_fn = entry_rules[entry_name]
        raw_pos = entry_fn()
        pos = apply_exits(df, raw_pos,
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
        pos_s = pd.Series(pos, index=df.index)
        rets, trades, _ = simulate_stops(df, pos_s, sym,
                                         comm_pts=args.comm_pts,
                                         slip_pts=args.slip_pts,
                                         floor_pts=args.floor_pts,
                                         stop_atr=args.stop_atr,
                                         tp_atr=args.tp_atr)
        validate_returns(rets.to_numpy(), close, np.abs(pos), label=label)
        evaluate(rets, pos, trades, time, bars_per_year, t0, edge1, edge2,
                 label, sym)
        if not args.no_publish:
            s = pd.Series(rets.to_numpy(), index=pd.DatetimeIndex(time))
            publish_results(f"sov_{entry_name}_{exit_name}_{sym}_{tf}",
                            s, append_to_bundle=False)
        return trades

    if args.sweep:
        print(f"--- sweeping {len(entry_rules)} entries x {len(exit_rules)} exits ---\n")
        results = []
        for en in entry_rules:
            for ex in exit_rules:
                label = f"{en} + {ex}"
                print(f"[{en:>18} + {ex:>16}] ", end="", flush=True)
                trades = run(en, ex, label)
                tm = trade_metrics(trades)
                results.append((en, ex, tm["pf"], tm["winrate"], len(trades)))
        print("\n--- SUMMARY (sorted by PF desc) ---")
        print(f"{'entry':>18} {'exit':>16} {'PF':>6} {'win%':>7} {'trades':>8}")
        results.sort(key=lambda x: (x[2] if x[2] is not None else -999), reverse=True)
        for en, ex, pf, wr, nt in results:
            pf_str = f"{pf:>6.2f}" if pf is not None else f"{'N/A':>6}"
            print(f"{en:>18} {ex:>16} {pf_str} {wr:>6.1f}% {nt:>8}")
    else:
        run(args.entry, args.exit,
            f"{args.entry} + {args.exit}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
