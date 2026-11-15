#!/usr/bin/env python3
"""Carry+mom sleeve promotion — pre-registered 3-split monthly proof."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "carry_promotion_verdict.json"

SPLITS = {
    "design_2005_2014": ("2005-01-01", "2015-01-01"),
    "validation_2015_2019": ("2015-01-01", "2020-01-01"),
    "OOS_2020_2026": ("2020-01-01", "2031-01-01"),
}


def metrics(monthly_ret: pd.Series) -> dict:
    r = monthly_ret.dropna()
    if len(r) < 3:
        return {"n": len(r), "cagr": 0.0, "sharpe": 0.0, "max_dd": 0.0, "pf": 0.0, "sum_pct": 0.0}
    equity = (1 + r).cumprod()
    peak = equity.cummax()
    dd = (equity / peak - 1).min()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1 / 12)
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) * 100
    sharpe = float(r.mean() / (r.std() + 1e-12) * np.sqrt(12))
    wins, losses = r[r > 0], r[r < 0]
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else 0.0
    return {
        "n": len(r),
        "cagr": round(cagr, 2),
        "sharpe": round(sharpe, 2),
        "max_dd": round(float(dd) * 100, 2),
        "pf": round(pf, 2),
        "sum_pct": round(float(r.sum() * 100), 2),
    }


def main() -> int:
    from export_period_results import carry_mom_filter_series, load_monthly_prices, load_rates  # noqa: PLC0415
    from carry import CURRENCIES  # noqa: PLC0415

    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
    r = carry_mom_filter_series(spot.loc[idx], diff.loc[idx], price.loc[idx])

    split_stats = {name: metrics(r[(r.index >= a) & (r.index < b)]) for name, (a, b) in SPLITS.items()}
    all_positive = all(split_stats[k]["sum_pct"] > 0 and split_stats[k]["pf"] >= 1.0 for k in split_stats)
    deployable = all_positive and split_stats["OOS_2020_2026"]["max_dd"] > -30

    verdict = {
        "sleeve": "carry_mom_filter",
        "role": "core_book" if all_positive else "research_only",
        "promoted_3split": all_positive,
        "deployable_frozen_policy": deployable,
        "rule": "each split monthly sum_pct>0 and PF>=1.0; deployable also max_dd>-30% on OOS",
        "splits": split_stats,
        "note": "Weak CAGR ~1.6% full sample — research-positive, not live per AGENTS.md unless you change policy",
    }
    OUT.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
