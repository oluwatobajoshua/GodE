#!/usr/bin/env python3
"""Narrow refinement grid outside sovereign_stack_tune (972 configs).

The main tune grid used VIX bands {18,20,22}x{28,30,32} and mid {0.5,0.75}.
This script searches a small pre-declared extension (VIX lo 16-19, hi 26-34,
mid 0.45-0.65) on the same carry+12m mom Stack v1 logic. Pick = highest
geometric-mean Sharpe (full x OOS) among configs that pass the same 3-split gate
as sovereign_stack_tune.

Run:
    .\\.venv\\Scripts\\python.exe research\\stack_refine.py
"""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import pandas as pd

from carry import CURRENCIES, load_monthly_prices, load_rates, metrics
from period_returns import print_split_monthly, publish_results
from sovereign_stack_tune import Cfg, SPLITS, build_series, passes, profit_factor, split_returns

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "stack_refine.csv"
PICK = HERE / "results" / "stack_refine_pick.json"

REFINE_GRID = {
    "mom_lb": (12,),
    "top_n": (4,),
    "vix_lo": (16.0, 17.0, 18.0, 19.0),
    "vix_hi": (26.0, 28.0, 30.0, 32.0, 34.0),
    "vix_mid": (0.45, 0.5, 0.55, 0.65, 0.75),
    "dd_halt": (None,),
    "neutral": (True,),
}

SPLIT_PRINT = {
    "design_2005_2014": ("2005-01-01", "2015-01-01"),
    "validation_2015_2019": ("2015-01-01", "2020-01-01"),
    "OOS_2020_2026": ("2020-01-01", "2030-01-01"),
}


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    rows = []
    grid = [
        Cfg(mom, n, vlo, vhi, mid, dd, neu)
        for mom, n, vlo, vhi, mid, dd, neu in product(
            REFINE_GRID["mom_lb"],
            REFINE_GRID["top_n"],
            REFINE_GRID["vix_lo"],
            REFINE_GRID["vix_hi"],
            REFINE_GRID["vix_mid"],
            REFINE_GRID["dd_halt"],
            REFINE_GRID["neutral"],
        )
        if vlo < vhi
    ]

    for cfg in grid:
        series = build_series(spot, diff, price, cfg)
        parts = split_returns(series)
        ok_all = all(passes(parts[sp]) for sp in SPLITS)
        row = {"config": cfg.key(), "promoted_3split": ok_all}
        for sp in SPLITS:
            part = parts[sp]
            m = metrics(part, sp)
            pf = profit_factor(part)
            row[f"cagr_{sp}"] = m["cagr"]
            row[f"sharpe_{sp}"] = m["sharpe"]
            row[f"maxdd_{sp}"] = m["maxdd"]
            row[f"pf_{sp}"] = round(pf, 2) if pd.notna(pf) else None
        full = series.dropna()
        fm = metrics(full, "full")
        row["cagr_full"] = fm["cagr"]
        row["sharpe_full"] = fm["sharpe"]
        row["maxdd_full"] = fm["maxdd"]
        row["blend_sharpe"] = (fm["sharpe"] * row["sharpe_OOS_2020_2026"]) ** 0.5 if ok_all else 0.0
        rows.append(row)

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("blend_sharpe", ascending=False).to_csv(OUT, index=False)

    promoted = df[df.promoted_3split]
    if promoted.empty:
        print("No promoted config in refinement grid.")
        return 1

    pick = promoted.sort_values("blend_sharpe", ascending=False).iloc[0]
    pick_cfg = next(c for c in grid if c.key() == pick["config"])
    series = build_series(spot, diff, price, pick_cfg)
    publish_results(f"refine_{pick_cfg.key()}", series, already_monthly=True)
    print_split_monthly(pick_cfg.key(), series, SPLIT_PRINT, already_monthly=True)

    payload = {
        "objective": "blend_sharpe = sqrt(sharpe_full * sharpe_OOS)",
        "prior_oos_pick": "mom12_n4_v18_30_m0.75_ddoff_neu1",
        "config": pick["config"],
        "cagr_full": float(pick["cagr_full"]),
        "sharpe_full": float(pick["sharpe_full"]),
        "maxdd_full": float(pick["maxdd_full"]),
        "cagr_OOS_2020_2026": float(pick["cagr_OOS_2020_2026"]),
        "sharpe_OOS_2020_2026": float(pick["sharpe_OOS_2020_2026"]),
        "maxdd_OOS_2020_2026": float(pick["maxdd_OOS_2020_2026"]),
        "blend_sharpe": float(pick["blend_sharpe"]),
        "ea_inputs": {
            "InpUseSovereignStackV1": True,
            "InpTopN": pick_cfg.top_n,
            "InpStackVixLo": pick_cfg.vix_lo,
            "InpStackVixHi": pick_cfg.vix_hi,
            "InpStackVixMidScale": pick_cfg.vix_mid,
            "InpStackRequireNeutral": pick_cfg.neutral,
            "InpStackDdHaltEnable": pick_cfg.dd_halt is not None,
        },
        "caveat": "Refinement grid outside main 972-cell tune; 3-split gate unchanged; not a live deploy unlock.",
    }
    PICK.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nPick: {pick['config']} blend_sharpe={pick['blend_sharpe']:.3f}")
    print(f"Wrote {OUT.name}, {PICK.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
