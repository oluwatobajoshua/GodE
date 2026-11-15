#!/usr/bin/env python3
"""Page BTCUSD H1 from the logged-in MT5 terminal (90-day windows)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "research" / "data" / "BTCUSD_H1.csv"
TERMINAL = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"


def main() -> int:
    if not mt5.initialize(TERMINAL):
        print(f"[FAIL] initialize: {mt5.last_error()}")
        return 1
    sym = "BTCUSD"
    if not mt5.symbol_select(sym, True):
        print(f"[FAIL] symbol_select: {mt5.last_error()}")
        mt5.shutdown()
        return 1
    frames: list[pd.DataFrame] = []
    cursor = datetime(2015, 1, 1, tzinfo=timezone.utc)
    end = datetime.now(timezone.utc)
    while cursor < end:
        wend = min(cursor + timedelta(days=90), end)
        chunk = mt5.copy_rates_range(sym, mt5.TIMEFRAME_H1, cursor, wend)
        if chunk is not None and len(chunk):
            frames.append(pd.DataFrame(chunk))
        cursor = wend
    mt5.shutdown()
    if not frames:
        print("[FAIL] no bars")
        return 1
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset="time").sort_values("time")
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"[OK] {len(df):,} bars {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
    print(f"     {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
