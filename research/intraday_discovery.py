#!/usr/bin/env python3
r"""Intraday Discovery Lab — H1 hypothesis testing on real MT5 data.

Tests intraday hypotheses (candle crosses, RSI extremes, fractals, structure,
rubberband, EMA extension, session effects) on EURUSD/GBPUSD/USDJPY H1 data
with chronological design → validation → OOS splits and a real cost model.

Proof bar: OOS profit factor >= 1.05, OOS Sharpe >= 0.25, max DD >= -30%.

Run:
    .\.venv\Scripts\python.exe research\intraday_discovery.py [--symbol EURUSD] [--tf H1]
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "mt5"
OUT = HERE / "results" / "intraday_discovery.csv"
MANIFEST = HERE / "results" / "intraday_discovery_manifest.json"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]

# Chronological splits (H1 bars)
SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-01-01"),
    "OOS": ("2024-01-01", "2026-12-31"),
}

PASS_PF = 1.05
PASS_SHARPE = 0.25
PASS_MAXDD = -30.0

BARS_PER_YEAR_H1 = 24 * 252


def load_csv(symbol: str, tf: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing: {path}")
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce").dt.tz_convert(None)
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def wilder_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def fractal_high(high: pd.Series, k: int = 5) -> pd.Series:
    """Bar i is a k-bar fractal high if high[i] > high[i±k]."""
    sig = pd.Series(False, index=high.index)
    for i in range(k, len(high) - k):
        window = high.iloc[i - k : i + k + 1]
        if high.iloc[i] == window.max() and (window == high.iloc[i]).sum() == 1:
            sig.iloc[i] = True
    return sig


def fractal_low(low: pd.Series, k: int = 5) -> pd.Series:
    """Bar i is a k-bar fractal low if low[i] < low[i±k]."""
    sig = pd.Series(False, index=low.index)
    for i in range(k, len(low) - k):
        window = low.iloc[i - k : i + k + 1]
        if low.iloc[i] == window.min() and (window == low.iloc[i]).sum() == 1:
            sig.iloc[i] = True
    return sig


def metrics(returns: pd.Series, bars_per_year: int = BARS_PER_YEAR_H1) -> dict:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0, "pf": 0.0, "winrate": 0.0, "n": 0}
    eq = (1 + r).cumprod()
    years = max(len(r) / bars_per_year, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * math.sqrt(bars_per_year)
    peak = np.maximum.accumulate(eq)
    maxdd = (eq / peak - 1).min()
    wins = r[r > 0]
    losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "pf": round(pf, 2) if pd.notna(pf) else None,
        "winrate": round((r > 0).mean() * 100, 1),
        "n": len(r),
    }


def slice_split(r: pd.Series, start: str, end: str, times: pd.Series) -> pd.Series:
    mask = (times.values >= np.datetime64(pd.Timestamp(start))) & (times.values < np.datetime64(pd.Timestamp(end)))
    return r.iloc[mask]


# =====================================================================
# Hypothesis families — each returns a position series (1 long, -1 short, 0 flat)
# =====================================================================

def hyp_candle_cross(df: pd.DataFrame, consecutive: int = 3, atr_mult: float = 0.0) -> pd.Series:
    """N consecutive same-direction candles, then counter-trend entry on cross.
    
    Long: last N bars all red (close<open), current bar closes above its open
    (bullish cross). Short: mirror.
    Position held until opposite cross or stop/TP.
    """
    close = df["close"]
    open_ = df["open"]
    atr = wilder_atr(df, 14)
    n = len(df)
    pos = pd.Series(0, index=df.index)

    red = close < open_
    green = close > open_

    # rolling count of consecutive same-color
    red_run = red.astype(int)
    green_run = green.astype(int)
    for i in range(1, n):
        if red.iloc[i]:
            red_run.iloc[i] = red_run.iloc[i - 1] + 1
            green_run.iloc[i] = 0
        elif green.iloc[i]:
            green_run.iloc[i] = green_run.iloc[i - 1] + 1
            red_run.iloc[i] = 0
        else:
            red_run.iloc[i] = 0
            green_run.iloc[i] = 0

    state = 0
    entry_bar = -10**9
    for i in range(consecutive + 1, n):
        if state > 0:
            held = i - entry_bar
            # exit on bearish cross (N red then green) or reverse cross
            if green_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
            elif close.iloc[i] <= df["low"].iloc[entry_bar] - atr_mult * atr.iloc[i]:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            held = i - entry_bar
            if red_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
            elif close.iloc[i] >= df["high"].iloc[entry_bar] + atr_mult * atr.iloc[i]:
                state = 0
                entry_bar = -10**9
        else:
            # entry: N red then green cross (long), N green then red cross (short)
            if red_run.iloc[i - 1] >= consecutive and green.iloc[i]:
                state = 1
                entry_bar = i
            elif green_run.iloc[i - 1] >= consecutive and red.iloc[i]:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_rsi_extreme(df: pd.DataFrame, rsi_period: int = 9, lo: float = 20.0, hi: float = 80.0,
                     mid: float = 50.0, min_hold: int = 6) -> pd.Series:
    """RSI extreme mean reversion. Long when RSI crosses below lo, short when above hi.
    Exit when RSI crosses back through mid (after min_hold bars) or opposite extreme.
    """
    rsi = wilder_rsi(df["close"], rsi_period)
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    for i in range(1, n):
        r, rp = rsi.iloc[i], rsi.iloc[i - 1]
        if not (np.isfinite(r) and np.isfinite(rp)):
            pos.iloc[i] = state
            continue
        if state > 0:
            held = i - entry_bar
            if r > hi:  # opposite extreme
                state = 0
                entry_bar = -10**9
            elif held >= min_hold and rp <= mid and r > mid:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            held = i - entry_bar
            if r < lo:
                state = 0
                entry_bar = -10**9
            elif held >= min_hold and rp >= mid and r < mid:
                state = 0
                entry_bar = -10**9
        else:
            if rp >= lo and r < lo:
                state = 1
                entry_bar = i
            elif rp <= hi and r > hi:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_fractal_breakout(df: pd.DataFrame, k: int = 5, retrace_bars: int = 3) -> pd.Series:
    """Fractal breakout: buy when close breaks above confirmed fractal high,
    sell when below fractal low. Exit on opposite fractal break.
    """
    f_high = fractal_high(df["high"], k)
    f_low = fractal_low(df["low"], k)
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    last_fh = -1
    last_fl = -1
    for i in range(k + 1, n):
        if f_high.iloc[i]:
            last_fh = i
        if f_low.iloc[i]:
            last_fl = i
        if state > 0:
            # exit on lower fractal low confirmed after entry
            if last_fl > entry_bar and i - last_fl >= k:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if last_fh > entry_bar and i - last_fh >= k:
                state = 0
                entry_bar = -10**9
        else:
            if last_fh >= 0 and i - last_fh <= retrace_bars and close_break(df, i, last_fh, "high"):
                state = 1
                entry_bar = i
            elif last_fl >= 0 and i - last_fl <= retrace_bars and close_break(df, i, last_fl, "low"):
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def close_break(df: pd.DataFrame, i: int, ref: int, kind: str) -> bool:
    c = df["close"].iloc[i]
    ref_val = df["high"].iloc[ref] if kind == "high" else df["low"].iloc[ref]
    if kind == "high":
        return c > ref_val
    return c < ref_val


def hyp_rubberband(df: pd.DataFrame, ema_period: int = 21, ext_atr: float = 2.0,
                    retrace_atr: float = 0.5, atr_period: int = 14) -> pd.Series:
    """Rubberband: price extended > ext_atr*ATR from EMA, fade back toward EMA.
    Long when close < EMA - ext_atr*ATR, exit when close >= EMA - retrace_atr*ATR.
    Short mirror."""
    e = ema(df["close"], ema_period)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    for i in range(ema_period + atr_period, n):
        e_val = e.iloc[i]
        a_val = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(e_val) and np.isfinite(a_val) and a_val > 0):
            pos.iloc[i] = state
            continue
        ext_hi = e_val + ext_atr * a_val
        ext_lo = e_val - ext_atr * a_val
        ret_hi = e_val + retrace_atr * a_val
        ret_lo = e_val - retrace_atr * a_val
        if state > 0:
            if c >= ret_lo:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if c <= ret_hi:
                state = 0
                entry_bar = -10**9
        else:
            if c < ext_lo:
                state = 1
                entry_bar = i
            elif c > ext_hi:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_ema_extension_fade(df: pd.DataFrame, ema_period: int = 21, ext_atr: float = 1.5,
                            atr_period: int = 14) -> pd.Series:
    """Fade price extended beyond ext_atr*ATR from EMA21, target EMA."""
    e = ema(df["close"], ema_period)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    for i in range(ema_period + atr_period, n):
        e_val = e.iloc[i]
        a_val = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(e_val) and np.isfinite(a_val) and a_val > 0):
            pos.iloc[i] = state
            continue
        band = ext_atr * a_val
        if state > 0:
            if c >= e_val:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if c <= e_val:
                state = 0
                entry_bar = -10**9
        else:
            if c < e_val - band:
                state = 1
                entry_bar = i
            elif c > e_val + band:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_session_momentum(df: pd.DataFrame, session_hour: int = 8, hold_bars: int = 4) -> pd.Series:
    """London/NY open momentum: enter in direction of first bar of session,
    hold for hold_bars bars."""
    times = df["time"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    for i in range(1, n):
        h = times.iloc[i].hour
        prev_h = times.iloc[i - 1].hour
        # new session bar
        if h == session_hour and prev_h != session_hour:
            direction = 1 if df["close"].iloc[i] > df["open"].iloc[i] else -1
            for j in range(i, min(i + hold_bars, n)):
                pos.iloc[j] = direction
    return pos


def hyp_atr_squeeze_breakout(df: pd.DataFrame, vol_w: int = 20, q: float = 0.20,
                              hold_bars: int = 6) -> pd.Series:
    """ATR squeeze (low vol) followed by breakout in direction of first expansion bar."""
    atr = wilder_atr(df, 14)
    close = df["close"]
    open_ = df["open"]
    atr_rank = atr.rolling(vol_w, min_periods=10).rank(pct=True)
    n = len(df)
    pos = pd.Series(0, index=df.index)
    squeeze_until = -1
    for i in range(vol_w, n):
        if squeeze_until >= 0 and i <= squeeze_until:
            continue
        if atr_rank.iloc[i] < q:
            # squeeze detected; wait for expansion breakout
            for j in range(i + 1, min(i + hold_bars, n)):
                if atr_rank.iloc[j] >= q:
                    direction = 1 if close.iloc[j] > open_.iloc[j] else -1
                    for k in range(j, min(j + hold_bars, n)):
                        pos.iloc[k] = direction
                    squeeze_until = j + hold_bars
                    break
    return pos


def hyp_structure_bos(df: pd.DataFrame, k: int = 5, confirm_bars: int = 2) -> pd.Series:
    """Break of Structure: price breaks above recent swing high (fractal high)
    with confirmation, long; breaks below swing low, short. Exit on opposite BOS."""
    f_high = fractal_high(df["high"], k)
    f_low = fractal_low(df["low"], k)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    last_swing_high = -1
    last_swing_low = -1
    for i in range(k + confirm_bars, n):
        if f_high.iloc[i]:
            last_swing_high = i
        if f_low.iloc[i]:
            last_swing_low = i
        if state > 0:
            # exit on break below last swing low
            if last_swing_low > entry_bar and low.iloc[i] < low.iloc[last_swing_low]:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if last_swing_high > entry_bar and high.iloc[i] > high.iloc[last_swing_high]:
                state = 0
                entry_bar = -10**9
        else:
            if last_swing_high >= 0 and i - last_swing_high <= confirm_bars + 2:
                if close.iloc[i] > high.iloc[last_swing_high]:
                    state = 1
                    entry_bar = i
            elif last_swing_low >= 0 and i - last_swing_low <= confirm_bars + 2:
                if close.iloc[i] < low.iloc[last_swing_low]:
                    state = -1
                    entry_bar = i
        pos.iloc[i] = state
    return pos


# =====================================================================
# Follow-through hypotheses — test whether price actually continued
# after the signal, not just touched it (fake cross filter)
# =====================================================================

def hyp_follow_through_cross(df: pd.DataFrame, consecutive: int = 3,
                              confirm_bars: int = 1) -> pd.Series:
    """Candle cross with follow-through confirmation.
    
    Entry signal: N same-color candles then opposite-color cross.
    Only enter if the cross bar itself closes beyond the prior bar's
    opposite extreme (follow-through), and hold only while price keeps
    making progress in signal direction. Exit on opposite cross or
    when progress stalls for confirm_bars bars.
    """
    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    atr = wilder_atr(df, 14)
    n = len(df)
    pos = pd.Series(0, index=df.index)

    red = close < open_
    green = close > open_

    red_run = red.astype(int)
    green_run = green.astype(int)
    for i in range(1, n):
        if red.iloc[i]:
            red_run.iloc[i] = red_run.iloc[i - 1] + 1
            green_run.iloc[i] = 0
        elif green.iloc[i]:
            green_run.iloc[i] = green_run.iloc[i - 1] + 1
            red_run.iloc[i] = 0
        else:
            red_run.iloc[i] = 0
            green_run.iloc[i] = 0

    state = 0
    entry_bar = -10**9
    stall_count = 0
    best_px = 0.0
    for i in range(consecutive + confirm_bars, n):
        if state > 0:  # long
            # progress: new high since entry
            if close.iloc[i] > best_px:
                best_px = close.iloc[i]
                stall_count = 0
            else:
                stall_count += 1
            # exit: bearish cross, or stall, or reverse fractal
            if green_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
                stall_count = 0
            elif stall_count >= confirm_bars + 2:
                state = 0
                entry_bar = -10**9
                stall_count = 0
        elif state < 0:  # short
            if close.iloc[i] < best_px:
                best_px = close.iloc[i]
                stall_count = 0
            else:
                stall_count += 1
            if red_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
                stall_count = 0
            elif stall_count >= confirm_bars + 2:
                state = 0
                entry_bar = -10**9
                stall_count = 0
        else:
            # entry with follow-through: cross bar must close beyond
            # the opposite extreme of the run
            if red_run.iloc[i - 1] >= consecutive and green.iloc[i]:
                # long: cross bar close must exceed the highest high of the red run
                run_start = i - red_run.iloc[i - 1]
                run_high = high.iloc[run_start:i].max()
                if close.iloc[i] > run_high:
                    state = 1
                    entry_bar = i
                    best_px = close.iloc[i]
                    stall_count = 0
            elif green_run.iloc[i - 1] >= consecutive and red.iloc[i]:
                run_start = i - green_run.iloc[i - 1]
                run_low = low.iloc[run_start:i].min()
                if close.iloc[i] < run_low:
                    state = -1
                    entry_bar = i
                    best_px = close.iloc[i]
                    stall_count = 0
        pos.iloc[i] = state
    return pos


def hyp_rsi_extreme_trend(df: pd.DataFrame, rsi_period: int = 9,
                           lo: float = 25.0, hi: float = 75.0,
                           mid: float = 50.0, min_hold: int = 6,
                           ema_period: int = 21) -> pd.Series:
    """RSI extreme mean reversion WITH trend filter.
    
    Only go long on RSI < lo when EMA21 slope is up (uptrend pullback).
    Only go short on RSI > hi when EMA21 slope is down (downtrend rally).
    This filters counter-trend fake crosses.
    """
    rsi = wilder_rsi(df["close"], rsi_period)
    e = ema(df["close"], ema_period)
    atr = wilder_atr(df, 14)
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    for i in range(max(rsi_period, ema_period, 14), n):
        r, rp = rsi.iloc[i], rsi.iloc[i - 1]
        e_val, e_prev = e.iloc[i], e.iloc[i - 1]
        a_val = atr.iloc[i]
        if not (np.isfinite(r) and np.isfinite(rp) and np.isfinite(e_val)
                and np.isfinite(e_prev) and np.isfinite(a_val) and a_val > 0):
            pos.iloc[i] = state
            continue
        trend = 1 if e_val > e_prev else -1  # EMA direction
        if state > 0:
            held = i - entry_bar
            if r > hi:
                state = 0
                entry_bar = -10**9
            elif held >= min_hold and rp <= mid and r > mid:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            held = i - entry_bar
            if r < lo:
                state = 0
                entry_bar = -10**9
            elif held >= min_hold and rp >= mid and r < mid:
                state = 0
                entry_bar = -10**9
        else:
            if trend > 0 and rp >= lo and r < lo:
                state = 1
                entry_bar = i
            elif trend < 0 and rp <= hi and r > hi:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_fractal_confirmed(df: pd.DataFrame, k: int = 5,
                           confirm_bars: int = 2) -> pd.Series:
    """Fractal breakout with close confirmation.
    
    Enter long only if close breaks above fractal high AND stays above
    it for confirm_bars consecutive bars. Enter short mirror.
    Exit on opposite fractal confirmation.
    """
    f_high = fractal_high(df["high"], k)
    f_low = fractal_low(df["low"], k)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    conf_count = 0
    last_fh = -1
    last_fl = -1
    for i in range(k + confirm_bars, n):
        if f_high.iloc[i]:
            last_fh = i
        if f_low.iloc[i]:
            last_fl = i
        if state > 0:
            # exit on confirmed lower close below last swing low
            if last_fl > entry_bar and close.iloc[i] < low.iloc[last_fl]:
                conf_count = 0
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if last_fh > entry_bar and close.iloc[i] > high.iloc[last_fh]:
                conf_count = 0
                state = 0
                entry_bar = -10**9
        else:
            if last_fh >= 0 and i - last_fh <= confirm_bars + 2:
                if close.iloc[i] > high.iloc[last_fh]:
                    conf_count += 1
                    if conf_count >= confirm_bars:
                        state = 1
                        entry_bar = i
                        conf_count = 0
                else:
                    conf_count = 0
            elif last_fl >= 0 and i - last_fl <= confirm_bars + 2:
                if close.iloc[i] < low.iloc[last_fl]:
                    conf_count += 1
                    if conf_count >= confirm_bars:
                        state = -1
                        entry_bar = i
                        conf_count = 0
                else:
                    conf_count = 0
        pos.iloc[i] = state
    return pos


def hyp_rubberband_retrace(df: pd.DataFrame, ema_period: int = 21,
                            ext_atr: float = 2.0, retrace_frac: float = 0.5,
                            atr_period: int = 14) -> pd.Series:
    """Rubberband fade with retrace confirmation.
    
    Only enter when price has extended beyond ext_atr*ATR from EMA
    AND has already started retracing (close moved back toward EMA
    by at least retrace_frac of the extension distance).
    Exit when close reaches EMA or extends again.
    """
    e = ema(df["close"], ema_period)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    extreme_px = 0.0
    extreme_side = 0
    for i in range(ema_period + atr_period, n):
        e_val = e.iloc[i]
        a_val = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(e_val) and np.isfinite(a_val) and a_val > 0):
            pos.iloc[i] = state
            continue
        band = ext_atr * a_val
        if state > 0:  # long fade (price was below EMA - band)
            # exit when back to EMA or re-extends down
            if c >= e_val:
                state = 0
                entry_bar = -10**9
            elif c < e_val - band:
                state = 0
                entry_bar = -10**9
        elif state < 0:  # short fade
            if c <= e_val:
                state = 0
                entry_bar = -10**9
            elif c > e_val + band:
                state = 0
                entry_bar = -10**9
        else:
            # detect extension
            if c < e_val - band:
                extreme_px = c
                extreme_side = 1
                # need retrace confirmation on next bars
                for j in range(i + 1, min(i + 6, n)):
                    c2 = close.iloc[j]
                    e2 = e.iloc[j]
                    # retrace = moved back toward EMA by retrace_frac of extension
                    retrace_dist = e2 - extreme_px
                    ext_dist = e2 - (e2 - band)
                    if retrace_dist >= retrace_frac * ext_dist:
                        state = 1
                        entry_bar = j
                        break
                extreme_side = 0
            elif c > e_val + band:
                extreme_px = c
                extreme_side = -1
                for j in range(i + 1, min(i + 6, n)):
                    c2 = close.iloc[j]
                    e2 = e.iloc[j]
                    retrace_dist = extreme_px - e2
                    ext_dist = band
                    if retrace_dist >= retrace_frac * ext_dist:
                        state = -1
                        entry_bar = j
                        break
                extreme_side = 0
        pos.iloc[i] = state
    return pos


def hyp_ema_extension_momentum(df: pd.DataFrame, ema_period: int = 21,
                                ext_atr: float = 2.0, atr_period: int = 14) -> pd.Series:
    """EMA extension fade with momentum candle confirmation.
    
    Only enter when price is extended beyond ext_atr*ATR from EMA
    AND the entry candle closes in the fade direction (close > open for
    long fade at lower extreme, close < open for short fade at upper extreme).
    Exit at EMA.
    """
    e = ema(df["close"], ema_period)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    open_ = df["open"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    for i in range(ema_period + atr_period, n):
        e_val = e.iloc[i]
        a_val = atr.iloc[i]
        c = close.iloc[i]
        o = open_.iloc[i]
        if not (np.isfinite(e_val) and np.isfinite(a_val) and a_val > 0):
            pos.iloc[i] = state
            continue
        band = ext_atr * a_val
        if state > 0:
            if c >= e_val:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if c <= e_val:
                state = 0
                entry_bar = -10**9
        else:
            if c < e_val - band and c > o:  # long fade + bullish candle
                state = 1
                entry_bar = i
            elif c > e_val + band and c < o:  # short fade + bearish candle
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_cross_body_size(df: pd.DataFrame, consecutive: int = 3,
                         body_atr_mult: float = 0.5) -> pd.Series:
    """Candle cross only when the cross candle body is significant.
    
    Filters doji/fake crosses by requiring the cross candle's body
    to be at least body_atr_mult * ATR in size.
    """
    close = df["close"]
    open_ = df["open"]
    atr = wilder_atr(df, 14)
    n = len(df)
    pos = pd.Series(0, index=df.index)

    red = close < open_
    green = close > open_
    body = abs(close - open_)

    red_run = red.astype(int)
    green_run = green.astype(int)
    for i in range(1, n):
        if red.iloc[i]:
            red_run.iloc[i] = red_run.iloc[i - 1] + 1
            green_run.iloc[i] = 0
        elif green.iloc[i]:
            green_run.iloc[i] = green_run.iloc[i - 1] + 1
            red_run.iloc[i] = 0
        else:
            red_run.iloc[i] = 0
            green_run.iloc[i] = 0

    state = 0
    entry_bar = -10**9
    for i in range(consecutive + 1, n):
        if state > 0:
            if green_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if red_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
        else:
            if red_run.iloc[i - 1] >= consecutive and green.iloc[i]:
                if body.iloc[i] >= body_atr_mult * atr.iloc[i]:
                    state = 1
                    entry_bar = i
            elif green_run.iloc[i - 1] >= consecutive and red.iloc[i]:
                if body.iloc[i] >= body_atr_mult * atr.iloc[i]:
                    state = -1
                    entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_delayed_rsi(df: pd.DataFrame, rsi_period: int = 9,
                     lo: float = 25.0, hi: float = 75.0,
                     mid: float = 50.0, min_hold: int = 6,
                     delay_bars: int = 1) -> pd.Series:
    """RSI extreme with delayed entry — enter N bars AFTER the cross.
    
    This filters wick fakes: if RSI crosses below lo but immediately
    reverses, the delayed entry avoids it. Enter only if RSI is still
    extreme after delay_bars bars.
    """
    rsi = wilder_rsi(df["close"], rsi_period)
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9
    cross_bar = -10**9
    cross_side = 0
    for i in range(1, n):
        r, rp = rsi.iloc[i], rsi.iloc[i - 1]
        if not (np.isfinite(r) and np.isfinite(rp)):
            pos.iloc[i] = state
            continue
        if state > 0:
            if r > hi:
                state = 0
                entry_bar = -10**9
            elif rp <= mid and r > mid:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if r < lo:
                state = 0
                entry_bar = -10**9
            elif rp >= mid and r < mid:
                state = 0
                entry_bar = -10**9
        else:
            # detect cross
            if cross_side == 0:
                if rp >= lo and r < lo:
                    cross_bar = i
                    cross_side = 1
                elif rp <= hi and r > hi:
                    cross_bar = i
                    cross_side = -1
            # delayed entry
            if cross_side != 0 and i - cross_bar >= delay_bars:
                if cross_side == 1 and r < lo:  # still extreme
                    state = 1
                    entry_bar = i
                    cross_side = 0
                    cross_bar = -10**9
                elif cross_side == -1 and r > hi:
                    state = -1
                    entry_bar = i
                    cross_side = 0
                    cross_bar = -10**9
                elif (cross_side == 1 and r >= mid) or (cross_side == -1 and r <= mid):
                    # reversed before entry — fake
                    cross_side = 0
                    cross_bar = -10**9
        pos.iloc[i] = state
    return pos


# =====================================================================
# MA Compression / Expansion hypotheses — address user's insight:
# "when all the moving averages are too close, we get bad moves.
# Detect when compression is ending and expansion begins."
# =====================================================================

def hyp_ma_compression_filter(df: pd.DataFrame, ema_fast: int = 8,
                               ema_mid: int = 21, ema_slow: int = 50,
                               compress_atr: float = 0.5,
                               expand_atr: float = 1.0,
                               atr_period: int = 14) -> pd.Series:
    """Trade only when MAs are expanding (diverging), not compressed.
    
    Bad moves: all MAs within compress_atr*ATR of each other (chop).
    Good moves: MAs have expanded beyond expand_atr*ATR separation.
    
    Entry: long when fast > mid > slow AND separation > expand_atr*ATR
           short when fast < mid < slow AND separation > expand_atr*ATR
    Exit: when MAs re-compress below compress_atr*ATR or trend flips.
    """
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -10**9

    for i in range(ema_slow + atr_period, n):
        e8v, e21v, e50v = e8.iloc[i], e21.iloc[i], e50.iloc[i]
        av = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(e8v) and np.isfinite(e21v) and np.isfinite(e50v)
                and np.isfinite(av) and av > 0):
            pos.iloc[i] = state
            continue

        # MA spread: max distance between any pair, normalized by ATR
        ma_spread = max(abs(e8v - e21v), abs(e21v - e50v), abs(e8v - e50v)) / av
        bullish = e8v > e21v > e50v
        bearish = e8v < e21v < e50v

        if state > 0:
            # exit: MA re-compresses or trend flips
            if ma_spread < compress_atr or not bullish:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if ma_spread < compress_atr or not bearish:
                state = 0
                entry_bar = -10**9
        else:
            # entry: MAs expanding beyond threshold
            if bullish and ma_spread > expand_atr:
                state = 1
                entry_bar = i
            elif bearish and ma_spread > expand_atr:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_ma_expansion_breakout(df: pd.DataFrame, ema_fast: int = 8,
                               ema_mid: int = 21, ema_slow: int = 50,
                               compress_atr: float = 0.5,
                               atr_period: int = 14,
                               confirm_bars: int = 2) -> pd.Series:
    """Wait for MA compression, then trade the breakout in expansion direction.
    
    1. Detect compression: all MAs within compress_atr*ATR
    2. Wait for price to break out of the MA bundle
    3. Enter in direction of breakout, hold while expanding
    
    This directly addresses: don't be too late — enter on first expansion
    candle after compression ends.
    """
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    n = len(df)
    pos = pd.Series(0, index=df.index)

    state = 0
    entry_bar = -10**9
    compressed_since = -1
    expansion_dir = 0  # +1 bullish expansion, -1 bearish

    for i in range(ema_slow + atr_period + confirm_bars, n):
        e8v, e21v, e50v = e8.iloc[i], e21.iloc[i], e50.iloc[i]
        av = atr.iloc[i]
        c = close.iloc[i]
        o = open_.iloc[i]
        h = high.iloc[i]
        l = low.iloc[i]
        if not (np.isfinite(e8v) and np.isfinite(e21v) and np.isfinite(e50v)
                and np.isfinite(av) and av > 0):
            pos.iloc[i] = state
            continue

        ma_spread = max(abs(e8v - e21v), abs(e21v - e50v), abs(e8v - e50v)) / av
        compressed = ma_spread < compress_atr
        ma_mid = (e8v + e21v + e50v) / 3.0

        if state > 0:
            # exit: MAs re-compress or price crosses back below MA bundle
            if compressed or c < ma_mid:
                state = 0
                entry_bar = -10**9
                compressed_since = -1
                expansion_dir = 0
        elif state < 0:
            if compressed or c > ma_mid:
                state = 0
                entry_bar = -10**9
                compressed_since = -1
                expansion_dir = 0
        else:
            # detect compression start
            if compressed:
                if compressed_since < 0:
                    compressed_since = i
            else:
                compressed_since = -1

            # breakout from compression: price moves beyond MA bundle + ATR buffer
            if compressed_since >= 0 and i - compressed_since >= 1:
                breakout_buf = av * 0.5
                if c > ma_mid + breakout_buf and o <= ma_mid + breakout_buf:
                    # bullish breakout
                    state = 1
                    entry_bar = i
                    compressed_since = -1
                    expansion_dir = 1
                elif c < ma_mid - breakout_buf and o >= ma_mid - breakout_buf:
                    # bearish breakout
                    state = -1
                    entry_bar = i
                    compressed_since = -1
                    expansion_dir = -1

        pos.iloc[i] = state
    return pos


def hyp_compression_rubberband(df: pd.DataFrame, ema_fast: int = 8,
                                ema_mid: int = 21, ema_slow: int = 50,
                                compress_atr: float = 0.5,
                                ext_atr: float = 2.0,
                                atr_period: int = 14) -> pd.Series:
    """Rubberband fade FROM MA compression extremes.
    
    When MAs are compressed, price often extends far from the bundle
    then snaps back. Fade the extension toward the MA center.
    
    Entry: long when price < MA_center - ext_atr*ATR during compression
           short when price > MA_center + ext_atr*ATR during compression
    Exit: when price reaches MA center or extends further.
    """
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)

    state = 0
    entry_bar = -10**9

    for i in range(ema_slow + atr_period, n):
        e8v, e21v, e50v = e8.iloc[i], e21.iloc[i], e50.iloc[i]
        av = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(e8v) and np.isfinite(e21v) and np.isfinite(e50v)
                and np.isfinite(av) and av > 0):
            pos.iloc[i] = state
            continue

        ma_spread = max(abs(e8v - e21v), abs(e21v - e50v), abs(e8v - e50v)) / av
        compressed = ma_spread < compress_atr
        ma_center = (e8v + e21v + e50v) / 3.0
        band = ext_atr * av

        if state > 0:
            if c >= ma_center:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if c <= ma_center:
                state = 0
                entry_bar = -10**9
        else:
            if compressed:
                if c < ma_center - band:
                    state = 1
                    entry_bar = i
                elif c > ma_center + band:
                    state = -1
                    entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_ma_slope_convergence(df: pd.DataFrame, ema_fast: int = 8,
                              ema_mid: int = 21, ema_slow: int = 50,
                              slope_window: int = 5,
                              atr_period: int = 14) -> pd.Series:
    """Trade when MA slopes converge (compression) then diverge (expansion).
    
    Detect: all three MA slopes pointing same direction AND magnitude
    increasing (expansion) after a period of flat/convergent slopes.
    
    This is the "not too late" detector — enters as slopes start
    diverging, not after the move is already extended.
    """
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)

    # MA slopes (change over slope_window bars)
    s8 = e8.diff(slope_window)
    s21 = e21.diff(slope_window)
    s50 = e50.diff(slope_window)

    state = 0
    entry_bar = -10**9
    convergent_bars = 0

    for i in range(ema_slow + atr_period + slope_window, n):
        s8v, s21v, s50v = s8.iloc[i], s21.iloc[i], s50.iloc[i]
        av = atr.iloc[i]
        c = close.iloc[i]
        if not (np.isfinite(s8v) and np.isfinite(s21v) and np.isfinite(s50v)
                and np.isfinite(av) and av > 0):
            pos.iloc[i] = state
            continue

        # Normalize slopes by ATR
        n8, n21, n50 = s8v / av, s21v / av, s50v / av

        # All slopes same direction?
        all_up = n8 > 0 and n21 > 0 and n50 > 0
        all_down = n8 < 0 and n21 < 0 and n50 < 0

        # Convergence: slopes are small (near zero) — compression
        convergent = abs(n8) < 0.15 and abs(n21) < 0.15 and abs(n50) < 0.15
        # Divergence: slopes are large and same direction — expansion
        divergent_up = n8 > 0.3 and n21 > 0.2 and n50 > 0.1
        divergent_down = n8 < -0.3 and n21 < -0.2 and n50 < -0.1

        if state > 0:
            if not all_up or convergent:
                state = 0
                entry_bar = -10**9
                convergent_bars = 0
        elif state < 0:
            if not all_down or convergent:
                state = 0
                entry_bar = -10**9
                convergent_bars = 0
        else:
            if convergent:
                convergent_bars += 1
            else:
                convergent_bars = 0

            # Enter: divergence after convergence (not too late)
            if convergent_bars >= 3 and divergent_up:
                state = 1
                entry_bar = i
                convergent_bars = 0
            elif convergent_bars >= 3 and divergent_down:
                state = -1
                entry_bar = i
                convergent_bars = 0

        pos.iloc[i] = state
    return pos


HYPOTHESES = [
    ("candle_cross_3", lambda d: hyp_candle_cross(d, consecutive=3)),
    ("candle_cross_5", lambda d: hyp_candle_cross(d, consecutive=5)),
    ("rsi_extreme_9_20_80", lambda d: hyp_rsi_extreme(d, rsi_period=9, lo=20.0, hi=80.0)),
    ("rsi_extreme_9_25_75", lambda d: hyp_rsi_extreme(d, rsi_period=9, lo=25.0, hi=75.0)),
    ("rsi_extreme_14_30_70", lambda d: hyp_rsi_extreme(d, rsi_period=14, lo=30.0, hi=70.0)),
    ("fractal_breakout_k5", lambda d: hyp_fractal_breakout(d, k=5)),
    ("fractal_breakout_k3", lambda d: hyp_fractal_breakout(d, k=3)),
    ("rubberband_21_2.0", lambda d: hyp_rubberband(d, ema_period=21, ext_atr=2.0)),
    ("rubberband_21_1.5", lambda d: hyp_rubberband(d, ema_period=21, ext_atr=1.5)),
    ("ema_extension_fade_1.5", lambda d: hyp_ema_extension_fade(d, ext_atr=1.5)),
    ("ema_extension_fade_2.0", lambda d: hyp_ema_extension_fade(d, ext_atr=2.0)),
    ("session_london_mom", lambda d: hyp_session_momentum(d, session_hour=8, hold_bars=4)),
    ("session_ny_mom", lambda d: hyp_session_momentum(d, session_hour=13, hold_bars=4)),
    ("atr_squeeze_breakout", lambda d: hyp_atr_squeeze_breakout(d)),
    ("structure_bos_k5", lambda d: hyp_structure_bos(d, k=5)),
    ("structure_bos_k3", lambda d: hyp_structure_bos(d, k=3)),
    # Follow-through variants
    ("ft_cross_3_confirm1", lambda d: hyp_follow_through_cross(d, consecutive=3, confirm_bars=1)),
    ("ft_rsi_trend_9_25_75", lambda d: hyp_rsi_extreme_trend(d, rsi_period=9, lo=25.0, hi=75.0)),
    ("ft_rsi_trend_14_30_70", lambda d: hyp_rsi_extreme_trend(d, rsi_period=14, lo=30.0, hi=70.0)),
    ("ft_fractal_conf_k5", lambda d: hyp_fractal_confirmed(d, k=5, confirm_bars=2)),
    ("ft_rubberband_retrace_21_2.0", lambda d: hyp_rubberband_retrace(d, ema_period=21, ext_atr=2.0)),
    ("ft_ema_mom_21_2.0", lambda d: hyp_ema_extension_momentum(d, ema_period=21, ext_atr=2.0)),
    ("ft_cross_body_3_0.5", lambda d: hyp_cross_body_size(d, consecutive=3, body_atr_mult=0.5)),
    ("ft_delayed_rsi_9_25_75", lambda d: hyp_delayed_rsi(d, rsi_period=9, lo=25.0, hi=75.0, delay_bars=1)),
    # MA compression / expansion (user's key insight)
    ("ma_compression_filter", lambda d: hyp_ma_compression_filter(d)),
    ("ma_expansion_breakout", lambda d: hyp_ma_expansion_breakout(d)),
    ("compression_rubberband", lambda d: hyp_compression_rubberband(d)),
    ("ma_slope_convergence", lambda d: hyp_ma_slope_convergence(d)),
]


def backtest_positions(df: pd.DataFrame, pos: pd.Series, symbol: str,
                       comm_pts: float = 7.0, slip_pts: float = 1.0,
                       floor_pts: float = 1.0, risk_pct: float = 0.5) -> dict:
    """Backtest a position series with per-bar returns and real cost model.
    Position sizing: risk_pct of equity per trade, stop = 1.5 ATR."""
    df = df.copy()
    atr = wilder_atr(df, 14)
    close = df["close"]
    n = len(df)
    point = 0.001 if symbol.endswith("JPY") else 0.00001
    spread_eff = np.maximum(df["spread"].to_numpy(float), floor_pts)
    cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
    cost_side = cost_side_pts * point
    tick_size = point

    equity = 10000.0
    equity_curve = [equity]
    trades = []
    in_position = False
    side = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_atr = 0.0
    entry_cost_dollars = 0.0

    for i in range(1, n):
        sig = pos.iloc[i - 1]  # signal from previous bar, execute at bar i open/close
        c = close.iloc[i]
        prev_c = close.iloc[i - 1]
        a = atr.iloc[i]

        # Manage open position
        if in_position:
            hit = False
            if side > 0:
                # long exit: stop hit, TP hit, or signal flip
                if c <= stop_price:
                    pnl = (stop_price - entry_price) / tick_size * 0.01
                    equity += pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append(("long", pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01, "stop"))
                    in_position = False
                    side = 0
                    hit = True
                elif c >= tp_price and not hit:
                    pnl = (tp_price - entry_price) / tick_size * 0.01
                    equity += pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append(("long", pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01, "tp"))
                    in_position = False
                    side = 0
                    hit = True
                elif sig <= 0 and not hit:
                    pnl = (c - entry_price) / tick_size * 0.01
                    equity += pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append(("long", pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01, "signal"))
                    in_position = False
                    side = 0
                    hit = True
            else:
                if c >= stop_price:
                    pnl = (entry_price - stop_price) / tick_size * 0.01
                    equity += pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append(("short", pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01, "stop"))
                    in_position = False
                    side = 0
                    hit = True
                elif c <= tp_price and not hit:
                    pnl = (entry_price - tp_price) / tick_size * 0.01
                    equity += pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append(("short", pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01, "tp"))
                    in_position = False
                    side = 0
                    hit = True
                elif sig >= 0 and not hit:
                    pnl = (entry_price - c) / tick_size * 0.01
                    equity += pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append(("short", pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01, "signal"))
                    in_position = False
                    side = 0
                    hit = True

        # New entry when flat
        if not in_position and sig != 0:
            entry_price = c
            entry_atr = a if a > 0 else 0.001
            stop_dist = entry_atr * 1.5
            if sig > 0:
                stop_price = entry_price - stop_dist
                tp_price = entry_price + stop_dist * 2.0
            else:
                stop_price = entry_price + stop_dist
                tp_price = entry_price - stop_dist * 2.0
            in_position = True
            side = sig
            entry_cost_dollars = cost_side[i] / tick_size * 0.01
            equity -= entry_cost_dollars

        equity_curve.append(equity)

    # close any remaining position at end
    if in_position:
        c = close.iloc[-1]
        if side > 0:
            pnl = (c - entry_price) / tick_size * 0.01
        else:
            pnl = (entry_price - c) / tick_size * 0.01
        exit_cost = cost_side[-1] / tick_size * 0.01
        equity += pnl - entry_cost_dollars - exit_cost
        trades.append((side, pnl - entry_cost_dollars - exit_cost, "end"))

    #--- derive the return series from the equity curve: it is the only record
    #--- that carries the realized costs. The old code filled a separate
    #--- returns_arr with (close - entry_price) - the cumulative gain since
    #--- entry - so any metric taken from it counted one gain once per bar held.
    eq_arr = np.asarray(equity_curve, dtype=float)
    rets = np.zeros(len(df))
    rets[1:len(eq_arr)] = np.diff(eq_arr) / eq_arr[:-1]
    m = metrics(pd.Series(rets, index=df["time"]))
    return m, trades, equity_curve


def evaluate_hypothesis(name: str, fn, symbol: str, df: pd.DataFrame) -> list[dict]:
    try:
        pos = fn(df)
    except Exception as exc:
        return [{"hypothesis": f"{symbol}_{name}", "symbol": symbol, "split": "error",
                 "status": "error", "note": str(exc), "cagr": 0, "sharpe": 0,
                 "maxdd": 0, "pf": None, "winrate": 0, "n": 0}]

    times = df["time"]
    # Build per-bar returns for metric computation
    # Reuse backtest to get equity curve, then compute split metrics from returns
    m_full, trades, eq_curve = backtest_positions(df, pos, symbol)
    eq_arr = np.array(eq_curve)
    bar_rets = np.diff(eq_arr) / eq_arr[:-1]
    bar_rets = pd.Series(bar_rets, index=times.iloc[1:])  # bar_rets[i] = return at bar i+1

    rows = []
    bar_times = times.iloc[1:]
    for split, (start, end) in SPLITS.items():
        part = slice_split(bar_rets, start, end, bar_times)
        m = metrics(part)
        status = "fail"
        if (m["pf"] or 0) >= PASS_PF and m["sharpe"] >= PASS_SHARPE and m["maxdd"] >= PASS_MAXDD:
            status = "pass"
        rows.append({
            "hypothesis": f"{symbol}_{name}", "symbol": symbol, "split": split,
            "status": status, "n_trades": len(trades), **m,
        })
    m_full2 = metrics(bar_rets)
    rows.append({
        "hypothesis": f"{symbol}_{name}", "symbol": symbol, "split": "full",
        "status": "info", "n_trades": len(trades), **m_full2,
    })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Intraday discovery lab on H1 MT5 data")
    ap.add_argument("--symbol", choices=PAIRS + ["all"], default="all")
    ap.add_argument("--tf", choices=["H1"], default="H1")
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    args = ap.parse_args()

    symbols = PAIRS if args.symbol == "all" else [args.symbol]

    all_rows = []
    for sym in symbols:
        print(f"\n=== {sym} {args.tf} ===")
        df = load_csv(sym, args.tf)
        print(f"  bars: {len(df)}  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
        for name, fn in HYPOTHESES:
            rows = evaluate_hypothesis(name, fn, sym, df)
            all_rows.extend(rows)
            for r in rows:
                if r["split"] == "OOS":
                    flag = "PASS" if r["status"] == "pass" else "fail"
                    print(f"  {name:<28} OOS {flag:4} PF={r['pf']} Sharpe={r['sharpe']} "
                          f"maxDD={r['maxdd']}% CAGR={r['cagr']}% n={r['n']}")

    df_out = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUT, index=False)

    oos_pass = df_out[(df_out["split"] == "OOS") & (df_out["status"] == "pass")]
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "tf": args.tf,
        "pass_bar": {"pf": PASS_PF, "sharpe": PASS_SHARPE, "maxdd_pct": PASS_MAXDD},
        "candidates_oos": oos_pass["hypothesis"].tolist(),
        "n_hypotheses": len(HYPOTHESES) * len(symbols),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\n=== OOS PASSES: {len(oos_pass)} ===")
    if not oos_pass.empty:
        print(oos_pass[["hypothesis", "pf", "sharpe", "maxdd", "cagr", "winrate", "n_trades"]].to_string(index=False))
    else:
        print("  None — no hypothesis cleared the OOS proof bar.")
    print(f"\nWrote {OUT}")
    print(f"Wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
