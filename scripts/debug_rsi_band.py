#!/usr/bin/env python3
"""Quick debug of RSI band entry logic on EURUSD H1."""

import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "data" / "mt5"

df = pd.read_csv(DATA_DIR / "EURUSD_H1.csv")
df.columns = [c.lower() for c in df.columns]
df["time"] = pd.to_datetime(df["time"])
df = df.sort_values("time").reset_index(drop=True)

df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

def atr(data, period=14):
    high = data["high"]; low = data["low"]; close = data["close"]
    prev = close.shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev), abs(low - prev)))
    return tr.ewm(alpha=1/period, adjust=False).mean()

df["atr14"] = atr(df, 14)

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = ag / al
    return 100 - (100 / (1 + rs))

df["rsi9"] = rsi(df["close"], 9)
df["ema_slope"] = (df["ema21"] - df["ema21"].shift(3)) / df["atr14"]

# Count entry signals
long_signals = 0
short_signals = 0
for i in range(3, len(df)):
    row = df.iloc[i]
    prev = df.iloc[i-1]
    rsi_val = row["rsi9"]; rsi_prev = prev["rsi9"]
    ema_slope = row["ema_slope"]
    
    if ema_slope > 0.10:  # uptrend
        if rsi_prev > (25.0 + 3.0) and rsi_val <= (25.0 - 3.0):
            long_signals += 1
    elif ema_slope < -0.10:  # downtrend
        if rsi_prev < (75.0 - 3.0) and rsi_val >= (75.0 + 3.0):
            short_signals += 1

print(f"Long signals (RSI<22, uptrend): {long_signals}")
print(f"Short signals (RSI>78, downtrend): {short_signals}")
print(f"Total signals: {long_signals + short_signals}")
print(f"Data range: {df['time'].iloc[0]} to {df['time'].iloc[-1]}")
print(f"Total bars: {len(df)}")
print(f"\nRSI stats: min={df['rsi9'].min():.1f}, max={df['rsi9'].max():.1f}, mean={df['rsi9'].mean():.1f}")
print(f"EMA slope stats: min={df['ema_slope'].min():.4f}, max={df['ema_slope'].max():.4f}")
print(f"Bars with slope > 0.10: {(df['ema_slope'] > 0.10).sum()}")
print(f"Bars with slope < -0.10: {(df['ema_slope'] < -0.10).sum()}")

# Check spread gate
print(f"\nSpread stats: min={df['spread'].min()}, max={df['spread'].max()}, median={df['spread'].median()}")
spread_sorted = sorted(df["spread"].dropna())
p90_idx = int(len(spread_sorted) * 0.9)
print(f"Spread p90: {spread_sorted[p90_idx]}")
