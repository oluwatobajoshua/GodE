#!/usr/bin/env python3
"""Clean the HistData aggregates: drop partial Sunday D1 bars and duplicates."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "histdata"


def main() -> int:
    for path in sorted(DATA.glob("*_D1.csv")):
        d = pd.read_csv(path, parse_dates=["time"])
        before = len(d)
        d = d[d["time"].dt.dayofweek < 5]
        d = d.drop_duplicates("time").sort_values("time")
        d.to_csv(path, index=False)
        print(f"{path.name}: {before} -> {len(d)} rows")
    for path in sorted(DATA.glob("*_H1.csv")):
        d = pd.read_csv(path, parse_dates=["time"])
        before = len(d)
        d = d.drop_duplicates("time").sort_values("time")
        d.to_csv(path, index=False)
        print(f"{path.name}: {before} -> {len(d)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
