#!/usr/bin/env python3
"""Cost vs signal: is the loss the entry, or the spread?

`m5_independent.py` stores every candidate with `r_multiple` already net of a
12-point round-trip cost and the stop distance in points (`sl_dist_pts`). Because
the simulator subtracts the cost once from gross R, we can re-price exactly:

    gross_r = r_multiple + 12 / sl_dist_pts
    net_r(c) = gross_r - c / sl_dist_pts

So cost = 0 gives the pure signal (mid-to-mid). If gross R is negative, the entry
has no edge and no filter can save it. If gross R is positive but net is negative,
the strategy is a cost problem and could in principle be fixed.

Usage:
    python research/cost_vs_signal.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CAND = HERE / "results" / "histdata" / "m5_candidates_histdata.csv"
OUT = HERE / "results"
COSTS = (0.0, 4.0, 8.0, 12.0, 20.0)


def stats(d: pd.DataFrame, label: str, cost: float) -> dict:
    if d.empty:
        return {"scope": label, "cost_pts": cost, "n": 0}
    r = d["gross_r"] - cost / d["sl_dist_pts"]
    w = r > 0
    gp = r[w].sum()
    gl = -r[~w].sum()
    wr = float(w.mean())
    avg_win = float(r[w].mean()) if w.any() else 0.0
    avg_loss = float(r[~w].mean()) if (~w).any() else 0.0
    # win rate needed to break even at this cost, using the observed payoff
    be = (
        100 * abs(avg_loss) / (abs(avg_loss) + avg_win)
        if avg_win > 0 and avg_loss < 0
        else np.nan
    )
    return {
        "scope": label,
        "cost_pts": cost,
        "n": int(len(d)),
        "win_pct": round(100 * wr, 1),
        "avg_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 1),
        "pf": round(float(gp / gl), 2) if gl > 0 else np.nan,
        "breakeven_win_pct": round(be, 1) if not np.isnan(be) else np.nan,
        "edge_pp": round(100 * wr - be, 1) if not np.isnan(be) else np.nan,
    }


def main() -> int:
    df = pd.read_csv(CAND, parse_dates=["entry_time"])
    df["gross_r"] = df["r_multiple"] + 12.0 / df["sl_dist_pts"]
    df["year"] = df["entry_time"].dt.year

    splits = {
        "design_2011_2014": (2011, 2014),
        "validation_2015_2019": (2015, 2019),
        "OOS_2020_2025": (2020, 2025),
        "full_2011_2025": (2011, 2025),
    }

    frames = {}
    frames["mode1_hour7"] = df[(df["mode"] == 1) & (df["hour"] == 7)]
    frames["mode1_all_hours"] = df[df["mode"] == 1]
    frames["mode0_meanrev"] = df[df["mode"] == 0]

    rows = []
    for name, sub in frames.items():
        for split, (lo, hi) in splits.items():
            d = sub[(sub["year"] >= lo) & (sub["year"] <= hi)]
            for cost in COSTS:
                rows.append(stats(d, f"{name}|{split}", cost))
    out = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "cost_vs_signal.csv", index=False)

    pd.set_option("display.width", 200)
    for name in frames:
        print(f"\n{'='*88}\n{name}\n{'='*88}")
        t = out[out["scope"].str.startswith(name + "|")]
        t = t[t["cost_pts"].isin((0.0, 8.0, 12.0))]
        print(t.to_string(index=False))

    # Yearly gross edge for the hour-7 breakout: is there any stable signal?
    h7 = frames["mode1_hour7"]
    yearly = (
        h7.groupby("year")
        .apply(
            lambda d: pd.Series(
                {
                    "n": len(d),
                    "gross_avg_r": d["gross_r"].mean(),
                    "net_avg_r_12": (d["gross_r"] - 12 / d["sl_dist_pts"]).mean(),
                    "win_pct": 100 * (d["gross_r"] > 0).mean(),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    yearly.to_csv(OUT / "cost_vs_signal_hour7_yearly.csv", index=False)
    print(f"\n{'='*88}\nhour-7 breakout: gross (cost=0) vs net (cost=12) by year\n{'='*88}")
    print(yearly.round(3).to_string(index=False))
    pos = int((yearly["gross_avg_r"] > 0).sum())
    print(f"\nyears with positive GROSS edge: {pos}/{len(yearly)}")
    print(f"years with positive NET edge:   {int((yearly['net_avg_r_12'] > 0).sum())}/{len(yearly)}")
    print(f"\nWrote {OUT / 'cost_vs_signal.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
