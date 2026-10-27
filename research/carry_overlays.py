#!/usr/bin/env python3
"""Risk overlays for the G10 carry + momentum strategy.

The independent HistData check confirmed that carry + 12-month momentum is the
only strategy family that stays positive in design, validation and out-of-sample
periods.  This script tests simple, pre-specified risk overlays on that
strategy.  It does not change the signal; it scales exposure when the prior
month's VIX close is elevated, when the strategy's own 12-month return is
negative, or when a faster 3/6-month realised volatility target is breached.

Run:
    .\\.venv\\Scripts\\python.exe research/carry_overlays.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from carry import (
    CURRENCIES,
    RATES,
    TARGET_VOL,
    carry_weights,
    load_monthly_prices,
    load_rates,
    metrics,
    run,
    split_returns,
)

HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))


def load_vix() -> pd.Series:
    d = pd.read_csv(RATES / "VIX.csv")
    d.columns = ["date", "value"]
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d = d.dropna().drop_duplicates("date").set_index("date")["value"].sort_index()
    m = d.resample("ME").last()
    m.index = m.index.to_period("M").to_timestamp()
    return m


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]

    diff = rates[CURRENCIES.keys()].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)

    base_w = carry_weights(diff_prev, top_n=3)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)

    vix = load_vix().reindex(idx).ffill()
    vix_prev = vix.shift(1)

    def scale_from(lo: float, hi: float, mid_scale: float) -> pd.Series:
        s = pd.Series(1.0, index=idx)
        s[vix_prev > lo] = mid_scale
        s[vix_prev > hi] = 0.0
        return s

    # The momentum filter can remove every name on one side. In that case the
    # book is no longer dollar-neutral: it is a residual dollar trend bet. Test
    # a strict dollar-neutral variant that stands aside when either side is empty.
    both_sides = long_ok.any(axis=1) & short_ok.any(axis=1)
    neutral = filtered.mul(both_sides.astype(float), axis=0)

    variants = {
        "carry_mom": filtered,
        "carry_mom_neutral": neutral,
        "carry_mom_neutral_vix20_30": neutral.mul(scale_from(20, 30, 0.75), axis=0),
        "carry_mom_neutral_vix25_35": neutral.mul(scale_from(25, 35, 0.50), axis=0),
        "carry_mom_vix20_30": filtered.mul(scale_from(20, 30, 0.75), axis=0),
        "carry_mom_vix25_35": filtered.mul(scale_from(25, 35, 0.50), axis=0),
        "carry_mom_vix18_28": filtered.mul(scale_from(18, 28, 0.50), axis=0),
    }

    rows = []
    curves = {}
    for name, w in variants.items():
        r = run(w, spot, diff, price)
        curves[name] = r
        for split, part in split_returns(r).items():
            rows.append({"name": name, "split": split, **metrics(part, name)})

    # Diagnostic risk overlays applied to the base strategy's return series.
    # These do not change the signal; they test whether a faster volatility
    # target or a trend filter on the strategy itself improves the risk path.
    base_r = curves["carry_mom"]
    trend = ((1 + base_r).rolling(12).apply(np.prod, raw=True) - 1).shift(1)
    overlay_returns = {"carry_mom_trend12": base_r.where(trend.gt(0), 0.0)}
    for window in (3, 6):
        vol = base_r.rolling(window).std() * np.sqrt(12)
        scalar = (TARGET_VOL / vol).clip(upper=2.0).shift(1).fillna(1.0)
        overlay_returns[f"carry_mom_vol{window}"] = base_r * scalar

    for name, r in overlay_returns.items():
        curves[name] = r
        for split, part in split_returns(r).items():
            rows.append({"name": name, "split": split, **metrics(part, name)})

    summary = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "carry_overlays_summary.csv", index=False)
    pd.DataFrame(curves).to_csv(OUT / "carry_overlays_returns.csv")

    print("=== carry overlays: full period ===")
    print(summary[summary["split"] == "full_2005_2026"].sort_values("sharpe", ascending=False).to_string(index=False))
    print("\n=== carry overlays: CAGR by split ===")
    piv = summary.pivot(index="name", columns="split", values="cagr")
    print(piv.round(2).to_string())
    print("\n=== carry overlays: max drawdown by split ===")
    piv_dd = summary.pivot(index="name", columns="split", values="maxdd")
    print(piv_dd.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
