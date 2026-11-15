#!/usr/bin/env python3
"""Long-horizon sleeve: vol-scaled carry_mom + cash when drawdown breaches."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "research" / "results" / "century_survivor_summary.csv"


def main() -> int:
    subprocess.check_call(
        [sys.executable, str(REPO / "research" / "carry.py")],
        cwd=str(REPO),
    )
    carry = pd.read_csv(REPO / "research" / "results" / "carry_returns.csv", index_col=0)
    col = "carry_mom_filter"
    if col not in carry.columns:
        print("carry_mom_filter column missing")
        return 1
    r = carry[col].dropna()
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    halt = dd < -0.20
    r2 = r.copy()
    r2[halt] = 0.0
    eq2 = (1 + r2).cumprod()
    years = (r2.index[-1] - r2.index[0]).days / 365.25
    cagr = eq2.iloc[-1] ** (1 / years) - 1 if years > 0 else 0.0
    sharpe = r2.mean() / r2.std() * (12**0.5) if r2.std() > 0 else 0.0
    row = {
        "sleeve": "carry_mom_dd_halt_20",
        "years": round(years, 1),
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd_pct": round((eq2 / eq2.cummax() - 1).min() * 100, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_csv(OUT, index=False)
    print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
