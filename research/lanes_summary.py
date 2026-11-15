#!/usr/bin/env python3
"""Merge lane 1–3 JSON outputs into research/results/lanes_summary.json."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

PATHS = {
    "lane1_ecn": RESULTS / "ecn_carry_friction.json",
    "lane2_alt": RESULTS / "alt_sleeve_manifest.json",
    "lane3_high_risk": RESULTS / "carry_high_risk_pick.json",
    "balanced_stack": RESULTS / "stack_refine_pick.json",
}


def main() -> int:
    out = {"lanes": {}, "policy": {"live_deploy": False, "default_sleeve": "stack_refine_pick"}}
    for key, path in PATHS.items():
        if path.exists():
            out["lanes"][key] = json.loads(path.read_text(encoding="utf-8"))
    dest = RESULTS / "lanes_summary.json"
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
