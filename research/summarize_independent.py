#!/usr/bin/env python3
"""Compact summary of the independent-data validation results."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results" / "histdata"

FILES = [
    ("h1_trend_summary.csv", "total"),
    ("h1_portfolio_summary.csv", "total"),
    ("h1_meanrev_summary.csv", "total"),
    ("d1_trend_summary.csv", "total"),
    ("d1_momentum_summary.csv", "cagr"),
    ("cot_sentiment_summary.csv", "cagr"),
    ("carry_summary.csv", "cagr"),
]


def main() -> int:
    for name, value in FILES:
        path = RES / name
        if not path.exists():
            print(f"\n=== {name}: missing ===")
            continue
        df = pd.read_csv(path)
        if df.empty:
            print(f"\n=== {name}: empty ===")
            continue
        key = "config" if "config" in df.columns else "name"
        piv = df.pivot(index=key, columns="split", values=value)
        print(f"\n=== {name} ({value}) ===")
        print(piv.round(2).to_string())
        design = [c for c in piv.columns if c.startswith("design")]
        validation = [c for c in piv.columns if c.startswith("validation")]
        oos = [c for c in piv.columns if c.startswith("OOS")]
        if design and validation and oos:
            d, v, o = design[0], validation[0], oos[0]
            good = piv[(piv[d] > 0) & (piv[v] > 0)]
            print(f"\n  positive in {d} AND {v}:")
            if good.empty:
                print("  none")
            else:
                print(good[[d, v, o]].round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
