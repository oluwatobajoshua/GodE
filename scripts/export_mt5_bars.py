#!/usr/bin/env python3
"""Export broker-native OHLCV+spread from MT5 into research/data/mt5/ (data plane).

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\export_mt5_bars.py --symbols GBPUSD EURUSD
    .\\.venv\\Scripts\\python.exe scripts\\export_mt5_bars.py --from-date 2018-01-01
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "research" / "data" / "mt5"
CONFIG = REPO / "configs" / "mt5_data_plane.json"
MANIFEST = OUT_DIR / "mt5_export_manifest.json"

TIMEFRAMES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def _load_config_symbols() -> list[str]:
    if not CONFIG.exists():
        return ["GBPUSD", "EURUSD", "USDJPY"]
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        return [s.upper() for s in cfg.get("symbols", [])]
    except json.JSONDecodeError:
        return ["GBPUSD", "EURUSD", "USDJPY"]


def export_symbol(symbol: str, timeframe: str, start: datetime, end: datetime, terminal: str) -> dict:
    import MetaTrader5 as mt5

    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map[timeframe]
    if not mt5.symbol_select(symbol, True):
        return {"symbol": symbol, "ok": False, "error": f"symbol_select: {mt5.last_error()}"}

    frames: list[pd.DataFrame] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + pd.Timedelta(days=30), end)
        rates = mt5.copy_rates_range(symbol, tf, cursor, window_end)
        if rates is not None and len(rates):
            frames.append(pd.DataFrame(rates))
        cursor = window_end

    if not frames:
        return {"symbol": symbol, "ok": False, "error": f"no_rates: {mt5.last_error()}"}

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="time").sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    if "tick_volume" not in df.columns and "real_volume" in df.columns:
        df["tick_volume"] = df["real_volume"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{symbol}_{timeframe}.csv"
    export_cols = [c for c in ("time", "open", "high", "low", "close", "tick_volume", "spread", "volume") if c in df.columns]
    df[export_cols].to_csv(out, index=False)

    span_days = int((df["time"].iloc[-1] - df["time"].iloc[0]).days)
    shallow = span_days < 400
    if shallow:
        print(f"  [WARN] {symbol} broker history only ~{span_days} days; use HistData M5 for long scalp proof")
    return {
        "symbol": symbol,
        "ok": True,
        "path": str(out),
        "bars": len(df),
        "from": str(df["time"].iloc[0]),
        "to": str(df["time"].iloc[-1]),
        "span_days": span_days,
        "shallow_history": shallow,
        "has_spread": "spread" in df.columns,
        "terminal": terminal,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MT5 rates to research/data/mt5/")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--timeframe", default="H1", choices=sorted(TIMEFRAMES))
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument(
        "--terminal",
        default=r"C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    )
    args = parser.parse_args()

    symbols = args.symbols or _load_config_symbols()
    cfg = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    exp = cfg.get("export") or {}
    from_s = args.from_date or exp.get("default_from", "2018-01-01")
    to_s = args.to_date or exp.get("default_to", "2026-12-31")
    start = datetime.fromisoformat(from_s).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(to_s).replace(tzinfo=timezone.utc)

    import MetaTrader5 as mt5

    if not mt5.initialize(args.terminal):
        print(f"[FAIL] mt5.initialize: {mt5.last_error()}")
        return 1

    manifest: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "timeframe": args.timeframe,
        "from": from_s,
        "to": to_s,
        "symbols": {},
    }
    ok_n = 0
    for sym in symbols:
        sym = sym.upper()
        print(f"[EXPORT] {sym} {args.timeframe} {from_s} -> {to_s}")
        row = export_symbol(sym, args.timeframe, start, end, args.terminal)
        manifest["symbols"][sym] = row
        if row.get("ok"):
            ok_n += 1
            print(f"  [OK] {row['bars']:,} bars -> {row['path']}")
        else:
            print(f"  [FAIL] {row.get('error')}")

    mt5.shutdown()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest["ok_count"] = ok_n
    manifest["canonical_dir"] = str(OUT_DIR)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[MANIFEST] {MANIFEST} ({ok_n}/{len(symbols)} ok)")
    return 0 if ok_n > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
