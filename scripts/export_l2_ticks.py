#!/usr/bin/env python3
"""Export MT5 tick stream (BBO + volume) for microstructure / OFI research.

Requires terminal running and symbol visible in Market Watch.
No pip — uses MetaTrader5 from requirements.txt.

Example:
    .\\.venv\\Scripts\\python.exe scripts\\export_l2_ticks.py --symbol GBPUSD --days 14
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import MetaTrader5 as mt5

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "research" / "data" / "l2"
DEFAULT_TERMINAL = r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe"


def _init(terminal: str) -> bool:
    if mt5.initialize(terminal):
        return True
    # fallback: default running terminal
    return bool(mt5.initialize())


def export_ticks(symbol: str, date_from: datetime, date_to: datetime) -> pd.DataFrame:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"symbol_select({symbol}): {mt5.last_error()}")
    parts: list[pd.DataFrame] = []
    cursor = date_from
    while cursor < date_to:
        window_end = min(cursor + timedelta(days=3), date_to)
        batch = mt5.copy_ticks_range(symbol, cursor, window_end, mt5.COPY_TICKS_ALL)
        if batch is not None and len(batch):
            parts.append(pd.DataFrame(batch))
        cursor = window_end
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = df.drop_duplicates(subset="time_msc").sort_values("time_msc")
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def export_book_snapshot(symbol: str, depth: int = 10) -> pd.DataFrame:
    """Single DOM snapshot (live). Subscribes briefly then releases."""
    if not mt5.market_book_add(symbol):
        return pd.DataFrame()
    import time

    time.sleep(0.5)
    book = mt5.market_book_get(symbol)
    mt5.market_book_release(symbol)
    if book is None:
        return pd.DataFrame()
    rows = []
    now = datetime.now(timezone.utc)
    for item in book:
        rows.append(
            {
                "time": now.isoformat(),
                "type": int(item.type),
                "price": float(item.price),
                "volume": float(item.volume),
                "volume_dbl": float(item.volume_dbl),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Export MT5 ticks for L2 / OFI pipeline")
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--days", type=int, default=14, help="History depth (keep small first)")
    p.add_argument("--terminal", default=DEFAULT_TERMINAL)
    p.add_argument("--book-snapshot", action="store_true", help="Also write live DOM snapshot")
    args = p.parse_args()

    if not _init(args.terminal):
        print(f"[FAIL] mt5.initialize: {mt5.last_error()}")
        return 1

    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, args.days))
        sym = args.symbol.upper()
        print(f"[l2] exporting {sym} ticks {start} -> {end}")
        df = export_ticks(sym, start, end)
        if df.empty:
            print(f"[FAIL] no ticks — open MT5, enable {sym}, check history subscription")
            return 1

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        tick_path = OUT_DIR / f"{sym}_l2_ticks.csv"
        cols = [c for c in ("time", "time_msc", "bid", "ask", "last", "volume", "flags", "volume_real") if c in df.columns]
        df[cols].to_csv(tick_path, index=False)
        print(f"[OK] {len(df):,} ticks -> {tick_path}")

        if args.book_snapshot:
            book = export_book_snapshot(sym)
            if not book.empty:
                book_path = OUT_DIR / f"{sym}_l2_book.csv"
                book.to_csv(book_path, index=False)
                print(f"[OK] book levels {len(book)} -> {book_path}")

        manifest = {
            "symbol": sym,
            "ticks": len(df),
            "from": str(df["time"].iloc[0]),
            "to": str(df["time"].iloc[-1]),
            "path": str(tick_path.relative_to(ROOT)),
        }
        (OUT_DIR / f"{sym}_l2_manifest.json").write_text(
            __import__("json").dumps(manifest, indent=2), encoding="utf-8"
        )
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
