#!/usr/bin/env python3
"""Parity check: EA carry logic vs research carry.py.

Replicates SovereignCarryMom.mqh logic exactly:
  * Rank by rate diff (foreign - USD), descending
  * Long top N with positive 12m momentum, short bottom N with negative mom
  * Neutral book filter
  * Vol targeting: scalar = volTarget / rolling12mVol, max 2x
  * Hold to month-end (no stop loss)
  * Monthly rebalance on prior-month rates

Compares against research/carry.py carry_mom_filter output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent / "research"))
from carry import load_rates, load_monthly_prices, metrics, split_returns, CURRENCIES, USD_RATE


def ea_carry_weights(diff: pd.DataFrame, mom: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    """Replicate EA BuildTargets logic."""
    rank = diff.rank(axis=1, ascending=False)
    n = diff.notna().sum(axis=1)
    long_ok = rank.le(top_n) & mom.gt(0)
    short_ok = rank.gt(n - top_n, axis=0) & mom.lt(0)

    n_long = long_ok.sum(axis=1).replace(0, np.nan)
    n_short = short_ok.sum(axis=1).replace(0, np.nan)
    w = long_ok.div(n_long, axis=0).fillna(0) - short_ok.div(n_short, axis=0).fillna(0)

    # Neutral book filter: if all long or all short, flatten
    has_long = (w > 0).any(axis=1)
    has_short = (w < 0).any(axis=1)
    w = w.where(has_long & has_short, 0.0)
    return w


def ea_vol_target(w: pd.DataFrame, spot: pd.DataFrame,
                  vol_target: float = 0.10, lookback_months: int = 12,
                  max_scalar: float = 2.0) -> pd.DataFrame:
    """Replicate EA ComputeVolScalar: portfolio vol targeting."""
    # Portfolio returns using current weights
    port_ret = (w.shift(1) * spot).sum(axis=1, min_count=1).fillna(0.0)

    # Rolling vol over lookback months
    rolling_vol = port_ret.rolling(lookback_months).std() * np.sqrt(12)

    scalar = (vol_target / rolling_vol).clip(upper=max_scalar).fillna(1.0)
    scalar = scalar.clip(lower=0.0)

    return w.mul(scalar, axis=0)


def run_ea_carry():
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]

    # Prior-month rate differential (columns are CURRENCIES keys: EUR, GBP, etc.)
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)

    # 12-month momentum
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)

    # EA weights (momentum filter + neutral book)
    w = ea_carry_weights(diff_prev, mom_prev, top_n=3)

    # Vol targeting
    w = ea_vol_target(w, spot, vol_target=0.10, lookback_months=12, max_scalar=2.0)

    # Carry return (prior month's rate diff / 1200)
    carry = diff_prev / 1200.0

    # Cost: turnover * cost_pts * points / price
    COST_PTS = 12.0
    points = pd.Series({ccy: meta[2] for ccy, meta in CURRENCIES.items()})
    per_unit = (COST_PTS * points) / price.shift(1)
    cost = (w.diff().abs() * per_unit).sum(axis=1, min_count=1).fillna(0.0)

    ret = ((w * (spot + carry)).sum(axis=1, min_count=1) - cost).dropna()

    print("=== EA Carry Logic (parity with research carry_mom_filter) ===")
    print(f"Full period: CAGR={metrics(ret, 'ea')['cagr']}%, Sharpe={metrics(ret, 'ea')['sharpe']}, MaxDD={metrics(ret, 'ea')['maxdd']}%")

    print("\n=== By split ===")
    for split, part in split_returns(ret).items():
        m = metrics(part, 'ea')
        print(f"  {split}: CAGR={m['cagr']}%, Sharpe={m['sharpe']}, MaxDD={m['maxdd']}%, Total={m['total']}%")

    print("\n=== Research carry_mom_filter (from carry_summary.csv) ===")
    print("  Full:    CAGR=1.61%, Sharpe=0.20, MaxDD=-26.30%")
    print("  Design:  CAGR=1.32%, Sharpe=0.17, MaxDD=-26.30%")
    print("  Val:     CAGR=0.05%, Sharpe=0.06, MaxDD=-14.08%")
    print("  OOS:     CAGR=3.26%, Sharpe=0.34, MaxDD=-17.46%")
    print("  Recent:  CAGR=5.38%, Sharpe=0.53, MaxDD=-7.32%")


if __name__ == "__main__":
    run_ea_carry()
