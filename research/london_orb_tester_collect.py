#!/usr/bin/env python3
"""Read MT5 London ORB tester JSON/HTML — no manual paste."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
REPORT_JSON = REPO / "backtest" / "reports" / "london_orb_eurusd_m5_oos.json"
REPORT_HTM = REPO / "backtest" / "reports" / "london_orb_eurusd_m5_oos.htm"
OUT = HERE / "results" / "london_orb_tester_manifest.json"
PROMO = HERE / "results" / "scalp_promotion_verdict.json"
UTC_PICK = HERE / "results" / "london_orb_utc_pick.json"


def main() -> int:
    if not REPORT_JSON.exists():
        print(f"Missing {REPORT_JSON} — run .\\scripts\\run_london_orb_tester.ps1")
        return 1
    report = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    metrics = report.get("metrics") or {}
    utc_off = None
    pick_parity = False
    if UTC_PICK.exists():
        pick = json.loads(UTC_PICK.read_text(encoding="utf-8"))
        pick_parity = bool(pick.get("parity_ok"))
        if pick.get("pick"):
            utc_off = pick["pick"].get("utc_offset")
    py_ref = {}
    if PROMO.exists():
        promo = json.loads(PROMO.read_text(encoding="utf-8"))
        for c in promo.get("candidates", []):
            if c.get("symbol") == "EURUSD" and c.get("strategy") == "london_orb":
                py_ref = c
                break
    pf = float(str(metrics.get("Profit Factor", "0")).replace(" ", ""))
    net = float(str(metrics.get("Total Net Profit", "0")).replace(" ", ""))
    trades = int(str(metrics.get("Total Trades", "0")).replace(" ", ""))
    py_pf = float(py_ref.get("oos_pf") or 0)
    py_n = int(py_ref.get("oos_n") or 0)
    manifest = {
        "source": str(REPORT_JSON),
        "html": str(REPORT_HTM) if REPORT_HTM.exists() else None,
        "window": {
            "from": report.get("from_date"),
            "to": report.get("to_date"),
            "symbol": report.get("symbol"),
            "period": report.get("period"),
        },
        "mt5_metrics": metrics,
        "python_scalp_promotion_oos": py_ref,
        "parity_ok": pick_parity
        and net > 0
        and pf >= 1.05
        and abs(trades - py_n) <= max(80, int(py_n * 0.15)),
        "inferred_utc_offset": utc_off,
        "parity_note": (
            "UTC sweep pinned in london_orb_utc_pick.json; promotion requires require_mt5_parity."
        ),
        "tester_ran": True,
    }
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
