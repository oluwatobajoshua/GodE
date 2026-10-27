#!/usr/bin/env python3
"""Independent M5 validation of the EA's hour-7 Donchian breakout.

The H1/D1 research families all failed on HistData.  This script checks the
actual intraday setup used by the EA: mode-1 Donchian breakout, H1 trend
alignment, completed M5 signal bar, next-bar market entry, 1.5 ATR stop,
3R target, 12-point round-trip cost, and the 07:00-07:59 UTC session.

Run after `build_histdata_m5.py`:
    .\\.venv\\Scripts\\python.exe research/m5_independent.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import core
from candidates import CandidateConfig, generate

HERE = Path(__file__).resolve().parent
HIST = HERE / "data" / "histdata"
OUT = HERE / "results" / "histdata"


def block(d: pd.DataFrame, label: str) -> dict:
    if d.empty:
        return {"label": label, "n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    w = d["r_multiple"] > 0
    gp = d.loc[w, "r_multiple"].sum()
    gl = -d.loc[~w, "r_multiple"].sum()
    return {
        "label": label,
        "n": int(len(d)),
        "win": round(float(w.mean() * 100), 1),
        "avg": round(float(d["r_multiple"].mean()), 3),
        "total": round(float(d["r_multiple"].sum()), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else 999.0,
    }


def main() -> int:
    core.DATA_DIR = HIST
    df = core.load_data(timeframe="M5", symbol="EURUSD")
    print(f"M5 rows={len(df)} {df.time.min()} .. {df.time.max()}")

    # 12-point round-trip cost: 11 points spread + 1 point slippage.
    cfg = CandidateConfig(spread=11.0, slippage=1.0)
    cand = generate(df, cfg)
    cand["year"] = cand["signal_time"].dt.year
    cand["signal_hour"] = cand["signal_time"].dt.hour
    # The EA checks the session at execution time, which is the next bar open.
    cand["hour"] = cand["entry_time"].dt.hour
    OUT.mkdir(parents=True, exist_ok=True)
    cand.to_csv(OUT / "m5_candidates_histdata.csv", index=False)
    print(f"candidates={len(cand)} mode1={len(cand[cand['mode'] == 1])}")

    rows = []
    m1 = cand[cand["mode"] == 1].copy()
    for hour in range(0, 24):
        rows.append({"scope": f"mode1 hour {hour}", **block(m1[m1["hour"] == hour], f"hour {hour}")})

    h7 = m1[m1["hour"] == 7].copy()
    for year, d in h7.groupby("year"):
        rows.append({"scope": f"mode1 hour7 {year}", **block(d, str(year))})

    for label, lo, hi in [
        ("design_2011_2014", 2011, 2015),
        ("validation_2015_2019", 2015, 2020),
        ("OOS_2020_2025", 2020, 2026),
        ("full_2011_2025", 2011, 2026),
    ]:
        d = h7[(h7["year"] >= lo) & (h7["year"] < hi)]
        rows.append({"scope": label, **block(d, label)})

    for side, name in [(1, "long"), (-1, "short")]:
        rows.append({"scope": f"mode1 hour7 {name}", **block(h7[h7["side"] == side], name)})

    # The stored r_multiple is net of 12 points.  Re-price it for nearby cost
    # assumptions; this is exact because the simulator subtracts the round-trip
    # cost once from the gross R.
    for cost in (10.0, 12.0, 14.0, 16.0):
        adj = h7.copy()
        adj["r_multiple"] = adj["r_multiple"] + (12.0 - cost) / adj["sl_dist_pts"]
        rows.append({"scope": f"mode1 hour7 cost{cost:.0f}", **block(adj, f"cost {cost:.0f}")})

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "m5_hour7_independent.csv", index=False)
    print("\n=== M5 mode-1 metrics by hour, all years ===")
    print(out[out["scope"].str.match(r"mode1 hour \d+$")].to_string(index=False))
    print("\n=== M5 mode-1 hour 7 by year ===")
    print(out[out["scope"].str.match(r"mode1 hour7 \d{4}")].to_string(index=False))
    print("\n=== M5 mode-1 hour 7 by split ===")
    print(out[out["scope"].str.startswith(("design_", "validation_", "OOS_", "full_"))].to_string(index=False))
    print("\n=== M5 mode-1 hour 7 by side ===")
    print(out[out["scope"].str.startswith("mode1 hour7 long") | out["scope"].str.startswith("mode1 hour7 short")].to_string(index=False))
    print("\n=== M5 mode-1 hour 7 cost sensitivity ===")
    print(out[out["scope"].str.startswith("mode1 hour7 cost")].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
