#!/usr/bin/env python3
"""Collect MT5 D1 carry tester JSON into one manifest."""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORTS = REPO / "backtest" / "reports"
OUT = HERE / "results" / "carry_mt5_book_manifest.json"


def _f(s: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", str(s).replace(" ", "")) or "0")


def load_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    m = data.get("metrics") or {}
    return {
        "source": str(path),
        "from_date": data.get("from_date"),
        "to_date": data.get("to_date"),
        "net": _f(m.get("Total Net Profit", "0")),
        "pf": _f(m.get("Profit Factor", "0")),
        "trades": int(_f(m.get("Total Trades", "0"))),
        "max_dd": m.get("Balance Drawdown Maximal"),
    }


def main() -> int:
    profiles = ("gods_eye_carry_d1_EURUSD_D1_book", "gods_eye_carry_d1_ecn_EURUSD_D1_book")
    rows = {}
    for name in profiles:
        rows[name.replace("_EURUSD_D1_book", "")] = load_metrics(REPORTS / f"{name}.json")
    fallback = load_metrics(REPORTS / "gods_eye_carry_d1_EURUSD_D1_factory.json")
    monthly_manifest = HERE / "results" / "carry_mt5_monthly_manifest.json"
    monthly_ref = {}
    if monthly_manifest.exists():
        monthly_ref = json.loads(monthly_manifest.read_text(encoding="utf-8")).get("outputs", {})

    payload = {
        "profiles": rows,
        "fallback_factory": fallback,
        "monthly_mt5": monthly_ref,
        "recommend_attach": {
            "ea": "SovereignGodsEyeEA.mq5",
            "timeframe": "D1",
            "inputs": "InpStrategy=SOV_CARRY_MOM",
        },
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
