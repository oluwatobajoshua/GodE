#!/usr/bin/env python3
"""Export MT5 history bars to CSV for offline research.

The terminal must be installed and logged in. This uses the official
MetaTrader5 Python package, so it reads the same real-tick history the
Strategy Tester uses.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research" / "data"

TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MT5 bars to CSV")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M5", choices=sorted(TIMEFRAMES))
    parser.add_argument("--from-date", default="2024-01-01")
    parser.add_argument("--to-date", default="2026-10-26")
    parser.add_argument("--terminal", default=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if not mt5.initialize(args.terminal):
        print(f"[FAIL] mt5.initialize: {mt5.last_error()}")
        return 1

    try:
        if not mt5.symbol_select(args.symbol, True):
            print(f"[FAIL] symbol_select({args.symbol}): {mt5.last_error()}")
            return 1

        start = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
        tf = TIMEFRAMES[args.timeframe]

        # MT5 rejects very large M5 range requests. Page through 30-day
        # windows and merge the results.
        frames: list[pd.DataFrame] = []
        cursor = start
        while cursor < end:
            window_end = min(cursor + pd.Timedelta(days=30), end)
            rates = mt5.copy_rates_range(args.symbol, tf, cursor, window_end)
            if rates is not None and len(rates):
                frames.append(pd.DataFrame(rates))
            cursor = window_end

        if not frames:
            print(f"[FAIL] no rates returned: {mt5.last_error()}")
            return 1

        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        out = Path(args.out) if args.out else OUT_DIR / f"{args.symbol}_{args.timeframe}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"[OK] {args.symbol} {args.timeframe}: {len(df):,} bars")
        print(f"     {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
        print(f"     {out}")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
