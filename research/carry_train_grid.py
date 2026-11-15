#!/usr/bin/env python3
"""Train carry_mom on DESIGN only (cost_pts, top_n); score frozen val/OOS.

Run:
    .\\.venv\\Scripts\\python.exe research\\carry_train_grid.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT_CSV = HERE / "results" / "carry_train_grid.csv"
OUT_PICK = HERE / "results" / "carry_train_pick.json"

from carry import (  # noqa: E402
    CURRENCIES,
    carry_weights,
    load_monthly_prices,
    load_rates,
    metrics,
    run,
    split_returns,
)
from sovereign_stack_tune import passes  # noqa: E402

COST_GRID = (0.0, 3.0, 8.0, 12.0, 16.0, 20.0)
TOP_N_GRID = (2, 3, 4)


def carry_mom_series(
    spot: pd.DataFrame,
    diff: pd.DataFrame,
    price: pd.DataFrame,
    *,
    top_n: int,
    cost_pts: float,
) -> pd.Series:
    diff_prev = diff.shift(1)
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)
    base_w = carry_weights(diff_prev, top_n=top_n)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    return run(filtered, spot, diff, price, cost_pts=cost_pts)


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    rows = []
    best_key = None
    best_sharpe = -999.0

    for cost_pts in COST_GRID:
        for top_n in TOP_N_GRID:
            series = carry_mom_series(spot, diff, price, top_n=top_n, cost_pts=cost_pts)
            parts = split_returns(series)
            d = parts["design_2005_2014"]
            m_d = metrics(d, "design")
            row = {
                "cost_pts": cost_pts,
                "top_n": top_n,
                "design_cagr": m_d["cagr"],
                "design_sharpe": m_d["sharpe"],
                "design_pf_pass": passes(d),
            }
            for sp in ("validation_2015_2019", "OOS_2020_2026"):
                m = metrics(parts[sp], sp)
                row[f"{sp}_cagr"] = m["cagr"]
                row[f"{sp}_sharpe"] = m["sharpe"]
                row[f"{sp}_pass"] = passes(parts[sp])
            rows.append(row)
            if row["design_pf_pass"] and m_d["sharpe"] > best_sharpe:
                best_sharpe = m_d["sharpe"]
                best_key = row

    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    pick = {}
    if best_key:
        pick = {k: (bool(v) if isinstance(v, (np.bool_, bool)) else float(v) if isinstance(v, np.floating) else int(v) if isinstance(v, np.integer) else v) for k, v in best_key.items()}
    val_oos_ok = pick and pick.get("validation_2015_2019_pass") and pick.get("OOS_2020_2026_pass")
    manifest = {
        "method": "max design Sharpe among PF-pass rows; params frozen for val/OOS",
        "pick": pick,
        "val_oos_still_pass": bool(val_oos_ok),
        "note": "If pick fails val/OOS, keep default cost_pts=12 top_n=3",
    }
    OUT_PICK.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print(f"\nWrote {OUT_CSV.name}, {OUT_PICK.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
