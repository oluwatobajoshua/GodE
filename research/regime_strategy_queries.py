#!/usr/bin/env python3
"""Regime-split strategy queries: trade with the daily trend or fade a range.

The prior tests showed mean reversion is structurally weak and breakouts are
only near breakeven.  This script checks the obvious missing condition:
breakouts should run with the daily trend, while mean reversion should only
operate inside a range.  Monthly results are printed to expose instability.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cross_asset_analysis import add_directional, line, metrics  # noqa: E402

MODEL = HERE / "models"
OUT = HERE / "results"


def main() -> int:
    cand = pd.read_csv(MODEL / "candidates_M5.csv", parse_dates=["signal_time"]).sort_values("signal_time")
    ca = pd.read_csv(MODEL / "cross_asset_M5.csv", parse_dates=["time"]).sort_values("time")
    df = pd.merge_asof(
        cand, ca, left_on="signal_time", right_on="time", direction="backward",
        tolerance=pd.Timedelta("6min"),
    ).dropna(subset=["dxy_ret6_atr", "vix_chg6_z", "flow_12", "d1_dist_sma200_atr"]).reset_index(drop=True)
    df = add_directional(df)
    df["d1_align"] = df["side"] * df["d1_dist_sma200_atr"]
    df["h1_di_align"] = df["side"] * df["h1_di_diff_scaled"]
    df["month"] = df["signal_time"].dt.to_period("M").astype(str)
    df["adx"] = df["h1_adx_scaled"] * 50.0
    df["rsi2"] = df["rsi2_scaled"] * 100.0

    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    rules = {
        # Breakout with the daily trend.
        "m1 & d1_align>0": lambda x: (x["mode"] == 1) & (x.d1_align > 0),
        "m1 & d1_align>0 & dxy>0": lambda x: (x["mode"] == 1) & (x.d1_align > 0) & (x.dxy_align > 0),
        "m1 & d1_align>0 & adx>=25": lambda x: (x["mode"] == 1) & (x.d1_align > 0) & (x.adx >= 25),
        "m1 & d1_align>0 & dxy>0 & adx>=25": lambda x: (x["mode"] == 1) & (x.d1_align > 0) & (x.dxy_align > 0) & (x.adx >= 25),
        "m1 & d1_align>0 & dxy>0 & di>0": lambda x: (x["mode"] == 1) & (x.d1_align > 0) & (x.dxy_align > 0) & (x.h1_di_align > 0),
        "m1 & d1_align>0 & dxy>0 & atr>50": lambda x: (x["mode"] == 1) & (x.d1_align > 0) & (x.dxy_align > 0) & (x.atr_pct > 0.5),
        "m1 & d1_align>0 & dxy>0 & rw>=5": lambda x: (x["mode"] == 1) & (x.d1_align > 0) & (x.dxy_align > 0) & (x.range_width_atr >= 5),
        # Mean reversion with the daily trend (pullback), not against it.
        "m0 & d1_align>0": lambda x: (x["mode"] == 0) & (x.d1_align > 0),
        "m0 & d1_align>0 & dxy<0": lambda x: (x["mode"] == 0) & (x.d1_align > 0) & (x.dxy_align < 0),
        "m0 & d1_align>0 & dxy<0 & adx<25": lambda x: (x["mode"] == 0) & (x.d1_align > 0) & (x.dxy_align < 0) & (x.adx < 25),
        "m0 & d1_align>0 & dxy<0 & adx>=25": lambda x: (x["mode"] == 0) & (x.d1_align > 0) & (x.dxy_align < 0) & (x.adx >= 25),
        "m0 & d1_align>0 & dxy<0 & vixz<0.5": lambda x: (x["mode"] == 0) & (x.d1_align > 0) & (x.dxy_align < 0) & (x.vix_z < 0.5),
        "m0 & d1_align>0 & dxy<0 & rsi_ext": lambda x: (x["mode"] == 0) & (x.d1_align > 0) & (x.dxy_align < 0) & (((x.side > 0) & (x.rsi2 < 10)) | ((x.side < 0) & (x.rsi2 > 90))),
        # Range fade against the daily trend, but only when ADX is low.
        "m0 & d1_align<0 & dxy<0 & adx<20": lambda x: (x["mode"] == 0) & (x.d1_align < 0) & (x.dxy_align < 0) & (x.adx < 20),
        "m0 & adx<20": lambda x: (x["mode"] == 0) & (x.adx < 20),
        "m0 & adx<20 & dxy<0": lambda x: (x["mode"] == 0) & (x.adx < 20) & (x.dxy_align < 0),
    }

    rows = [line("baseline", tr, va, te)]
    print("\n--- regime rules ---")
    for name, fn in rules.items():
        rows.append(line(name, tr[fn(tr)], va[fn(va)], te[fn(te)]))

    res = pd.DataFrame(
        [
            {
                "name": r["name"],
                **{f"train_{k}": v for k, v in r["train"].items()},
                **{f"val_{k}": v for k, v in r["val"].items()},
                **{f"test_{k}": v for k, v in r["test"].items()},
            }
            for r in rows
        ]
    )
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "regime_strategy_queries.csv", index=False)
    print(f"\nSaved: {OUT / 'regime_strategy_queries.csv'}")

    print("\n--- monthly net R for selected rules ---")
    selected = [
        "m1 & d1_align>0",
        "m1 & d1_align>0 & dxy>0",
        "m1 & d1_align>0 & dxy>0 & adx>=25",
        "m0 & d1_align>0 & dxy<0",
        "m0 & d1_align>0 & dxy<0 & adx<25",
        "m0 & adx<20",
    ]
    print(f"{'month':8s}" + "".join(f"{n[:26]:>28s}" for n in selected))
    for month in sorted(df.month.unique()):
        m = df[df.month == month]
        cells = []
        for name in selected:
            fn = rules[name]
            d = m[fn(m)]
            if d.empty:
                cells.append(f"{'-':>28s}")
            else:
                cells.append(f"n={len(d):3d} R={d.r_multiple.sum():+7.1f} avg={d.r_multiple.mean():+.2f}".rjust(28))
        print(f"{month:8s}" + "".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
