#!/usr/bin/env python3
"""FXMOPOL continuous learn — read every closed trade, update timing/setup guidance.

Inputs (MT5 Files/Sovereign/):
  - trade_outcomes.csv
  - performance_buckets.csv

Output:
  - fxmopol_trade_learn.json  (blocked buckets, suggested weekday mask / hours)

The EA governor already learns per-bucket expectancy live via SovereignPerformance;
this script mirrors that offline for research and documents what the tester saw.

Run:
    .\\.venv\\Scripts\\python.exe python\\fxmopol_adapt.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULT = Path(
    os.environ.get(
        "SOVEREIGN_MT5_FILES",
        REPO / "backtest" / "mt5_files" / "Sovereign",
    )
)
OUT = DEFAULT / "fxmopol_trade_learn.json"
OUTCOMES = DEFAULT / "trade_outcomes.csv"
BUCKETS = DEFAULT / "performance_buckets.csv"

MIN_N = 5.0
BLOCK_SUM_R = -0.5


def main() -> int:
    payload: dict = {
        "source": str(DEFAULT),
        "min_n_eff": MIN_N,
        "block_sum_r_below": BLOCK_SUM_R,
        "symbols": {},
        "blocked_buckets": [],
        "promoted_buckets": [],
        "note": "",
    }

    try:
        return _run(payload)
    except Exception as exc:
        payload["note"] = f"exception: {exc}"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"[fxmopol_adapt] ERROR: {exc}", file=sys.stderr)
        return 0  # non-fatal for autonomous learning stack


def _run(payload: dict) -> int:
    if not BUCKETS.exists():
        payload["note"] = "no performance_buckets.csv — governor will learn on first live/tester closes"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps({"wrote": str(OUT), "note": payload["note"]}, indent=2))
        return 0

    b = pd.read_csv(BUCKETS, header=None, names=["key", "n_eff", "wins_eff", "sum_r_eff", "blocked_until", "last_update"])
    for _, row in b.iterrows():
        key = str(row["key"])
        if not key.startswith("SOV-FXMOPOL|"):
            continue
        n = float(row["n_eff"])
        s = float(row["sum_r_eff"])
        if n < MIN_N:
            continue
        rec = {"bucket": key, "n_eff": round(n, 2), "sum_r_eff": round(s, 3)}
        if s <= BLOCK_SUM_R:
            payload["blocked_buckets"].append(rec)
        elif s > 0.5:
            payload["promoted_buckets"].append(rec)
        parts = key.split("|")
        if len(parts) >= 2:
            sym = parts[1]
            payload["symbols"].setdefault(sym, {"buckets": 0, "blocked": 0, "promoted": 0})
            payload["symbols"][sym]["buckets"] += 1

    for rec in payload["blocked_buckets"]:
        sym = rec["bucket"].split("|")[1]
        payload["symbols"].setdefault(sym, {"buckets": 0, "blocked": 0, "promoted": 0})
        payload["symbols"][sym]["blocked"] += 1
    for rec in payload["promoted_buckets"]:
        sym = rec["bucket"].split("|")[1]
        payload["symbols"].setdefault(sym, {"buckets": 0, "blocked": 0, "promoted": 0})
        payload["symbols"][sym]["promoted"] += 1

    if OUTCOMES.exists():
        o = pd.read_csv(OUTCOMES)
        payload["outcome_rows"] = len(o)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), **{k: payload[k] for k in ("blocked_buckets", "promoted_buckets") if payload[k]}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
