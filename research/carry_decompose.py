#!/usr/bin/env python3
"""Decompose carry + momentum returns into dollar, cross-sectional, carry, cost.

Diagnostic only.  The momentum filter can remove every name on one side of the
book, leaving a residual long-USD or short-USD bet.  This script measures how
much of the reported return comes from that dollar exposure versus the
dollar-neutral cross-sectional carry signal.

It rebuilds the same weights as carry.py / carry_overlays.py, applies the same
12-month volatility target and 12-point round-trip cost, and reports the
average monthly contribution of each component by split.

Run:
    .\\.venv\\Scripts\\python.exe research/carry_decompose.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from carry import (
    COST_PTS,
    CURRENCIES,
    TARGET_VOL,
    carry_weights,
    load_monthly_prices,
    load_rates,
    split_returns,
)

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))


def components(weights: pd.DataFrame, spot: pd.DataFrame, diff: pd.DataFrame, price: pd.DataFrame):
    """Return the scaled weights and the monthly return components."""
    w = weights.fillna(0.0)
    carry = diff.shift(1) / 1200.0
    raw = (w * (spot + carry)).sum(axis=1, min_count=1).fillna(0.0)
    pvol = raw.rolling(12).std() * np.sqrt(12)
    scalar = (TARGET_VOL / pvol).clip(upper=2.0).shift(1).fillna(1.0)
    w = w.mul(scalar, axis=0)

    points = pd.Series({ccy: meta[2] for ccy, meta in CURRENCIES.items()})
    per_unit = (COST_PTS * points) / price.shift(1)
    cost = (w.diff().abs() * per_unit).sum(axis=1, min_count=1).fillna(0.0)

    spot_comp = (w * spot).sum(axis=1, min_count=1).fillna(0.0)
    carry_comp = (w * carry).sum(axis=1, min_count=1).fillna(0.0)
    net = w.sum(axis=1)
    mean_spot = spot.mean(axis=1)
    dollar_spot = net * mean_spot
    xsec_spot = spot_comp - dollar_spot
    total = spot_comp + carry_comp - cost

    return {
        "total": total,
        "spot": spot_comp,
        "dollar_spot": dollar_spot,
        "xsec_spot": xsec_spot,
        "carry": carry_comp,
        "cost": cost,
        "net": net,
        "dollar_factor": -mean_spot,
    }


def stats(x: pd.Series, label: str, split: str, variant: str) -> dict:
    x = x.dropna()
    if x.empty or x.std() == 0:
        return {
            "variant": variant,
            "split": split,
            "component": label,
            "months": 0,
            "mean_bps": 0.0,
            "ann_pct": 0.0,
            "vol_pct": 0.0,
            "sharpe": 0.0,
        }
    mean_bps = x.mean() * 1e4
    ann = ((1 + x).prod() ** (12 / len(x)) - 1) * 100
    vol = x.std() * np.sqrt(12) * 100
    sharpe = x.mean() / x.std() * np.sqrt(12)
    return {
        "variant": variant,
        "split": split,
        "component": label,
        "months": len(x),
        "mean_bps": round(mean_bps, 2),
        "ann_pct": round(ann, 2),
        "vol_pct": round(vol, 2),
        "sharpe": round(sharpe, 2),
    }


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]

    diff = rates[list(CURRENCIES)].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)

    base_w = carry_weights(diff_prev, top_n=3)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    both_sides = long_ok.any(axis=1) & short_ok.any(axis=1)
    neutral = filtered.mul(both_sides.astype(float), axis=0)

    rows = []
    for name, w in (("carry_mom", filtered), ("carry_mom_neutral", neutral)):
        c = components(w, spot, diff, price)
        for split, _ in split_returns(c["total"]).items():
            mask = split_returns(c["total"])[split].index
            for component in ("total", "spot", "dollar_spot", "xsec_spot", "carry", "cost"):
                rows.append(stats(c[component].loc[mask], component, split, name))

        # Regression of total return on the dollar factor.
        total = c["total"].dropna()
        dollar = c["dollar_factor"].reindex(total.index).dropna()
        total = total.reindex(dollar.index)
        beta, alpha = np.polyfit(dollar, total, 1)
        corr = total.corr(dollar)
        rows.append(
            {
                "variant": name,
                "split": "full_2005_2026",
                "component": f"regression_beta={beta:.3f}_alpha_ann={alpha * 12 * 100:.2f}%_r2={corr ** 2:.3f}",
                "months": len(total),
                "mean_bps": round(alpha * 1e4, 2),
                "ann_pct": round(alpha * 12 * 100, 2),
                "vol_pct": 0.0,
                "sharpe": 0.0,
            }
        )

    summary = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "carry_decomposition.csv", index=False)

    normal = summary[~summary["component"].str.startswith("regression")]
    print("=== carry decomposition: mean monthly contribution (bps) ===")
    piv = normal.pivot_table(
        index=["variant", "split"], columns="component", values="mean_bps", aggfunc="first"
    )
    print(piv.round(2).to_string())
    print("\n=== carry decomposition: annualised contribution (%) ===")
    piv = normal.pivot_table(
        index=["variant", "split"], columns="component", values="ann_pct", aggfunc="first"
    )
    print(piv.round(2).to_string())
    print("\n=== dollar-factor regression (full period) ===")
    print(summary[summary["component"].str.startswith("regression")].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
