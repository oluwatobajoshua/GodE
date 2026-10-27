#!/usr/bin/env python3
"""Shared data and feature preparation for the Sovereign research pipeline."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "data"
POINT = 0.00001

TIMEFRAME_RULES = {
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
}


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()


def adx_frame(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    tr = true_range(df)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame(
        {
            "plus_di": plus_di,
            "minus_di": minus_di,
            "adx": dx.ewm(alpha=1.0 / period, adjust=False).mean(),
            "atr": atr_s,
        },
        index=df.index,
    )


def rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample bid OHLC bars. Spread is averaged; volume is summed."""
    indexed = df.set_index("time").sort_index()
    out = indexed.resample(rule, label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "tick_volume": "sum",
            "spread": "mean",
            "real_volume": "sum",
        }
    )
    out = out.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return out


def add_htf_features(df: pd.DataFrame) -> pd.DataFrame:
    """Merge completed H1 and D1 context onto the execution timeframe."""
    h1 = pd.read_csv(DATA_DIR / "EURUSD_H1.csv", parse_dates=["time"]).sort_values("time")
    d1 = pd.read_csv(DATA_DIR / "EURUSD_D1.csv", parse_dates=["time"]).sort_values("time")
    # Normalise tz-aware HistData timestamps to naive UTC so merge_asof works
    # with both the old naive Deriv CSVs and the new independent CSVs.
    h1["time"] = pd.to_datetime(h1["time"], utc=True).dt.tz_localize(None)
    d1["time"] = pd.to_datetime(d1["time"], utc=True).dt.tz_localize(None)

    h1["h1_sma20"] = h1["close"].rolling(20).mean()
    h1["h1_sma50"] = h1["close"].rolling(50).mean()
    h1["h1_sma200"] = h1["close"].rolling(200).mean()
    h1["h1_ema20"] = h1["close"].ewm(span=20, adjust=False).mean()
    h1_adx = adx_frame(h1, 14)
    h1["h1_adx"] = h1_adx["adx"]
    h1["h1_atr"] = h1_adx["atr"]
    h1["h1_plus_di"] = h1_adx["plus_di"]
    h1["h1_minus_di"] = h1_adx["minus_di"]
    h1["available"] = h1["time"] + pd.Timedelta(hours=1)

    d1["d1_sma50"] = d1["close"].rolling(50).mean()
    d1["d1_sma200"] = d1["close"].rolling(200).mean()
    d1["available"] = d1["time"] + pd.Timedelta(days=1)

    h1_cols = [
        "available",
        "close",
        "h1_sma20",
        "h1_sma50",
        "h1_sma200",
        "h1_ema20",
        "h1_adx",
        "h1_atr",
        "h1_plus_di",
        "h1_minus_di",
    ]
    h1_features = h1[h1_cols].rename(columns={"close": "h1_close"})
    d1_features = d1[["available", "close", "d1_sma50", "d1_sma200"]].rename(
        columns={"close": "d1_close"}
    )

    merged = pd.merge_asof(
        df.sort_values("time"),
        h1_features,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    merged = pd.merge_asof(
        merged,
        d1_features,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    return merged


def load_data(
    timeframe: str = "M5",
    symbol: str = "EURUSD",
    lookback: int = 50,
) -> pd.DataFrame:
    """Load execution bars, add features, and merge completed HTF context."""
    if timeframe not in TIMEFRAME_RULES:
        raise ValueError(f"Unsupported timeframe: {timeframe}")

    raw = pd.read_csv(DATA_DIR / f"{symbol}_M5.csv", parse_dates=["time"])
    raw["time"] = pd.to_datetime(raw["time"], utc=True).dt.tz_localize(None)
    raw = raw.sort_values("time").reset_index(drop=True)
    if timeframe != "M5":
        raw = resample_ohlc(raw, TIMEFRAME_RULES[timeframe])

    df = raw.copy()
    df = add_execution_features(df, lookback)
    df = add_htf_features(df)
    return df.reset_index(drop=True)


def add_execution_features(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
    df = df.copy()
    df["atr"] = atr(df, 14)
    df["atr50"] = df["atr"].rolling(50).mean()
    df["atr200"] = df["atr"].rolling(200).mean()
    df["atr_ratio"] = df["atr"] / df["atr50"].replace(0, np.nan)
    df["atr_pct"] = df["atr"].rolling(200).rank(pct=True)
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema100"] = df["close"].ewm(span=100, adjust=False).mean()
    df["rsi2"] = rsi(df["close"], 2)
    df["rsi14"] = rsi(df["close"], 14)
    df["range_high"] = df["high"].rolling(lookback).max().shift(1)
    df["range_low"] = df["low"].rolling(lookback).min().shift(1)
    df["range_width"] = df["range_high"] - df["range_low"]
    df["range_pos"] = (df["close"] - df["range_low"]) / df["range_width"].replace(0, np.nan)
    df["body"] = (df["close"] - df["open"]).abs()
    df["body_atr"] = df["body"] / df["atr"].replace(0, np.nan)
    df["ret1_atr"] = (df["close"] - df["close"].shift(1)) / df["atr"].replace(0, np.nan)
    df["mom6_atr"] = (df["close"] - df["close"].shift(6)) / df["atr"].replace(0, np.nan)
    df["mom12_atr"] = (df["close"] - df["close"].shift(12)) / df["atr"].replace(0, np.nan)
    df["swing_low5"] = df["low"].rolling(5).min().shift(1)
    df["swing_high5"] = df["high"].rolling(5).max().shift(1)
    df["date"] = df["time"].dt.date
    df["hour"] = df["time"].dt.hour
    df["weekday"] = df["time"].dt.weekday
    return df


def add_d1_features(df: pd.DataFrame) -> pd.DataFrame:
    d1 = pd.read_csv(DATA_DIR / "EURUSD_D1.csv", parse_dates=["time"]).sort_values("time")
    d1["d1_sma50"] = d1["close"].rolling(50).mean()
    d1["d1_sma200"] = d1["close"].rolling(200).mean()
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    d1_features = d1[["available", "close", "d1_sma50", "d1_sma200"]].rename(
        columns={"close": "d1_close"}
    )
    return pd.merge_asof(
        df.sort_values("time"),
        d1_features,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])


def load_native_htf(timeframe: str = "H1", symbol: str = "EURUSD", lookback: int = 50) -> pd.DataFrame:
    """Load native H1 history and use it as both execution and regime timeframe."""
    if timeframe != "H1":
        raise ValueError("Native HTF loader currently supports H1 only")
    df = pd.read_csv(DATA_DIR / f"{symbol}_H1.csv", parse_dates=["time"]).sort_values("time")
    df = df.reset_index(drop=True)
    df = add_execution_features(df, lookback)
    df = add_d1_features(df)

    h1_adx = adx_frame(df, 14)
    df["h1_close"] = df["close"]
    df["h1_sma20"] = df["close"].rolling(20).mean()
    df["h1_sma50"] = df["close"].rolling(50).mean()
    df["h1_sma200"] = df["close"].rolling(200).mean()
    df["h1_ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["h1_adx"] = h1_adx["adx"]
    df["h1_atr"] = h1_adx["atr"]
    df["h1_plus_di"] = h1_adx["plus_di"]
    df["h1_minus_di"] = h1_adx["minus_di"]
    return df.reset_index(drop=True)

