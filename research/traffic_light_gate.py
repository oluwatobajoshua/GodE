#!/usr/bin/env python3
"""Pre-trade traffic-light board.

Each light is a real, causal condition.  The script reports:

1. how often each light is green,
2. the expectancy when it is green versus red,
3. the result of requiring all / most lights to be green,
4. monthly stability of the gate.

The point is not to force a trade.  If the board is red, the correct action is
no trade.
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


def build() -> pd.DataFrame:
    cand = pd.read_csv(MODEL / "candidates_M5.csv", parse_dates=["signal_time"]).sort_values("signal_time")
    ca = pd.read_csv(MODEL / "cross_asset_M5.csv", parse_dates=["time"]).sort_values("time")
    ta = pd.read_csv(MODEL / "ta_features_M5.csv", parse_dates=["time"]).sort_values("time")
    df = pd.merge_asof(
        cand, ca, left_on="signal_time", right_on="time", direction="backward",
        tolerance=pd.Timedelta("6min"),
    )
    df = pd.merge_asof(
        df.sort_values("signal_time"), ta, left_on="signal_time", right_on="time",
        direction="backward", tolerance=pd.Timedelta("10min"),
    )
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

    # --- The ten lights ---------------------------------------------------
    df["L_trend"] = (df["d1_align"] > 0) & (df["h1_trend"] == df["side"])
    df["L_structure"] = np.where(
        df["mode"] == 0,
        np.where(df["side"] > 0, df["range_pos"] <= 0.20, df["range_pos"] >= 0.80),
        df["break_align"] >= 1,
    )
    df["L_momentum"] = (df["h1_di_align"] > 0) & (df["momentum_align"] >= -0.10)
    df["L_volatility"] = df["atr_pct"].between(0.20, 0.80)
    df["L_macro"] = np.where(df["mode"] == 1, df["dxy_align"] > 0, df["dxy_align"] < 0)
    df["L_sentiment"] = np.where(df["mode"] == 1, df["vix_align"] > 0, df["vix_align"] < 0)
    df["L_session"] = df["hour"].between(7, 15)
    df["L_spread"] = df["spread_scaled"] <= 1.0
    df["L_room"] = df["room_align"] >= 1.5
    df["L_no_opposing"] = df["opposing"] == 0
    df["L_pattern"] = df["rev_align"] >= 1

    light_cols = [
        "L_trend", "L_structure", "L_momentum", "L_volatility", "L_macro",
        "L_sentiment", "L_session", "L_spread", "L_room", "L_no_opposing", "L_pattern",
    ]
    for c in light_cols:
        df[c] = df[c].fillna(False).astype(int)
    df["green_core"] = df[["L_trend", "L_structure", "L_momentum", "L_volatility", "L_macro", "L_sentiment", "L_session", "L_spread"]].sum(axis=1)
    df["green_all"] = df[light_cols].sum(axis=1)
    return df


def main() -> int:
    df = build()
    print(f"rows={len(df)}  {df.signal_time.min()} .. {df.signal_time.max()}")
    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    rows = [line("baseline (all candidates)", tr, va, te)]
    print("\n--- each light: green vs red ---")
    for c in ["L_trend", "L_structure", "L_momentum", "L_volatility", "L_macro", "L_sentiment", "L_session", "L_spread", "L_room", "L_no_opposing", "L_pattern"]:
        g = df[c] == 1
        rows.append(line(f"{c} green", tr[g.loc[tr.index]], va[g.loc[va.index]], te[g.loc[te.index]]))
        rows.append(line(f"{c} red", tr[~g.loc[tr.index]], va[~g.loc[va.index]], te[~g.loc[te.index]]))

    print("\n--- cumulative traffic-light gates ---")
    gates = {
        "core 8/8": df.green_core == 8,
        "core >=7": df.green_core >= 7,
        "core >=6": df.green_core >= 6,
        "core + room (9/9)": df.green_core + df.L_room == 9,
        "core + room >=8": df.green_core + df.L_room >= 8,
        "core + no_opposing (9/9)": df.green_core + df.L_no_opposing == 9,
        "core + room + no_opposing (10/10)": df.green_core + df.L_room + df.L_no_opposing == 10,
        "all 11": df.green_all == 11,
        "all >=10": df.green_all >= 10,
        "all >=9": df.green_all >= 9,
        "all >=8": df.green_all >= 8,
    }
    for name, mask in gates.items():
        rows.append(line(name, tr[mask.loc[tr.index]], va[mask.loc[va.index]], te[mask.loc[te.index]]))

    print("\n--- by engine: core >=7 ---")
    for mode in (0, 1):
        m = df.green_core >= 7
        rows.append(line(f"mode {mode} core>=7", tr[(tr["mode"] == mode) & m.loc[tr.index]], va[(va["mode"] == mode) & m.loc[va.index]], te[(te["mode"] == mode) & m.loc[te.index]]))

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
    res.to_csv(OUT / "traffic_light_gate.csv", index=False)
    print(f"\nSaved: {OUT / 'traffic_light_gate.csv'}")

    print("\n--- monthly: core 8/8 ---")
    d = df[df.green_core == 8]
    for month, m in d.groupby("month"):
        w = m["r_multiple"] > 0
        print(f"{month}  n={len(m):3d} win={w.mean()*100:5.1f}% avgR={m.r_multiple.mean():+.3f} totalR={m.r_multiple.sum():+7.1f}")

    print("\n--- monthly: all 11 ---")
    d = df[df.green_all == 11]
    for month, m in d.groupby("month"):
        w = m["r_multiple"] > 0
        print(f"{month}  n={len(m):3d} win={w.mean()*100:5.1f}% avgR={m.r_multiple.mean():+.3f} totalR={m.r_multiple.sum():+7.1f}")

    # Save the board for inspection / EA parity.
    light_cols = [
        "L_trend", "L_structure", "L_momentum", "L_volatility", "L_macro",
        "L_sentiment", "L_session", "L_spread", "L_room", "L_no_opposing", "L_pattern",
    ]
    keep = ["signal_time", "side", "mode", "r_multiple", "green_core", "green_all"] + light_cols
    df[keep].to_csv(OUT / "traffic_light_rows.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
