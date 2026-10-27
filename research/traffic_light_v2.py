#!/usr/bin/env python3
"""Strategy-specific traffic-light board.

A mean-reversion pullback and a breakout need opposite short-term conditions.
Using one universal board was the mistake in the first test: it demanded
momentum alignment from a strategy whose whole premise is a temporary
pullback.  This version has a separate board for each engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cross_asset_analysis import add_directional, line  # noqa: E402

MODEL = HERE / "models"
OUT = HERE / "results"


def build() -> pd.DataFrame:
    cand = pd.read_csv(MODEL / "candidates_M5.csv", parse_dates=["signal_time"]).sort_values("signal_time")
    ca = pd.read_csv(MODEL / "cross_asset_M5.csv", parse_dates=["time"]).sort_values("time")
    ta = pd.read_csv(MODEL / "ta_features_M5.csv", parse_dates=["time"]).sort_values("time")
    df = pd.merge_asof(cand, ca, left_on="signal_time", right_on="time", direction="backward", tolerance=pd.Timedelta("6min"))
    df = pd.merge_asof(df.sort_values("signal_time"), ta, left_on="signal_time", right_on="time", direction="backward", tolerance=pd.Timedelta("10min"))
    df = df.dropna(subset=["dxy_ret6_atr", "vix_chg6_z", "flow_12", "d1_dist_sma200_atr"]).reset_index(drop=True)
    df = add_directional(df)
    df["d1_align"] = df["side"] * df["d1_dist_sma200_atr"]
    df["h1_di_align"] = df["side"] * df["h1_di_diff_scaled"]
    df["momentum_align"] = df["side"] * df["ret1_atr"]
    df["hour"] = ((np.degrees(np.arctan2(df["hour_sin"], df["hour_cos"])) / 15.0) % 24.0).round().astype(int)
    df["room_align"] = np.where(df["side"] > 0, df["room_to_resistance_atr"], df["room_to_support_atr"])
    df["opposing"] = np.where(df["side"] > 0, df["resistance_confluence"], df["support_confluence"])
    df["rev_align"] = np.where(df["side"] > 0, df["bull_reversal_score"], df["bear_reversal_score"])
    df["cont_align"] = np.where(df["side"] > 0, df["bull_continuation_score"], df["bear_continuation_score"])
    df["break_align"] = np.where(df["side"] > 0, df["break_up_count"], df["break_down_count"])
    df["month"] = df["signal_time"].dt.strftime("%Y-%m")
    df["adx"] = df["h1_adx_scaled"] * 50.0
    return df.copy()


def main() -> int:
    df = build()
    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    # --- shared quality lights -------------------------------------------
    df["C_vol"] = df["atr_pct"].between(0.20, 0.80)
    df["C_session"] = df["hour"].between(7, 15)
    df["C_spread"] = df["spread_scaled"] <= 1.0
    df["C_room"] = df["room_align"] >= 1.5
    df["C_no_opp"] = df["opposing"] == 0
    # --- mean-reversion pullback board -----------------------------------
    df["M0_trend"] = (df["d1_align"] > 0) & (df["h1_trend"] == df["side"])
    df["M0_structure"] = np.where(df["side"] > 0, df["range_pos"] <= 0.20, df["range_pos"] >= 0.80)
    df["M0_pullback"] = (df["h1_di_align"] > 0) & (df["momentum_align"] < -0.20)
    df["M0_macro"] = df["dxy_align"] < 0
    df["M0_sent"] = df["vix_align"] < 0
    df["M0_pattern"] = df["rev_align"] >= 1
    # --- breakout continuation board ------------------------------------
    df["M1_trend"] = (df["d1_align"] > 0) & (df["h1_trend"] == df["side"])
    df["M1_structure"] = df["break_align"] >= 1
    df["M1_momentum"] = (df["h1_di_align"] > 0) & (df["momentum_align"] >= 0)
    df["M1_macro"] = df["dxy_align"] > 0
    df["M1_sent"] = df["vix_align"] > 0
    df["M1_pattern"] = df["cont_align"] >= 1

    shared = ["C_vol", "C_session", "C_spread", "C_room", "C_no_opp"]
    m0 = ["M0_trend", "M0_structure", "M0_pullback", "M0_macro", "M0_sent", "M0_pattern"]
    m1 = ["M1_trend", "M1_structure", "M1_momentum", "M1_macro", "M1_sent", "M1_pattern"]
    for c in shared + m0 + m1:
        df[c] = df[c].fillna(False).astype(int)
    df["M0_green"] = df[shared + m0].sum(axis=1)
    df["M1_green"] = df[shared + m1].sum(axis=1)
    df["M0_full"] = (df["mode"] == 0) & (df["M0_green"] == 11)
    df["M1_full"] = (df["mode"] == 1) & (df["M1_green"] == 11)

    print(f"rows={len(df)}")
    rows = [line("baseline", tr, va, te)]
    print("\n--- individual strategy lights ---")
    for c in shared + m0 + m1:
        g = df[c] == 1
        rows.append(line(f"{c} green", tr[g.loc[tr.index]], va[g.loc[va.index]], te[g.loc[te.index]]))

    print("\n--- full boards ---")
    for name, mask in {
        "mode0 full 11/11": df.M0_full,
        "mode1 full 11/11": df.M1_full,
        "both full": df.M0_full | df.M1_full,
        "mode0 >=9": (df["mode"] == 0) & (df.M0_green >= 9),
        "mode1 >=9": (df["mode"] == 1) & (df.M1_green >= 9),
        "mode0 >=8": (df["mode"] == 0) & (df.M0_green >= 8),
        "mode1 >=8": (df["mode"] == 1) & (df.M1_green >= 8),
        "mode0 core (no TA)": (df["mode"] == 0) & (df[["M0_trend", "M0_structure", "M0_pullback", "M0_macro", "M0_sent", "C_session", "C_spread"]].sum(axis=1) == 7),
        "mode1 core (no TA)": (df["mode"] == 1) & (df[["M1_trend", "M1_structure", "M1_momentum", "M1_macro", "M1_sent", "C_session", "C_spread"]].sum(axis=1) == 7),
    }.items():
        rows.append(line(name, tr[mask.loc[tr.index]], va[mask.loc[va.index]], te[mask.loc[te.index]]))

    res = pd.DataFrame(
        [{"name": r["name"], **{f"train_{k}": v for k, v in r["train"].items()}, **{f"val_{k}": v for k, v in r["val"].items()}, **{f"test_{k}": v for k, v in r["test"].items()}} for r in rows]
    )
    res.to_csv(OUT / "traffic_light_v2.csv", index=False)
    print(f"\nSaved: {OUT / 'traffic_light_v2.csv'}")

    for label, mask in {
        "mode0 full": df.M0_full,
        "mode1 full": df.M1_full,
        "mode0 >=9": (df["mode"] == 0) & (df.M0_green >= 9),
        "mode1 >=9": (df["mode"] == 1) & (df.M1_green >= 9),
    }.items():
        print(f"\n--- monthly: {label} ---")
        for month, m in df[mask].groupby("month"):
            w = m["r_multiple"] > 0
            print(f"{month} n={len(m):3d} win={w.mean()*100:5.1f}% avgR={m.r_multiple.mean():+.3f} totalR={m.r_multiple.sum():+7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
