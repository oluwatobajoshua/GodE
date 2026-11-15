#!/usr/bin/env python3
"""Long-horizon FX sleeve report (2005 → present, decade splits).

Implements the only research-backed multi-decade candidate in this repo:
monthly carry + 12m momentum, vol-targeted, with optional drawdown halt.

This does NOT prove profits back to the invention of forex — only what is
reproducible on FRED rates + D1 spot in ``research/data/``.

Run:
    .\\.venv\\Scripts\\python.exe research\\sovereign_long_horizon.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = HERE / "results" / "sovereign_long_horizon_summary.csv"
YEARLY = HERE / "results" / "sovereign_long_horizon_yearly.csv"

TARGET_VOL = 0.10
DD_HALT = 0.20
DECADES = [
    ("2005_2014", "2005-01-01", "2015-01-01"),
    ("2015_2024", "2015-01-01", "2025-01-01"),
    ("2025_plus", "2025-01-01", "2035-01-01"),
    ("full", "2005-01-01", "2035-01-01"),
]


def vol_scale(r: pd.Series, target: float = TARGET_VOL) -> pd.Series:
    pvol = r.rolling(12, min_periods=6).std() * np.sqrt(12)
    scalar = (target / pvol).clip(upper=2.0).shift(1).fillna(1.0)
    return (r * scalar).fillna(0.0)


def apply_dd_halt(r: pd.Series, threshold: float = DD_HALT) -> pd.Series:
    out = r.copy()
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    halted = False
    for i, dt in enumerate(out.index):
        if not halted and dd.iloc[i] <= -threshold:
            halted = True
        if halted:
            out.iloc[i] = 0.0
            if dd.iloc[i] >= -threshold * 0.5:
                halted = False
    return out


def metrics(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    if r.empty:
        return {"sleeve": label, "cagr": 0, "sharpe": 0, "maxdd": 0, "years": 0}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else 0.0
    maxdd = (eq / eq.cummax() - 1).min()
    return {
        "sleeve": label,
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "years": round(years, 1),
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "n": len(r),
    }


def main() -> int:
    carry_returns = REPO / "research" / "results" / "carry_returns.csv"
    if not carry_returns.exists():
        subprocess.check_call([sys.executable, str(REPO / "research" / "carry.py")], cwd=str(REPO))
    curves = pd.read_csv(carry_returns, index_col=0, parse_dates=True)
    if "carry_mom_filter" not in curves.columns:
        raise SystemExit("carry_mom_filter missing from carry_returns.csv")

    base = curves["carry_mom_filter"].dropna()
    vol = vol_scale(base)
    halted = apply_dd_halt(vol)

    sleeves = {
        "carry_mom_raw": base,
        "carry_mom_vol10": vol,
        "carry_mom_vol10_ddhalt20": halted,
    }

    rows = []
    for name, series in sleeves.items():
        for decade, start, end in DECADES:
            part = series[(series.index >= start) & (series.index < end)]
            rows.append({"decade": decade, **metrics(part, name)})

    summary = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT, index=False)

    yearly = (1 + halted).groupby(halted.index.year).prod() - 1
    yearly.to_csv(YEARLY, header=["return_pct"])

    print("=== Sovereign long-horizon (best research sleeve) ===")
    print(summary[summary["decade"] == "full"].to_string(index=False))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
