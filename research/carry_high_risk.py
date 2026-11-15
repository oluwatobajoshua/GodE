#!/usr/bin/env python3
"""Lane 3: explicit higher-risk carry stack (documented DD floor).

Scales vol target on the refined balanced stack (same signals, more notional).
Pick = highest full-period CAGR with 3-split pass and maxDD >= DD_FLOOR.

Run:
    .\\.venv\\Scripts\\python.exe research\\carry_high_risk.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from carry import CURRENCIES, load_monthly_prices, load_rates, metrics, split_returns
from period_returns import publish_results, print_split_monthly
from sovereign_stack_tune import SPLITS, build_series, cfg_from_key, passes

HERE = Path(__file__).resolve().parent
PICK_REFINE = HERE / "results" / "stack_refine_pick.json"
OUT = HERE / "results" / "carry_high_risk.csv"
PICK = HERE / "results" / "carry_high_risk_pick.json"

DD_FLOOR = -35.0
VOL_TARGETS = (0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.18, 0.20)

SPLIT_PRINT = {
    "design_2005_2014": ("2005-01-01", "2015-01-01"),
    "validation_2015_2019": ("2015-01-01", "2020-01-01"),
    "OOS_2020_2026": ("2020-01-01", "2030-01-01"),
}


def main() -> int:
    if not PICK_REFINE.exists():
        print("Run stack_refine.py first.")
        return 1
    cfg = cfg_from_key(json.loads(PICK_REFINE.read_text(encoding="utf-8"))["config"])

    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    rows = []
    cache: dict[float, pd.Series] = {}
    for vol in VOL_TARGETS:
        series = build_series(spot, diff, price, cfg, vol_target=vol)
        cache[vol] = series
        parts = split_returns(series)
        ok_all = all(passes(parts[sp]) for sp in SPLITS)
        full = metrics(series.dropna(), "full")
        rows.append(
            {
                "base_config": cfg.key(),
                "vol_target": vol,
                "promoted_3split": ok_all,
                "cagr_full": full["cagr"],
                "sharpe_full": full["sharpe"],
                "maxdd_full": full["maxdd"],
                "cagr_OOS": metrics(parts["OOS_2020_2026"], "oos")["cagr"],
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(OUT, index=False)

    eligible = df[(df.promoted_3split) & (df.maxdd_full >= DD_FLOOR)]
    if eligible.empty:
        PICK.write_text(json.dumps({"error": "none_eligible", "dd_floor": DD_FLOOR}, indent=2))
        print("No vol scale passed 3-split within DD floor.")
        return 1

    pick_row = eligible.sort_values("cagr_full", ascending=False).iloc[0]
    vol = float(pick_row["vol_target"])
    series = cache[vol]
    publish_results(f"high_risk_{cfg.key()}_v{vol}", series, already_monthly=True)
    print_split_monthly(f"high_risk_vol{vol}", series, SPLIT_PRINT, already_monthly=True)

    geom_mo = ((1 + pick_row["cagr_full"] / 100) ** (1 / 12) - 1) * 100
    payload = {
        "dd_floor_pct": DD_FLOOR,
        "base_config": cfg.key(),
        "vol_target": vol,
        "cagr_full": float(pick_row["cagr_full"]),
        "maxdd_full": float(pick_row["maxdd_full"]),
        "cagr_OOS": float(pick_row["cagr_OOS"]),
        "approx_monthly_compound_pct": round(geom_mo, 3),
        "caveat": "Python vol_target only; EA uses InpRisk* — align manually in tester.",
        "ea_inputs_overlay": {
            "InpRiskPerPosition": 2.0,
            "InpMaxTotalRisk": 8.0,
            "InpTopN": cfg.top_n,
            "InpStackVixLo": cfg.vix_lo,
            "InpStackVixHi": cfg.vix_hi,
            "InpStackVixMidScale": cfg.vix_mid,
            "InpStackRequireNeutral": cfg.neutral,
            "InpStackDdHaltEnable": False,
        },
    }
    PICK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nPick: vol_target={vol} CAGR={pick_row['cagr_full']}% maxDD={pick_row['maxdd_full']}%")
    print(f"~{geom_mo:.2f}%/month compound vs balanced 2.53% CAGR")
    print(f"Wrote {OUT.name}, {PICK.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
