#!/usr/bin/env python3
"""Compare Deriv-Demo D1 candles with independent HistData D1 candles.

The goal is to see whether the broker feed is broadly consistent with an
independent source before trusting either one for strategy conclusions.

Run:
    .\\.venv\\Scripts\\python.exe research/compare_data.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DERIV = HERE / "data"
HIST = HERE / "data" / "histdata"
OUT = HERE / "results"
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP"]
POINTS = {"USDJPY": 0.001, "EURJPY": 0.001, "AUDJPY": 0.001, "CADJPY": 0.001}


def load(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path, usecols=["time", "open", "high", "low", "close"], parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None).dt.normalize()
    d = d.drop_duplicates("time").set_index("time").sort_index()
    return d


def main() -> int:
    rows = []
    for pair in PAIRS:
        a_path = DERIV / f"{pair}_D1.csv"
        b_path = HIST / f"{pair}_D1.csv"
        if not a_path.exists() or not b_path.exists():
            continue
        a = load(a_path)
        b = load(b_path)
        joined = a.join(b, how="inner", lsuffix="_deriv", rsuffix="_hist")
        if joined.empty:
            continue
        point = POINTS.get(pair, 0.00001)
        ret_a = joined["close_deriv"].pct_change()
        ret_b = joined["close_hist"].pct_change()
        mask = ret_a.notna() & ret_b.notna()
        corr = ret_a[mask].corr(ret_b[mask])
        diff = (joined["close_deriv"] - joined["close_hist"]).abs() / point
        rows.append(
            {
                "pair": pair,
                "matched_days": len(joined),
                "deriv_days": len(a),
                "hist_days": len(b),
                "ret_corr": round(float(corr), 4),
                "mean_abs_close_diff_pts": round(float(diff.mean()), 2),
                "p95_abs_close_diff_pts": round(float(diff.quantile(0.95)), 2),
                "max_abs_close_diff_pts": round(float(diff.max()), 2),
                "deriv_start": str(a.index.min().date()),
                "deriv_end": str(a.index.max().date()),
                "hist_start": str(b.index.min().date()),
                "hist_end": str(b.index.max().date()),
            }
        )
    out = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "data_comparison.csv", index=False)
    print(out.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
