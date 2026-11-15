#!/usr/bin/env python3
r"""MT5 Data Window equivalent for research.

Pick any bar (by index or time) on any symbol/timeframe and dump every
data point the EA would see at that moment:

  * OHLCV + spread
  * Indicators: RSI9, EMA21, EMA50, ATR14, EMA21 slope (3-bar/ATR)
  * Regime: volatility, trend strength, session, spread tier, ATR ratio
  * Fractals: swing high/low at k=3,5
  * Every hypothesis: would it have fired? Long/short/flat? Entry/exit?
  * If in a position at that bar: entry price, stop, TP, unrealized PnL

Usage:
    .\.venv\Scripts\python.exe research/data_window.py --symbol EURUSD --tf H1 --bar 10000
    .\.venv\Scripts\python.exe research/data_window.py --symbol EURUSD --tf H1 --time 2024-06-15T10:00
    .\.venv\Scripts\python.exe research/data_window.py --symbol EURUSD --tf H1 --bar 10000 --context 5
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "mt5"

PAIRS = ["EURUSD", "GBPUSD", "USDJPY"]


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
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


def find_bar_index(df: pd.DataFrame, bar: int | None, time: str | None) -> int:
    if bar is not None:
        if bar < 0 or bar >= len(df):
            raise ValueError(f"Bar index {bar} out of range [0, {len(df)-1}]")
        return bar
    if time is not None:
        t = pd.to_datetime(time, utc=True, errors="coerce").tz_convert(None)
        if pd.isna(t):
            raise ValueError(f"Cannot parse time: {time}")
        # find nearest bar
        diffs = (df["time"] - t).abs()
        idx = diffs.idxmin()
        return int(idx)
    raise ValueError("Must specify --bar or --time")


def fmt(x, dec=5, na="—"):
    if pd.isna(x):
        return na
    return f"{x:.{dec}f}"


def fmt_pct(x, dec=1, na="—"):
    if pd.isna(x):
        return na
    return f"{x*100:.{dec}f}%"


def data_window(symbol: str, tf: str, bar_idx: int, context: int = 5,
                comm_pts: float = 7.0, slip_pts: float = 1.0,
                floor_pts: float = 1.0) -> None:
    df = load_csv(symbol, tf)
    n = len(df)

    # Compute all indicator columns
    df = df.copy()
    df["ema8"] = ema(df["close"], 8)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["atr14"] = wilder_atr(df, 14)
    df["rsi9"] = wilder_rsi(df["close"], 9)
    df["adx14"] = adx(df, 14)

    # EMA21 slope over 3 bars / ATR
    df["ema_slope3"] = (df["ema21"] - df["ema21"].shift(3)) / df["atr14"]

    # Fractals k=3, k=5
    df["fh3"] = fractal_high(df["high"], 3)
    df["fl3"] = fractal_low(df["low"], 3)
    df["fh5"] = fractal_high(df["high"], 5)
    df["fl5"] = fractal_low(df["low"], 5)

    # ATR percentile (200-bar)
    df["atr_pct"] = df["atr14"].rolling(200, min_periods=50).rank(pct=True)

    # ATR ratio (current vs 20-bar avg)
    df["atr_ratio"] = df["atr14"] / df["atr14"].rolling(20, min_periods=5).mean()

    # Daily-ish range position (24H rolling for H1)
    roll_high = df["high"].rolling(24, min_periods=1).max()
    roll_low = df["low"].rolling(24, min_periods=1).min()
    df["range_pos"] = (df["close"] - roll_low) / (roll_high - roll_low).replace(0, np.nan)

    # Spread percentile
    df["spread_pct"] = df["spread"].rank(pct=True)

    row = df.iloc[bar_idx]
    t = row["time"]

    print(f"\n{'='*70}")
    print(f"DATA WINDOW — {symbol} {tf}  |  Bar {bar_idx} / {n-1}")
    print(f"{'='*70}")
    print(f"Time:        {t}")
    print(f"Hour:        {t.hour:02d}:00  |  Day of week: {t.strftime('%A')} (dow={t.dayofweek})")
    print()

    # --- OHLCV ---
    print("--- PRICE ---")
    print(f"  Open:  {fmt(row['open'])}")
    print(f"  High:  {fmt(row['high'])}")
    print(f"  Low:   {fmt(row['low'])}")
    print(f"  Close: {fmt(row['close'])}")
    print(f"  Body:  {fmt(abs(row['close']-row['open']), 5)}  ({'bullish' if row['close']>row['open'] else 'bearish'})")
    print(f"  Range: {fmt(row['high']-row['low'], 5)}")
    print(f"  Vol:   {int(row['tick_volume'])}  |  Spread: {int(row['spread'])} pts")
    print()

    # --- Indicators ---
    print("--- INDICATORS ---")
    print(f"  RSI9:      {fmt(row['rsi9'], 2)}")
    print(f"  EMA8:      {fmt(row['ema8'])}")
    print(f"  EMA21:     {fmt(row['ema21'])}")
    print(f"  EMA50:     {fmt(row['ema50'])}")
    print(f"  ATR14:     {fmt(row['atr14'], 5)}")
    print(f"  EMA21 slope(3/ATR): {fmt(row['ema_slope3'], 4)}")
    print(f"  ADX14:     {fmt(row['adx14'], 2)}")
    print()

    # --- Regimes ---
    ap = row["atr_pct"]
    if pd.isna(ap):
        vol_reg = "unknown"
    elif ap < 0.33:
        vol_reg = "low_vol (chop)"
    elif ap < 0.66:
        vol_reg = "med_vol"
    else:
        vol_reg = "high_vol"

    adv = row["adx14"]
    if pd.isna(adv):
        trend_reg = "unknown"
    elif adv < 20:
        trend_reg = "range"
    elif adv < 30:
        trend_reg = "weak_trend"
    else:
        trend_reg = "strong_trend"

    h = t.hour
    if h < 8:
        session = "asia"
    elif h < 13:
        session = "london"
    elif h < 21:
        session = "ny"
    else:
        session = "late"

    sp = row["spread_pct"]
    if pd.isna(sp):
        spread_reg = "unknown"
    elif sp < 0.33:
        spread_reg = "tight"
    elif sp < 0.66:
        spread_reg = "normal"
    else:
        spread_reg = "wide"

    ar = row["atr_ratio"]
    if pd.isna(ar):
        atr_ratio_reg = "unknown"
    elif ar < 0.7:
        atr_ratio_reg = "squeeze"
    elif ar < 1.3:
        atr_ratio_reg = "normal"
    else:
        atr_ratio_reg = "expansion"

    rp = row["range_pos"]
    if pd.isna(rp):
        range_reg = "unknown"
    elif rp < 0.25:
        range_reg = "bottom_quartile"
    elif rp < 0.75:
        range_reg = "middle"
    else:
        range_reg = "top_quartile"

    print("--- REGIMES ---")
    print(f"  Volatility:    {vol_reg}   (ATR pctile: {fmt_pct(ap)})")
    print(f"  Trend:         {trend_reg}   (ADX: {fmt(adv, 2)})")
    print(f"  Session:       {session}   (hour {h:02d})")
    print(f"  Spread:        {spread_reg}   (pctile: {fmt_pct(sp)})")
    print(f"  ATR ratio:     {atr_ratio_reg}   ({fmt(ar, 3)}x 20-bar avg)")
    print(f"  Range pos:     {range_reg}   ({fmt_pct(rp)})")
    print()

    # --- Fractals / Structure ---
    print("--- FRACTALS / STRUCTURE ---")
    print(f"  Swing high k=3:  {row['fh3']}   |  Swing low k=3:  {row['fl3']}")
    print(f"  Swing high k=5:  {row['fh5']}   |  Swing low k=5:  {row['fl5']}")
    print(f"  MA alignment:    EMA8={fmt(row['ema8'])}  EMA21={fmt(row['ema21'])}  EMA50={fmt(row['ema50'])}  "
          f"{'bullish' if row['ema8']>row['ema21']>row['ema50'] else 'bearish' if row['ema8']<row['ema21']<row['ema50'] else 'mixed'}")
    print()

    # --- Hypothesis signals at this bar ---
    print("--- HYPOTHESIS SIGNALS (would this bar fire?) ---")

    signals = {}

    # RSI extreme
    rsi_prev = df["rsi9"].iloc[bar_idx-1] if bar_idx > 0 else np.nan
    rsi_val = row["rsi9"]
    if pd.notna(rsi_prev) and pd.notna(rsi_val):
        if rsi_prev >= 25.0 and rsi_val < 25.0:
            signals["rsi_extreme_25"] = "LONG (RSI < 25)"
        elif rsi_prev <= 75.0 and rsi_val > 75.0:
            signals["rsi_extreme_75"] = "SHORT (RSI > 75)"
        elif rsi_prev >= 30.0 and rsi_val < 30.0:
            signals["rsi_extreme_30"] = "LONG (RSI < 30)"
        elif rsi_prev <= 70.0 and rsi_val > 70.0:
            signals["rsi_extreme_70"] = "SHORT (RSI > 70)"

    # Candle cross
    red = row["close"] < row["open"]
    green = row["close"] > row["open"]
    signals["candle_color"] = "bullish" if green else "bearish" if red else "doji"

    # MA compression
    e8v, e21v, e50v = row["ema8"], row["ema21"], row["ema50"]
    av = row["atr14"]
    if pd.notna(e8v) and pd.notna(e21v) and pd.notna(e50v) and pd.notna(av) and av > 0:
        ma_spread = max(abs(e8v-e21v), abs(e21v-e50v), abs(e8v-e50v)) / av
        bullish_ma = e8v > e21v > e50v
        bearish_ma = e8v < e21v < e50v
        if ma_spread < 0.5:
            signals["ma_compression"] = f"COMPRESSED ({fmt(ma_spread,3)}x ATR) — NO TRADE"
        elif bullish_ma and ma_spread > 1.0:
            signals["ma_expansion"] = f"LONG — bullish expansion ({fmt(ma_spread,3)}x ATR)"
        elif bearish_ma and ma_spread > 1.0:
            signals["ma_expansion"] = f"SHORT — bearish expansion ({fmt(ma_spread,3)}x ATR)"

    # Rubberband
    e21v = row["ema21"]
    if pd.notna(e21v) and pd.notna(av) and av > 0:
        ext_lo = e21v - 2.0 * av
        ext_hi = e21v + 2.0 * av
        if row["close"] < ext_lo:
            signals["rubberband"] = f"LONG — below EMA-2ATR ({fmt(row['close'])} < {fmt(ext_lo)})"
        elif row["close"] > ext_hi:
            signals["rubberband"] = f"SHORT — above EMA+2ATR ({fmt(row['close'])} > {fmt(ext_hi)})"

    # EMA extension fade
    band = 1.5 * av if pd.notna(av) else 0
    if pd.notna(e21v) and pd.notna(band) and band > 0:
        if row["close"] < e21v - band:
            signals["ema_fade_1.5"] = f"LONG — close {fmt(row['close'])} < EMA-1.5ATR {fmt(e21v-band)}"
        elif row["close"] > e21v + band:
            signals["ema_fade_1.5"] = f"SHORT — close {fmt(row['close'])} > EMA+1.5ATR {fmt(e21v+band)}"

    if signals:
        for k, v in signals.items():
            print(f"  {k:<22} {v}")
    else:
        print("  (no hypothesis would fire at this bar)")
    print()

    # --- Context: surrounding bars ---
    if context > 0:
        print(f"--- CONTEXT ({context} bars before/after) ---")
        start = max(0, bar_idx - context)
        end = min(n - 1, bar_idx + context)
        for i in range(start, end + 1):
            r = df.iloc[i]
            marker = " <<<" if i == bar_idx else ""
            bull = "+" if r["close"] > r["open"] else "-" if r["close"] < r["open"] else "."
            rsi_v = fmt(r["rsi9"], 1)
            print(f"  {i:6d}  {r['time']}  O{fmt(r['open'])} H{fmt(r['high'])} L{fmt(r['low'])} C{fmt(r['close'])} "
                  f"{bull} RSI{rsi_v} ATR{fmt(r['atr14'],5)}{marker}")
        print()

    print(f"{'='*70}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="MT5 Data Window for research")
    ap.add_argument("--symbol", choices=PAIRS, required=True)
    ap.add_argument("--tf", choices=["H1", "D1"], default="H1")
    ap.add_argument("--bar", type=int, default=None, help="Bar index (0 = oldest)")
    ap.add_argument("--time", type=str, default=None, help="Time (UTC), e.g. 2024-06-15T10:00")
    ap.add_argument("--context", type=int, default=5, help="Bars of context before/after")
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    args = ap.parse_args()

    df = load_csv(args.symbol, args.tf)
    bar_idx = find_bar_index(df, args.bar, args.time)
    data_window(args.symbol, args.tf, bar_idx, args.context,
                args.comm_pts, args.slip_pts, args.floor_pts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
