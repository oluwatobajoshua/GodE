#!/usr/bin/env python3
"""Pick what to trade in MT5 — uses strategy_certification (good_strategies.json).

Run:
    .\\.venv\\Scripts\\python.exe research\\profitable_sleeve.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RESULTS = HERE / "results"
OUT_JSON = RESULTS / "profitable_sleeve_verdict.json"
OUT_CFG = REPO / "configs" / "profitable_sleeve.json"
GOOD = REPO / "configs" / "good_strategies.json"
CERT = RESULTS / "strategy_certification_verdict.json"
CERT_PY = HERE / "strategy_certification.py"
SCALP = RESULTS / "scalp_promotion_verdict.json"
H1 = RESULTS / "h1_promotion_verdict.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if not CERT.exists() and CERT_PY.exists():
        py = REPO / ".venv" / "Scripts" / "python.exe"
        subprocess.run([str(py if py.exists() else sys.executable), str(CERT_PY)], cwd=str(REPO), check=False)

    router = _load(GOOD)
    cert = _load(CERT)
    scalp = _load(SCALP)
    h1 = _load(H1)

    deploy = router.get("trade_in_mt5_default")
    research_good = router.get("research_good") or []
    desk_good = router.get("desk_good") or []
    discovery_ok = [
        s for s in (cert.get("sleeves") or []) if s.get("sleeve_id", "").startswith("discovery:")
        and s.get("research_good")
    ]

    sleeves_by_id = {s.get("sleeve_id"): s for s in cert.get("sleeves") or []}
    carry_cert = sleeves_by_id.get("carry_mom_filter") or {}

    candidates = []
    if "carry_mom_filter" in research_good:
        candidates.append(
            {
                "tier": "research_good",
                "hypothesis": "carry_mom_filter",
                "wired_ea": "SovereignGodsEyeEA.mq5",
                "chart": "D1",
                "inputs": "InpStrategy=SOV_CARRY_MOM",
                "mt5_tester": ".\\scripts\\run_lane1_mt5_truth.ps1",
                "desk_good": "carry_mom_filter" in desk_good,
            }
        )
    for sid in research_good:
        if sid == "carry_mom_filter" or sid.startswith("discovery:"):
            continue
        candidates.append({"tier": "research_good", "sleeve_id": sid})

    payload = {
        "quality_bar": "configs/strategy_quality_bar.json",
        "definition": router.get("definition"),
        "deploy_frozen_policy": router.get("frozen_live_policy", True),
        "research_good": research_good,
        "desk_good": desk_good,
        "live_good": router.get("live_good") or [],
        "trade_in_mt5": deploy,
        "trade_role": "core_book_weak_edge" if deploy == "carry_mom_filter" else "none",
        "satellite_h1": h1.get("role", "overlay_only"),
        "scalp_any_promoted": bool(scalp.get("any_promoted")),
        "discovery_strict_3split": [d.get("sleeve_id") for d in discovery_ok],
        "carry_certification": carry_cert,
        "candidates_queue": candidates,
        "reasons": carry_cert.get("reasons") or router.get("tiers_explained"),
        "commands": {
            "carry_book": ".\\scripts\\run_carry_book.ps1",
            "certify": ".\\scripts\\run_strategy_certification.ps1",
            "weekly_research": ".\\scripts\\run_research_factory.ps1",
            "mt5_scalp_truth": ".\\scripts\\run_scalp_mt5_truth.ps1 -ExportOnly",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    OUT_CFG.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
