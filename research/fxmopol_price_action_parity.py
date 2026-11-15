#!/usr/bin/env python3
"""Verify FXMOPOL price-action primitives exist (EA + chart_core parity list).

Run: .\\.venv\\Scripts\\python.exe research\\fxmopol_price_action_parity.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "fxmopol_price_action_parity.json"

CHECKS = [
    "stop_hunt_reclaim",
    "stop_hunt_reclaim_stack",
    "playbook_chart_entry",
    "pullback_long",
    "retest_short",
    "fractal_low",
    "fractal_high",
    "regime_streak",
    "stack_long",
    "stack_short",
    "extension_exit_trigger",
    "passes_entry_filters",
]

def main() -> int:
    import fxmopol_chart_core as core

    rows = [{"fn": name, "ok": hasattr(core, name)} for name in CHECKS]
    mqh = (HERE.parent / "EA" / "Include" / "SovereignFxmopol.mqh").read_text(encoding="utf-8")
    ea = {
        "PlaybookChartEntry": "PlaybookChartEntry" in mqh,
        "WickHuntReclaimStack": "WickHuntReclaimStack" in mqh,
        "LevelTpHit": "LevelTpHit" in mqh,
        "InpUseFullPriceAction": "InpUseFullPriceAction" in (HERE.parent / "EA" / "SovereignFxmopolEA.mq5").read_text(
            encoding="utf-8"
        ),
    }
    payload = {"python": rows, "ea_mqh": ea, "note": "OOS certification unchanged — implementation only"}
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if all(r["ok"] for r in rows) and all(ea.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
