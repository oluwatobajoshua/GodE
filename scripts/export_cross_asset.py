#!/usr/bin/env python3
"""Export cross-asset history used for macro / sentiment research.

One MT5 session exports every symbol, which is faster and avoids repeated
terminal handshakes.  The resulting CSVs are inputs to
``research/cross_asset_features.py``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research" / "data"

DEFAULT_SYMBOLS = [
    "EURUSD",
    "DXYUSD",
    "VIXUSD",
    "XAUUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
]

TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "D1": mt5.TIMEFRAME_D1,
}


def export_symbol(symbol: str, timeframe: str, start: datetime, end: datetime) -> int:
    if not mt5.symbol_select(symbol, True):
        print(f"[FAIL] symbol_select({symbol}): {mt5.last_error()}")
        return 0

    tf = TIMEFRAMES[timeframe]
    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + pd.Timedelta(days=30), end)
        rates = mt5.copy_rates_range(symbol, tf, cursor, window_end)
        if rates is not None and len(rates):
            frames.append(pd.DataFrame(rates))
        cursor = window_end

    if not frames:
        print(f"[FAIL] no rates for {symbol}: {mt5.last_error()}")
        return 0

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    out = OUT_DIR / f"{symbol}_{timeframe}.csv"
    df.to_csv(out, index=False)
    print(f"[OK] {symbol:8s} {timeframe}: {len(df):,} bars  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    return len(df)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframe", default="M5", choices=sorted(TIMEFRAMES))
    parser.add_argument("--from-date", default="2025-12-01")
    parser.add_argument("--to-date", default="2026-11-01")
    parser.add_argument("--terminal", default=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe")
    args = parser.parse_args()

    if not mt5.initialize(args.terminal):
        print(f"[FAIL] mt5.initialize: {mt5.last_error()}")
        return 1

    start = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
    try:
        total = 0
        for symbol in args.symbols:
            total += export_symbol(symbol, args.timeframe, start, end)
        print(f"[DONE] {total:,} bars across {len(args.symbols)} symbols")
        return 0 if total else 1
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
