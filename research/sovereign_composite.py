#!/usr/bin/env python3
"""Sovereign Stack v1 — proof that 'profit' means selection + risk, not reviving losers.

Rules (pre-specified):
1. Alpha sleeve only: monthly G10 carry + 12m momentum, **dollar-neutral**
   (flat when either side has no names after the momentum filter).
2. VIX overlay on prior-month close: exposure 100% if VIX<=20, 75% if 20<VIX<=30,
   0% (cash) if VIX>30.
3. Optional drawdown halt: flat while strategy equity is more than 20% below peak
   until drawdown recovers to 50% of the threshold.
4. **Zero weight** forever on research lanes that failed validation+OOS:
   M5/H1 intraday, micro scout, daily xsec MR as primary alpha, discovery
   hypotheses without dual-split pass.

Run:
    .\\.venv\\Scripts\\python.exe research\\sovereign_composite.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

from carry import (  # noqa: E402
    CURRENCIES,
    carry_weights,
    load_monthly_prices,
    load_rates,
    metrics,
    run,
    split_returns,
)

OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results")) / "sovereign_composite_summary.csv"
DD_HALT = 0.20
RATES = HERE / "data" / "rates"


def load_vix() -> pd.Series:
    d = pd.read_csv(RATES / "VIX.csv")
    d.columns = ["date", "value"]
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d = d.dropna().drop_duplicates("date").set_index("date")["value"].sort_index()
    m = d.resample("ME").last()
    m.index = m.index.to_period("M").to_timestamp()
    return m


def vix_scale(vix_prev: pd.Series, lo: float = 20.0, hi: float = 30.0, mid: float = 0.75) -> pd.Series:
    s = pd.Series(1.0, index=vix_prev.index)
    s[vix_prev > lo] = mid
    s[vix_prev > hi] = 0.0
    return s


def apply_dd_halt(r: pd.Series, threshold: float = DD_HALT) -> pd.Series:
    out = r.copy()
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    halted = False
    for i in range(len(out)):
        if not halted and dd.iloc[i] <= -threshold:
            halted = True
        if halted:
            out.iloc[i] = 0.0
            if dd.iloc[i] >= -threshold * 0.5:
                halted = False
    return out


def build_neutral_momentum_weights(spot: pd.DataFrame, diff_prev: pd.DataFrame) -> pd.DataFrame:
    base_w = carry_weights(diff_prev, top_n=3)
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    both_sides = long_ok.any(axis=1) & short_ok.any(axis=1)
    return filtered.mul(both_sides.astype(float), axis=0)


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]
    diff = rates[CURRENCIES.keys()].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)

    neutral_w = build_neutral_momentum_weights(spot, diff_prev)
    vix = load_vix().reindex(idx).ffill()
    scale = vix_scale(vix.shift(1))
    w_stack = neutral_w.mul(scale, axis=0)

    sleeves = {
        "sov_stack_v1_neutral_vix20_30": run(w_stack, spot, diff, price),
        "sov_stack_v1_neutral_vix_ddhalt20": apply_dd_halt(run(w_stack, spot, diff, price)),
        "carry_mom_filter_baseline": None,
    }

    carry_path = OUT.parent / "carry_returns.csv"
    if carry_path.exists():
        curves = pd.read_csv(carry_path, index_col=0, parse_dates=True)
        if "carry_mom_filter" in curves.columns:
            sleeves["carry_mom_filter_baseline"] = curves["carry_mom_filter"].dropna()

    sleeves = {k: v for k, v in sleeves.items() if v is not None and not v.empty}

    rows = []
    for name, r in sleeves.items():
        for split, part in split_returns(r).items():
            rows.append({"sleeve": name, "split": split, **metrics(part, name)})

    summary = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT, index=False)

    print("=== Sovereign Stack v1 (vs baseline) — CAGR % by split ===")
    pivot = summary.pivot_table(index="split", columns="sleeve", values="cagr")
    print(pivot.to_string())
    print(f"\nWrote {OUT}")
    print(
        "\nRejected lanes (0% capital): M5 breakout/MR, H1 Donchian, micro scout, "
        "daily xsec MR primary, discovery without validation+OOS pass."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
