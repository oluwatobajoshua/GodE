#!/usr/bin/env python3
"""H1 FXMOPOL promotion rule — overlay-only until OOS passes thresholds.

Rule (pre-registered):
  - OOS trade count >= min_trades (default 30)
  - OOS total return % > 0
  - OOS profit factor >= min_pf (default 1.05)

Run:
    .\\.venv\\Scripts\\python.exe research\\h1_promotion.py --symbol GBPUSD --mode professor_governor
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "h1_promotion_verdict.json"
MANIFEST = HERE / "results" / "fxmopol_stack_pnl_manifest.json"

DEFAULT_MIN_TRADES = 30
DEFAULT_MIN_PF = 1.05


def evaluate(oos: dict, *, min_trades: int, min_pf: float) -> dict:
    n = int(oos.get("n") or 0)
    total = float(oos.get("total_pct") or 0)
    pf = float(oos.get("pf") or 0)
    reasons: list[str] = []
    if n < min_trades:
        reasons.append(f"OOS trades {n} < {min_trades}")
    if total <= 0:
        reasons.append(f"OOS total_pct {total} <= 0")
    if pf < min_pf and n >= 10:
        reasons.append(f"OOS PF {pf} < {min_pf}")
    promoted = len(reasons) == 0 and n >= min_trades
    role = "promoted_h1_sleeve" if promoted else "overlay_only"
    return {
        "promoted": promoted,
        "role": role,
        "oos_n": n,
        "oos_total_pct": total,
        "oos_pf": pf,
        "reasons": reasons,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--mode", default="professor_governor")
    p.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES)
    p.add_argument("--min-pf", type=float, default=DEFAULT_MIN_PF)
    args = p.parse_args()

    if not MANIFEST.exists():
        print(f"Missing {MANIFEST}; run research/fxmopol_stack_pnl.py")
        return 1
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mode_data = man.get("modes", {}).get(args.mode)
    if not mode_data:
        print(f"Mode {args.mode} not in manifest")
        return 1
    oos = mode_data.get("splits", {}).get("OOS", {})
    verdict = evaluate(oos, min_trades=args.min_trades, min_pf=args.min_pf)
    payload = {
        "symbol": args.symbol,
        "mode": args.mode,
        "rule": {
            "min_oos_trades": args.min_trades,
            "min_oos_pf": args.min_pf,
            "oos_total_pct_gt": 0,
        },
        **verdict,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT}")
    return 0 if verdict["promoted"] else 0  # exit 0 always — overlay is valid outcome


if __name__ == "__main__":
    raise SystemExit(main())
