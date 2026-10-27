#!/usr/bin/env python3
"""Test the sign of macro context separately for mean-reversion and breakout.

Hypothesis:
    mean reversion wants a stretched move (trade against recent DXY/VIX flow)
    breakout wants aligned momentum (trade with recent DXY/VIX flow)

If that is false, the macro layer is not useful as an entry filter.
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
    # Stretch is the negative of alignment: high stretch means the trade is
    # fading a recent dollar / risk move.
    df["stretch"] = -(df["dxy_align"] + df["vix_align"] + df["flow_align"] * 1000.0)
    df["stretch_dv"] = -(df["dxy_align"] + df["vix_align"])
    df["stretch_flow"] = -df["flow_align"]

    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    print(f"merged={len(df)}  {df.signal_time.min()} .. {df.signal_time.max()}")
    rows = [line("baseline", tr, va, te)]
    print("\n--- baseline by engine ---")
    for mode in (0, 1):
        rows.append(line(f"mode {mode}", tr[tr["mode"] == mode], va[va["mode"] == mode], te[te["mode"] == mode]))

    print("\n--- mean reversion: fade recent dollar/risk move ---")
    m0 = lambda x: x["mode"] == 0
    tests = {
        "m0 dxy_align < 0": lambda x: (x["mode"] == 0) & (x.dxy_align < 0),
        "m0 dxy_align < -1": lambda x: (x["mode"] == 0) & (x.dxy_align < -1),
        "m0 dxy_align < -2": lambda x: (x["mode"] == 0) & (x.dxy_align < -2),
        "m0 vix_align < 0": lambda x: (x["mode"] == 0) & (x.vix_align < 0),
        "m0 vix_align < -1": lambda x: (x["mode"] == 0) & (x.vix_align < -1),
        "m0 flow_align < 0": lambda x: (x["mode"] == 0) & (x.flow_align < 0),
        "m0 stretch_dv > 0": lambda x: (x["mode"] == 0) & (x.stretch_dv > 0),
        "m0 stretch_dv > 1": lambda x: (x["mode"] == 0) & (x.stretch_dv > 1),
        "m0 stretch_dv > 2": lambda x: (x["mode"] == 0) & (x.stretch_dv > 2),
        "m0 stretch_flow > 0": lambda x: (x["mode"] == 0) & (x.stretch_flow > 0),
        "m0 dxy<0 & vix<0": lambda x: (x["mode"] == 0) & (x.dxy_align < 0) & (x.vix_align < 0),
        "m0 dxy<0 & flow<0": lambda x: (x["mode"] == 0) & (x.dxy_align < 0) & (x.flow_align < 0),
        "m0 vix<0 & flow<0": lambda x: (x["mode"] == 0) & (x.vix_align < 0) & (x.flow_align < 0),
        "m0 short & dxy<0": lambda x: (x["mode"] == 0) & (x.side < 0) & (x.dxy_align < 0),
        "m0 long  & dxy<0": lambda x: (x["mode"] == 0) & (x.side > 0) & (x.dxy_align < 0),
        "m0 short & stretch_dv>1": lambda x: (x["mode"] == 0) & (x.side < 0) & (x.stretch_dv > 1),
        "m0 long  & stretch_dv>1": lambda x: (x["mode"] == 0) & (x.side > 0) & (x.stretch_dv > 1),
        "m0 extreme>=0.2 & dxy<0": lambda x: (x["mode"] == 0) & (x.dxy_align < 0),
    }
    for name, fn in tests.items():
        rows.append(line(name, tr[fn(tr)], va[fn(va)], te[fn(te)]))

    print("\n--- breakout: follow recent dollar/risk move ---")
    tests = {
        "m1 dxy_align > 0": lambda x: (x["mode"] == 1) & (x.dxy_align > 0),
        "m1 dxy_align > 1": lambda x: (x["mode"] == 1) & (x.dxy_align > 1),
        "m1 dxy_align > 2": lambda x: (x["mode"] == 1) & (x.dxy_align > 2),
        "m1 vix_align > 0": lambda x: (x["mode"] == 1) & (x.vix_align > 0),
        "m1 flow_align > 0": lambda x: (x["mode"] == 1) & (x.flow_align > 0),
        "m1 stretch_dv < 0": lambda x: (x["mode"] == 1) & (x.stretch_dv < 0),
        "m1 stretch_dv < -1": lambda x: (x["mode"] == 1) & (x.stretch_dv < -1),
        "m1 dxy>0 & vix>0": lambda x: (x["mode"] == 1) & (x.dxy_align > 0) & (x.vix_align > 0),
        "m1 dxy>0 & flow>0": lambda x: (x["mode"] == 1) & (x.dxy_align > 0) & (x.flow_align > 0),
        "m1 vix>0 & flow>0": lambda x: (x["mode"] == 1) & (x.vix_align > 0) & (x.flow_align > 0),
        "m1 short & dxy>0": lambda x: (x["mode"] == 1) & (x.side < 0) & (x.dxy_align > 0),
        "m1 long  & dxy>0": lambda x: (x["mode"] == 1) & (x.side > 0) & (x.dxy_align > 0),
    }
    for name, fn in tests.items():
        rows.append(line(name, tr[fn(tr)], va[fn(va)], te[te[fn.__name__] if False else fn(te)]))

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
    res.to_csv(OUT / "regime_queries.csv", index=False)
    print(f"\nSaved: {OUT / 'regime_queries.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
