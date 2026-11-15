#!/usr/bin/env python3
"""Ingest research + FRED data → market_snapshot.json (Ecosystem Professor).

Writes to MT5 Files tree (FILE_COMMON path in tests / backtest stub).

Run:
    .\\.venv\\Scripts\\python.exe python\\market_ingest.py
    .\\.venv\\Scripts\\python.exe python\\market_ingest.py --symbols GBPUSD,EURUSD --json-stdout
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "python") not in sys.path:
    sys.path.insert(0, str(REPO / "python"))

from ecosystem_professor import build_ecosystem_snapshot  # noqa: E402

DEFAULT_FILES = Path(
    os.environ.get(
        "SOVEREIGN_MT5_FILES",
        REPO / "backtest" / "mt5_files" / "Sovereign",
    )
)
OUT = Path(os.environ.get("SOVEREIGN_MARKET_SNAPSHOT", DEFAULT_FILES / "market_snapshot.json"))


def run_once(
    symbols: list[str] | None = None,
    *,
    ttl_hours: int = 4,
    out_path: Path | None = None,
) -> dict:
    snap = build_ecosystem_snapshot(
        symbols,
        ttl_hours=ttl_hours,
        mt5_files=DEFAULT_FILES,
    )
    dest = out_path or OUT
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    results_copy = REPO / "research" / "results" / "market_snapshot.json"
    results_copy.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    print(f"[market_ingest] wrote {dest} ({len(snap.get('symbol_gates', {}))} symbols)")
    return snap


def main() -> int:
    p = argparse.ArgumentParser(description="Sovereign ecosystem professor ingest")
    p.add_argument("--symbols", default="", help="Comma-separated; default GBPUSD,EURUSD,USDJPY")
    p.add_argument("--ttl-hours", type=int, default=4)
    p.add_argument("--out", default="", help="Override output JSON path")
    p.add_argument("--json-stdout", action="store_true")
    args = p.parse_args()

    syms = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    out = Path(args.out) if args.out else None
    try:
        snap = run_once(syms, ttl_hours=args.ttl_hours, out_path=out)
    except Exception as exc:
        print(f"[market_ingest] FATAL: {exc}", file=sys.stderr)
        return 1
    if args.json_stdout:
        print(json.dumps(snap, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
