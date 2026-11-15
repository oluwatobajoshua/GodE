#!/usr/bin/env python3
r"""Regime analysis: what factors separate good moves from bad moves?

For each OOS-passing hypothesis from intraday_discovery.py, tags every trade
with market regime features and computes PF/Sharpe/winrate per regime.

Regime features:
  - volatility regime (ATR percentile)
  - trend strength (ADX / EMA slope)
  - session (Asia / London / NY / late)
  - spread (quoted, pctile)
  - time of day (hour)
  - day of week
  - RSI level at entry
  - range position (where close sits in daily range)
  - ATR ratio (current ATR vs 20-bar avg)

Output: research/results/regime_analysis.csv + printed summary.

Run:
    .\.venv\Scripts\python.exe research\regime_analysis.py [--symbol EURUSD]
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
OUT = HERE / "results" / "regime_analysis.csv"
MANIFEST = HERE / "results" / "regime_analysis_manifest.json"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-01-01"),
    "OOS": ("2024-01-01", "2026-12-31"),
}

BARS_PER_YEAR_H1 = 24 * 252


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA / f"{symbol}_{tf}.csv"
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


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))

    tr_s = pd.Series(tr, index=df.index).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_s
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_s

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


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


# =====================================================================
# Hypothesis implementations (same as intraday_discovery.py)
# =====================================================================

def fractal_high(high: pd.Series, k: int = 5) -> pd.Series:
    sig = pd.Series(False, index=high.index)
    for i in range(k, len(high) - k):
        window = high.iloc[i - k : i + k + 1]
        if high.iloc[i] == window.max() and (window == high.iloc[i]).sum() == 1:
            sig.iloc[i] = True
    return sig


def fractal_low(low: pd.Series, k: int = 5) -> pd.Series:
    sig = pd.Series(False, index=low.index)
    for i in range(k, len(low) - k):
        window = low.iloc[i - k : i + k + 1]
        if low.iloc[i] == window.min() and (window == low.iloc[i]).sum() == 1:
            sig.iloc[i] = True
    return sig


def hyp_candle_cross(df: pd.DataFrame, consecutive: int = 3) -> pd.Series:
    close = df["close"]
    open_ = df["open"]
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
                state = 1
                entry_bar = i
            elif green_run.iloc[i - 1] >= consecutive and red.iloc[i]:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_rsi_extreme(df: pd.DataFrame, rsi_period: int = 9, lo: float = 25.0, hi: float = 75.0,
                     mid: float = 50.0, min_hold: int = 6) -> pd.Series:
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
            if r > hi:
                state = 0
                entry_bar = -10**9
            elif i - entry_bar >= min_hold and rp <= mid and r > mid:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if r < lo:
                state = 0
                entry_bar = -10**9
            elif i - entry_bar >= min_hold and rp >= mid and r < mid:
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


def hyp_rubberband(df: pd.DataFrame, ema_period: int = 21, ext_atr: float = 2.0,
                    retrace_atr: float = 0.5, atr_period: int = 14) -> pd.Series:
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


def hyp_atr_squeeze_breakout(df: pd.DataFrame, vol_w: int = 20, q: float = 0.20,
                              hold_bars: int = 6) -> pd.Series:
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
            for j in range(i + 1, min(i + hold_bars, n)):
                if atr_rank.iloc[j] >= q:
                    direction = 1 if close.iloc[j] > open_.iloc[j] else -1
                    for k in range(j, min(j + hold_bars, n)):
                        pos.iloc[k] = direction
                    squeeze_until = j + hold_bars
                    break
    return pos


def hyp_follow_through_cross(df: pd.DataFrame, consecutive: int = 3,
                              confirm_bars: int = 1) -> pd.Series:
    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
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
        if state > 0:
            if close.iloc[i] > best_px:
                best_px = close.iloc[i]
                stall_count = 0
            else:
                stall_count += 1
            if green_run.iloc[i] >= consecutive:
                state = 0
                entry_bar = -10**9
                stall_count = 0
            elif stall_count >= confirm_bars + 2:
                state = 0
                entry_bar = -10**9
                stall_count = 0
        elif state < 0:
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
            if red_run.iloc[i - 1] >= consecutive and green.iloc[i]:
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


def hyp_cross_body_size(df: pd.DataFrame, consecutive: int = 3,
                         body_atr_mult: float = 0.5) -> pd.Series:
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
            if cross_side == 0:
                if rp >= lo and r < lo:
                    cross_bar = i
                    cross_side = 1
                elif rp <= hi and r > hi:
                    cross_bar = i
                    cross_side = -1
            if cross_side != 0 and i - cross_bar >= delay_bars:
                if cross_side == 1 and r < lo:
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
                    cross_side = 0
                    cross_bar = -10**9
        pos.iloc[i] = state
    return pos


def hyp_ema_extension_momentum(df: pd.DataFrame, ema_period: int = 21,
                                ext_atr: float = 2.0, atr_period: int = 14) -> pd.Series:
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
            if c < e_val - band and c > o:
                state = 1
                entry_bar = i
            elif c > e_val + band and c < o:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_session_momentum(df: pd.DataFrame, session_hour: int = 8, hold_bars: int = 4) -> pd.Series:
    times = df["time"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    for i in range(1, n):
        h = times.iloc[i].hour
        prev_h = times.iloc[i - 1].hour
        if h == session_hour and prev_h != session_hour:
            direction = 1 if df["close"].iloc[i] > df["open"].iloc[i] else -1
            for j in range(i, min(i + hold_bars, n)):
                pos.iloc[j] = direction
    return pos


def hyp_structure_bos(df: pd.DataFrame, k: int = 5) -> pd.Series:
    f_high = fractal_high(df["high"], k)
    f_low = fractal_low(df["low"], k)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    n = len(df)
    pos = pd.Series(0, index=df.index)
    state = 0
    entry_bar = -1
    last_sh = -1
    last_sl = -1
    for i in range(k + 2, n):
        if f_high.iloc[i]:
            last_sh = i
        if f_low.iloc[i]:
            last_sl = i
        if state > 0:
            if last_sl > entry_bar and low.iloc[i] < low.iloc[last_sl]:
                state = 0
                entry_bar = -1
        elif state < 0:
            if last_sh > entry_bar and high.iloc[i] > high.iloc[last_sh]:
                state = 0
                entry_bar = -1
        else:
            if last_sh >= 0 and i - last_sh <= 4 and close.iloc[i] > high.iloc[last_sh]:
                state = 1
                entry_bar = i
            elif last_sl >= 0 and i - last_sl <= 4 and close.iloc[i] < low.iloc[last_sl]:
                state = -1
                entry_bar = i
        pos.iloc[i] = state
    return pos


def hyp_ma_compression_filter(df: pd.DataFrame, ema_fast: int = 8,
                               ema_mid: int = 21, ema_slow: int = 50,
                               compress_atr: float = 0.5,
                               expand_atr: float = 1.0,
                               atr_period: int = 14) -> pd.Series:
    """Trade only when MAs are expanding (diverging), not compressed."""
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
        bullish = e8v > e21v > e50v
        bearish = e8v < e21v < e50v

        if state > 0:
            if ma_spread < compress_atr or not bullish:
                state = 0
                entry_bar = -10**9
        elif state < 0:
            if ma_spread < compress_atr or not bearish:
                state = 0
                entry_bar = -10**9
        else:
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
    """Wait for MA compression, then trade the breakout in expansion direction."""
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
            if compressed or c < ma_mid:
                state = 0
                entry_bar = -10**9
                compressed_since = -1
        elif state < 0:
            if compressed or c > ma_mid:
                state = 0
                entry_bar = -10**9
                compressed_since = -1
        else:
            if compressed:
                if compressed_since < 0:
                    compressed_since = i
            else:
                compressed_since = -1

            if compressed_since >= 0 and i - compressed_since >= 1:
                breakout_buf = av * 0.5
                if c > ma_mid + breakout_buf and o <= ma_mid + breakout_buf:
                    state = 1
                    entry_bar = i
                    compressed_since = -1
                elif c < ma_mid - breakout_buf and o >= ma_mid - breakout_buf:
                    state = -1
                    entry_bar = i
                    compressed_since = -1

        pos.iloc[i] = state
    return pos


def hyp_compression_rubberband(df: pd.DataFrame, ema_fast: int = 8,
                                ema_mid: int = 21, ema_slow: int = 50,
                                compress_atr: float = 0.5,
                                ext_atr: float = 2.0,
                                atr_period: int = 14) -> pd.Series:
    """Rubberband fade FROM MA compression extremes."""
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
    """Trade when MA slopes converge (compression) then diverge (expansion)."""
    e8 = ema(df["close"], ema_fast)
    e21 = ema(df["close"], ema_mid)
    e50 = ema(df["close"], ema_slow)
    atr = wilder_atr(df, atr_period)
    close = df["close"]
    n = len(df)
    pos = pd.Series(0, index=df.index)

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

        n8, n21, n50 = s8v / av, s21v / av, s50v / av

        all_up = n8 > 0 and n21 > 0 and n50 > 0
        all_down = n8 < 0 and n21 < 0 and n50 < 0

        convergent = abs(n8) < 0.15 and abs(n21) < 0.15 and abs(n50) < 0.15
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
    ("rsi_extreme_9_25_75", lambda d: hyp_rsi_extreme(d, rsi_period=9, lo=25.0, hi=75.0)),
    ("rsi_extreme_14_30_70", lambda d: hyp_rsi_extreme(d, rsi_period=14, lo=30.0, hi=70.0)),
    ("rubberband_21_2.0", lambda d: hyp_rubberband(d, ema_period=21, ext_atr=2.0)),
    ("rubberband_21_1.5", lambda d: hyp_rubberband(d, ema_period=21, ext_atr=1.5)),
    ("ema_extension_fade_1.5", lambda d: hyp_ema_extension_fade(d, ext_atr=1.5)),
    ("ema_extension_fade_2.0", lambda d: hyp_ema_extension_fade(d, ext_atr=2.0)),
    ("session_london_mom", lambda d: hyp_session_momentum(d, session_hour=8, hold_bars=4)),
    ("session_ny_mom", lambda d: hyp_session_momentum(d, session_hour=13, hold_bars=4)),
    ("atr_squeeze_breakout", lambda d: hyp_atr_squeeze_breakout(d)),
    ("structure_bos_k3", lambda d: hyp_structure_bos(d, k=3)),
    ("ft_cross_3_confirm1", lambda d: hyp_follow_through_cross(d, consecutive=3, confirm_bars=1)),
    ("ft_cross_body_3_0.5", lambda d: hyp_cross_body_size(d, consecutive=3, body_atr_mult=0.5)),
    ("ft_delayed_rsi_9_25_75", lambda d: hyp_delayed_rsi(d, rsi_period=9, lo=25.0, hi=75.0, delay_bars=1)),
    ("ft_ema_mom_21_2.0", lambda d: hyp_ema_extension_momentum(d, ema_period=21, ext_atr=2.0)),
    # MA compression / expansion (user's key insight)
    ("ma_compression_filter", lambda d: hyp_ma_compression_filter(d)),
    ("ma_expansion_breakout", lambda d: hyp_ma_expansion_breakout(d)),
    ("compression_rubberband", lambda d: hyp_compression_rubberband(d)),
    ("ma_slope_convergence", lambda d: hyp_ma_slope_convergence(d)),
]

def backtest_with_trades(df: pd.DataFrame, pos: pd.Series, symbol: str,
                         comm_pts: float = 7.0, slip_pts: float = 1.0,
                         floor_pts: float = 1.0) -> tuple:
    """Backtest and return per-trade records with entry/exit bars and P&L."""
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
    trades = []
    in_position = False
    side = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_atr = 0.0
    entry_bar = -1
    entry_cost_dollars = 0.0

    for i in range(1, n):
        sig = pos.iloc[i - 1]
        c = close.iloc[i]

        if in_position:
            hit = False
            if side > 0:
                if c <= stop_price:
                    pnl = (stop_price - entry_price) / tick_size * 0.01
                    eq_pnl = pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append({
                        "entry_bar": entry_bar, "exit_bar": i, "side": 1,
                        "entry_price": entry_price, "exit_price": stop_price,
                        "pnl": eq_pnl, "exit_reason": "stop",
                        "entry_atr": entry_atr,
                    })
                    in_position = False
                    side = 0
                    hit = True
                elif c >= tp_price and not hit:
                    pnl = (tp_price - entry_price) / tick_size * 0.01
                    eq_pnl = pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append({
                        "entry_bar": entry_bar, "exit_bar": i, "side": 1,
                        "entry_price": entry_price, "exit_price": tp_price,
                        "pnl": eq_pnl, "exit_reason": "tp",
                        "entry_atr": entry_atr,
                    })
                    in_position = False
                    side = 0
                    hit = True
                elif sig <= 0 and not hit:
                    pnl = (c - entry_price) / tick_size * 0.01
                    eq_pnl = pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append({
                        "entry_bar": entry_bar, "exit_bar": i, "side": 1,
                        "entry_price": entry_price, "exit_price": c,
                        "pnl": eq_pnl, "exit_reason": "signal",
                        "entry_atr": entry_atr,
                    })
                    in_position = False
                    side = 0
                    hit = True
            else:
                if c >= stop_price:
                    pnl = (entry_price - stop_price) / tick_size * 0.01
                    eq_pnl = pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append({
                        "entry_bar": entry_bar, "exit_bar": i, "side": -1,
                        "entry_price": entry_price, "exit_price": stop_price,
                        "pnl": eq_pnl, "exit_reason": "stop",
                        "entry_atr": entry_atr,
                    })
                    in_position = False
                    side = 0
                    hit = True
                elif c <= tp_price and not hit:
                    pnl = (entry_price - tp_price) / tick_size * 0.01
                    eq_pnl = pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append({
                        "entry_bar": entry_bar, "exit_bar": i, "side": -1,
                        "entry_price": entry_price, "exit_price": tp_price,
                        "pnl": eq_pnl, "exit_reason": "tp",
                        "entry_atr": entry_atr,
                    })
                    in_position = False
                    side = 0
                    hit = True
                elif sig >= 0 and not hit:
                    pnl = (entry_price - c) / tick_size * 0.01
                    eq_pnl = pnl - entry_cost_dollars - cost_side[i] / tick_size * 0.01
                    trades.append({
                        "entry_bar": entry_bar, "exit_bar": i, "side": -1,
                        "entry_price": entry_price, "exit_price": c,
                        "pnl": eq_pnl, "exit_reason": "signal",
                        "entry_atr": entry_atr,
                    })
                    in_position = False
                    side = 0
                    hit = True

        if not in_position and sig != 0:
            entry_price = c
            entry_atr = atr.iloc[i] if atr.iloc[i] > 0 else 0.001
            stop_dist = entry_atr * 1.5
            if sig > 0:
                stop_price = entry_price - stop_dist
                tp_price = entry_price + stop_dist * 2.0
            else:
                stop_price = entry_price + stop_dist
                tp_price = entry_price - stop_dist * 2.0
            in_position = True
            side = sig
            entry_bar = i
            entry_cost_dollars = cost_side[i] / tick_size * 0.01
            equity -= entry_cost_dollars

    if in_position:
        c = close.iloc[-1]
        if side > 0:
            pnl = (c - entry_price) / tick_size * 0.01
        else:
            pnl = (entry_price - c) / tick_size * 0.01
        exit_cost = cost_side[-1] / tick_size * 0.01
        eq_pnl = pnl - entry_cost_dollars - exit_cost
        trades.append({
            "entry_bar": entry_bar, "exit_bar": n - 1, "side": side,
            "entry_price": entry_price, "exit_price": c,
            "pnl": eq_pnl, "exit_reason": "end",
            "entry_atr": entry_atr,
        })

    return trades


def tag_regimes(df: pd.DataFrame, trades: list, symbol: str) -> list[dict]:
    """Tag each trade with regime features at entry bar."""
    n = len(df)
    atr = wilder_atr(df, 14)
    rsi = wilder_rsi(df["close"], 9)
    e21 = ema(df["close"], 21)
    adx_s = adx(df, 14)
    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    times = df["time"]
    spread = df["spread"].to_numpy(float)

    # ATR percentile (200-bar rolling)
    atr_pct = atr.rolling(200, min_periods=50).rank(pct=True)

    # Daily range position (where close sits in today's range)
    # Approximate with 24-hour rolling high/low for H1
    roll_high = high.rolling(24, min_periods=1).max()
    roll_low = low.rolling(24, min_periods=1).min()
    range_pos = (close - roll_low) / (roll_high - roll_low).replace(0, np.nan)

    # ATR ratio (current vs 20-bar avg)
    atr_ratio = atr / atr.rolling(20, min_periods=5).mean()

    tagged = []
    for t in trades:
        eb = t["entry_bar"]
        if eb < 0 or eb >= n:
            continue
        hour = times.iloc[eb].hour
        dow = times.iloc[eb].dayofweek
        if hour < 8:
            session = "asia"
        elif hour < 13:
            session = "london"
        elif hour < 21:
            session = "ny"
        else:
            session = "late"

        # Volatility regime
        ap = atr_pct.iloc[eb]
        if pd.isna(ap):
            vol_reg = "unknown"
        elif ap < 0.33:
            vol_reg = "low_vol"
        elif ap < 0.66:
            vol_reg = "med_vol"
        else:
            vol_reg = "high_vol"

        # Trend strength
        adx_val = adx_s.iloc[eb]
        if pd.isna(adx_val):
            trend_reg = "unknown"
        elif adx_val < 20:
            trend_reg = "range"
        elif adx_val < 30:
            trend_reg = "weak_trend"
        else:
            trend_reg = "strong_trend"

        # Spread regime
        sp = spread[eb]
        sp_pct = pd.Series(spread).rank(pct=True).iloc[eb]
        if pd.isna(sp_pct):
            spread_reg = "unknown"
        elif sp_pct < 0.33:
            spread_reg = "tight"
        elif sp_pct < 0.66:
            spread_reg = "normal"
        else:
            spread_reg = "wide"

        # Range position
        rp = range_pos.iloc[eb]
        if pd.isna(rp):
            range_reg = "unknown"
        elif rp < 0.25:
            range_reg = "bottom_quartile"
        elif rp < 0.75:
            range_reg = "middle"
        else:
            range_reg = "top_quartile"

        # ATR ratio
        ar = atr_ratio.iloc[eb]
        if pd.isna(ar):
            atr_ratio_reg = "unknown"
        elif ar < 0.7:
            atr_ratio_reg = "squeeze"
        elif ar < 1.3:
            atr_ratio_reg = "normal"
        else:
            atr_ratio_reg = "expansion"

        # RSI at entry
        rsv = rsi.iloc[eb]
        if pd.isna(rsv):
            rsi_reg = "unknown"
        elif rsv < 30:
            rsi_reg = "oversold"
        elif rsv > 70:
            rsi_reg = "overbought"
        else:
            rsi_reg = "neutral"

        tagged.append({
            **t,
            "symbol": symbol,
            "entry_time": str(times.iloc[eb]),
            "hour": int(hour),
            "dow": int(dow),
            "session": session,
            "vol_regime": vol_reg,
            "trend_regime": trend_reg,
            "spread_regime": spread_reg,
            "range_regime": range_reg,
            "atr_ratio_regime": atr_ratio_reg,
            "rsi_regime": rsi_reg,
            "atr_entry": float(atr.iloc[eb]) if pd.notna(atr.iloc[eb]) else 0.0,
            "rsi_entry": float(rsv) if pd.notna(rsv) else 0.0,
        })
    return tagged


def regime_metrics(trades: list, key: str, val) -> dict:
    """Compute metrics for trades matching trades[i][key] == val."""
    sub = [t for t in trades if t.get(key) == val]
    if not sub:
        return {"n": 0, "pf": 0.0, "winrate": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}
    wins = [t for t in sub if t["pnl"] > 0]
    losses = [t for t in sub if t["pnl"] < 0]
    pf = sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else float("inf")
    return {
        "n": len(sub),
        "pf": round(pf, 2),
        "winrate": round(len(wins) / len(sub) * 100, 1),
        "avg_pnl": round(sum(t["pnl"] for t in sub) / len(sub), 2),
        "total_pnl": round(sum(t["pnl"] for t in sub), 2),
    }


def evaluate(name: str, fn, symbol: str, df: pd.DataFrame,
             comm_pts: float, slip_pts: float, floor_pts: float) -> list[dict]:
    pos = fn(df)
    trades = backtest_with_trades(df, pos, symbol, comm_pts, slip_pts, floor_pts)
    if not trades:
        return []

    tagged = tag_regimes(df, trades, symbol)

    rows = []
    regime_keys = ["vol_regime", "trend_regime", "session", "spread_regime",
                   "range_regime", "atr_ratio_regime", "rsi_regime", "hour", "dow"]
    for key in regime_keys:
        vals = sorted(set(t.get(key) for t in tagged if t.get(key) is not None))
        for val in vals:
            m = regime_metrics(tagged, key, val)
            rows.append({
                "hypothesis": f"{symbol}_{name}",
                "symbol": symbol,
                "regime_dim": key,
                "regime_val": str(val),
                **m,
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Regime analysis: good vs bad move factors")
    ap.add_argument("--symbol", choices=PAIRS + ["all"], default="all")
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    args = ap.parse_args()

    symbols = PAIRS if args.symbol == "all" else [args.symbol]

    all_rows = []
    for sym in symbols:
        print(f"\n=== {sym} ===")
        df = load_csv(sym)
        print(f"  bars: {len(df)}  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
        for name, fn in HYPOTHESES:
            rows = evaluate(name, fn, sym, df, args.comm_pts, args.slip_pts, args.floor_pts)
            all_rows.extend(rows)

    df_out = pd.DataFrame(all_rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_csv(OUT, index=False)

    # Print summary: best/worst regime per dimension
    print("\n=== REGIME SUMMARY (best/worst PF per dimension) ===")
    for dim in ["vol_regime", "trend_regime", "session", "spread_regime",
                "range_regime", "atr_ratio_regime", "rsi_regime"]:
        sub = df_out[(df_out["regime_dim"] == dim) & (df_out["n"] >= 20)]
        if sub.empty:
            continue
        best = sub.loc[sub["pf"].idxmax()]
        worst = sub.loc[sub["pf"].idxmin()]
        print(f"\n{dim}:")
        print(f"  BEST:  {best['regime_val']}  PF={best['pf']}  WR={best['winrate']}%  n={best['n']}  avg_pnl={best['avg_pnl']}")
        print(f"  WORST: {worst['regime_val']}  PF={worst['pf']}  WR={worst['winrate']}%  n={worst['n']}  avg_pnl={worst['avg_pnl']}")

    print(f"\nWrote {OUT}")
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "n_rows": len(df_out),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
