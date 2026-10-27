#!/usr/bin/env python3
"""Deep check of the London-open breakout window.

The corrected hour analysis found one stable pocket: the Donchian breakout
engine during the 07:00 UTC hour was positive in train, validation and test.
This script checks whether that is a real session effect or a small-sample
artifact by looking at adjacent hours, side, month, macro alignment and
alternative exits.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cross_asset_analysis import add_directional, metrics  # noqa: E402

MODEL = HERE / "models"
OUT = HERE / "results"


def block(d: pd.DataFrame, label: str) -> None:
    if d.empty:
        print(f"{label:<38} n=0")
        return
    w = d["r"] > 0
    gp = d.loc[w, "r"].sum()
    gl = -d.loc[~w, "r"].sum()
    pf = gp / gl if gl > 0 else 999.0
    print(f"{label:<38} n={len(d):4d} win={w.mean()*100:5.1f}% avgR={d.r.mean():+.3f} "
          f"totalR={d.r.sum():+7.1f} PF={pf:.2f}")


def main() -> int:
    cand = pd.read_csv(MODEL / "candidates_M5.csv", parse_dates=["signal_time"])
    ca = pd.read_csv(MODEL / "cross_asset_M5.csv", parse_dates=["time"]).sort_values("time")
    ta = pd.read_csv(MODEL / "ta_features_M5.csv", parse_dates=["time"]).sort_values("time")

    df = pd.merge_asof(cand.sort_values("signal_time"), ca, left_on="signal_time",
                       right_on="time", direction="backward", tolerance=pd.Timedelta("6min"))
    df = pd.merge_asof(df, ta, left_on="signal_time", right_on="time",
                       direction="backward", tolerance=pd.Timedelta("10min"))
    df = df.dropna(subset=["dxy_ret6_atr", "vix_chg6_z", "d1_dist_sma200_atr"]).reset_index(drop=True)
    df = add_directional(df)
    df["hour"] = ((np.degrees(np.arctan2(df["hour_sin"], df["hour_cos"])) / 15.0) % 24.0).round().astype(int)
    df["d1_align"] = df["side"] * df["d1_dist_sma200_atr"]
    df["h1_di_align"] = df["side"] * df["h1_di_diff_scaled"]
    df["momentum_align"] = df["side"] * df["ret1_atr"]
    df["adx"] = df["h1_adx_scaled"] * 50.0
    df["r"] = df["r_multiple"]
    df["month"] = df["signal_time"].dt.strftime("%Y-%m")

    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    print("=== mode1 by hour: all metrics ===")
    for h in range(6, 10):
        print(f"\n-- hour {h} --")
        for name, d in [("train", tr), ("val", va), ("test", te)]:
            block(d[(d["mode"] == 1) & (d.hour == h)], f"{name} mode1 hour {h}")
            block(d[(d["mode"] == 1) & (d.hour == h) & (d.side > 0)], f"{name} mode1 LONG hour {h}")
            block(d[(d["mode"] == 1) & (d.hour == h) & (d.side < 0)], f"{name} mode1 SHORT hour {h}")

    print("\n=== monthly: mode1 hour 7 ===")
    h7 = df[(df["mode"] == 1) & (df.hour == 7)]
    for month, m in h7.groupby("month"):
        block(m, f"mode1 hour7 {month}")

    print("\n=== monthly: mode1 hours 6-9 ===")
    h69 = df[(df["mode"] == 1) & (df.hour.between(6, 9))]
    for month, m in h69.groupby("month"):
        block(m, f"mode1 hours6-9 {month}")

    print("\n=== mode1 hour7 with macro/trend filters ===")
    rules = {
        "h7 all": lambda x: x.hour == 7,
        "h7 dxy>0": lambda x: (x.hour == 7) & (x.dxy_align > 0),
        "h7 dxy>1": lambda x: (x.hour == 7) & (x.dxy_align > 1),
        "h7 vix>0": lambda x: (x.hour == 7) & (x.vix_align > 0),
        "h7 dxy>0 & vix>0": lambda x: (x.hour == 7) & (x.dxy_align > 0) & (x.vix_align > 0),
        "h7 d1_align>0": lambda x: (x.hour == 7) & (x.d1_align > 0),
        "h7 d1_align>0 & dxy>0": lambda x: (x.hour == 7) & (x.d1_align > 0) & (x.dxy_align > 0),
        "h7 adx>=20": lambda x: (x.hour == 7) & (x.adx >= 20),
        "h7 adx>=25": lambda x: (x.hour == 7) & (x.adx >= 25),
        "h7 atr_pct 0.2-0.8": lambda x: (x.hour == 7) & x.atr_pct.between(0.2, 0.8),
        "h7 dxy>0 & adx>=20": lambda x: (x.hour == 7) & (x.dxy_align > 0) & (x.adx >= 20),
        "h7 dxy>0 & d1>0 & adx>=20": lambda x: (x.hour == 7) & (x.dxy_align > 0) & (x.d1_align > 0) & (x.adx >= 20),
    }
    for name, fn in rules.items():
        print(f"\n-- {name} --")
        for label, d in [("train", tr), ("val", va), ("test", te)]:
            block(d[(d["mode"] == 1) & fn(d)], f"{label} {name}")

    print("\n=== mode1 hour7 with alternative exits ===")
    pol = OUT / "candidate_exit_policies.csv"
    if pol.exists():
        p = pd.read_csv(pol)
        p["hour"] = pd.to_datetime(p["signal_time"], utc=True).dt.hour
        for policy in ["base_tp3", "time_24", "time_48", "time_48_mfe1", "trail_1_05", "trail_1_05_time48"]:
            d = p[(p.policy == policy) & (p["mode"] == 1) & (p.hour == 7)]
            for label, mask in [
                ("train", d.signal_time < str(train_end)),
                ("val", (d.signal_time >= str(train_end)) & (d.signal_time < str(val_end))),
                ("test", d.signal_time >= str(val_end)),
            ]:
                x = d[mask]
                if x.empty:
                    continue
                w = x.exit_r > 0
                print(f"{policy:<18} {label:<5} n={len(x):3d} win={w.mean()*100:5.1f}% avgR={x.exit_r.mean():+.3f} totalR={x.exit_r.sum():+7.1f}")

    print("\n=== all mode1 hours 6-9 with exit variants (test only) ===")
    if pol.exists():
        p = pd.read_csv(pol)
        p["hour"] = pd.to_datetime(p["signal_time"], utc=True).dt.hour
        for policy in ["base_tp3", "time_48", "trail_1_05_time48"]:
            d = p[(p.policy == policy) & (p["mode"] == 1) & (p.hour.between(6, 9)) & (p.signal_time >= str(val_end))]
            if d.empty:
                continue
            w = d.exit_r > 0
            print(f"{policy:<18} test n={len(d):3d} win={w.mean()*100:5.1f}% avgR={d.exit_r.mean():+.3f} totalR={d.exit_r.sum():+7.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
