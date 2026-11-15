#!/usr/bin/env python3
"""Lane 1: ECN-style friction on the promoted carry stack (monthly research).

Sweeps round-trip cost assumptions (0–20 pts) on the same stack as
stack_refine_pick.json. Does not replace MT5 multi-leg tester — see
scripts/run_lane1_mt5_truth.ps1.

Run:
    .\\.venv\\Scripts\\python.exe research\\ecn_carry_friction.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from carry import CURRENCIES, load_monthly_prices, load_rates, metrics, split_returns
from period_returns import publish_results
from sovereign_stack_tune import cfg_from_key, build_series, passes

HERE = Path(__file__).resolve().parent
PICK = HERE / "results" / "stack_refine_pick.json"
OUT = HERE / "results" / "ecn_carry_friction.csv"
VERDICT = HERE / "results" / "ecn_carry_friction.json"

COST_SWEEP = (0.0, 3.0, 8.0, 12.0, 20.0)


def main() -> int:
    if not PICK.exists():
        print(f"Missing {PICK.name} — run stack_refine.py first.")
        return 1
    key = json.loads(PICK.read_text(encoding="utf-8"))["config"]
    cfg = cfg_from_key(key)

    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    rows = []
    for pts in COST_SWEEP:
        series = build_series(spot, diff, price, cfg, cost_pts=pts)
        full = metrics(series.dropna(), "full")
        parts = split_returns(series)
        ok_all = all(passes(parts[sp]) for sp in ("design_2005_2014", "validation_2015_2019", "OOS_2020_2026"))
        rows.append(
            {
                "cost_pts": pts,
                "label": "ecn" if pts <= 8 else ("deriv" if pts <= 12 else "wide"),
                "cagr_full": full["cagr"],
                "sharpe_full": full["sharpe"],
                "maxdd_full": full["maxdd"],
                "profitable_3split": ok_all,
            }
        )
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)

    base = build_series(spot, diff, price, cfg, cost_pts=12.0)
    publish_results(f"ecn_ref_stack_12pts", base, already_monthly=True)

    verdict = {
        "stack": key,
        "note": "8 pts ≈ ECN round-turn proxy; 12 pts = Deriv retail default in carry.py",
        "rows": rows,
        "ecn_8pts_still_3split": bool(df.loc[df.cost_pts == 8, "profitable_3split"].iloc[0])
        if 8 in COST_SWEEP
        else None,
        "mt5_next": ".\\scripts\\run_lane1_mt5_truth.ps1",
    }
    VERDICT.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(df.to_string(index=False))
    print(f"Wrote {OUT.name}, {VERDICT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
