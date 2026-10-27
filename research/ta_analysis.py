#!/usr/bin/env python3
"""Test traditional price-action features on labelled candidates.

The candidate set is split by time.  We look for features that improve both the
validation and the untouched test months, not just the training period.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

MODEL_DIR = HERE / "models"
OUT = HERE / "results"


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "win_pct": 0.0, "avg_r": 0.0, "total_r": 0.0, "pf": 0.0}
    wins = df["r_multiple"] > 0
    gp = float(df.loc[wins, "r_multiple"].sum())
    gl = float(-df.loc[~wins, "r_multiple"].sum())
    return {
        "n": int(len(df)),
        "win_pct": round(float(wins.mean() * 100), 1),
        "avg_r": round(float(df["r_multiple"].mean()), 3),
        "total_r": round(float(df["r_multiple"].sum()), 1),
        "pf": round(gp / gl, 2) if gl > 0 else 999.0,
    }


def line(name: str, train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame) -> dict:
    mt, mv, ms = metrics(train), metrics(val), metrics(test)
    print(
        f"{name:<44} TR n={mt['n']:4d} w={mt['win_pct']:5.1f}% aR={mt['avg_r']:+.3f} tR={mt['total_r']:+7.1f} "
        f"| VA n={mv['n']:4d} w={mv['win_pct']:5.1f}% aR={mv['avg_r']:+.3f} tR={mv['total_r']:+7.1f} "
        f"| TE n={ms['n']:4d} w={ms['win_pct']:5.1f}% aR={ms['avg_r']:+.3f} tR={ms['total_r']:+7.1f}"
    )
    return {"name": name, "train": mt, "val": mv, "test": ms}


def add_directional(df: pd.DataFrame) -> pd.DataFrame:
    s = df["side_sign"]
    long = s > 0
    df["rev_align"] = np.where(long, df["bull_reversal_score"], df["bear_reversal_score"])
    df["rev_against"] = np.where(long, df["bear_reversal_score"], df["bull_reversal_score"])
    df["cont_align"] = np.where(long, df["bull_continuation_score"], df["bear_continuation_score"])
    df["cont_against"] = np.where(long, df["bear_continuation_score"], df["bull_continuation_score"])
    df["support_align"] = np.where(long, df["support_confluence"], df["resistance_confluence"])
    df["opposing_confluence"] = np.where(long, df["resistance_confluence"], df["support_confluence"])
    df["room_align"] = np.where(long, df["room_to_resistance_atr"], df["room_to_support_atr"])
    df["room_against"] = np.where(long, df["room_to_support_atr"], df["room_to_resistance_atr"])
    df["break_align"] = np.where(long, df["break_up_count"], df["break_down_count"])
    df["false_break_against"] = np.where(long, df["false_break_up_count"], df["false_break_down_count"])
    df["ta_align"] = np.where(long, df["bull_ta_score"], df["bear_ta_score"])
    df["ta_conflict"] = np.where(long, df["bear_ta_score"], df["bull_ta_score"])
    df["ta_net"] = df["ta_align"] - df["ta_conflict"]
    # Directional versions of the individual candle patterns.
    pairs = {
        "engulf": ("bull_engulf", "bear_engulf"),
        "hammer": ("hammer", "shooting_star"),
        "harami": ("bull_harami", "bear_harami"),
        "piercing": ("piercing_line", "dark_cloud"),
        "star": ("morning_star", "evening_star"),
        "tweezer": ("tweezer_bottom", "tweezer_top"),
        "strong_close": ("strong_bull_close", "strong_bear_close"),
    }
    for name, (bull, bear) in pairs.items():
        df[f"{name}_align"] = np.where(long, df[bull], df[bear])
        df[f"{name}_against"] = np.where(long, df[bear], df[bull])
    return df


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default=str(MODEL_DIR / "candidates_M5.csv"))
    parser.add_argument("--ta", default=str(MODEL_DIR / "ta_features_M5.csv"))
    parser.add_argument("--actual", default=str(OUT / "actual_trades_analysis.csv"))
    parser.add_argument("--train-end", default="2026-05-01")
    parser.add_argument("--val-end", default="2026-07-01")
    parser.add_argument("--out", default=str(OUT / "ta_analysis.csv"))
    args = parser.parse_args()

    cand = pd.read_csv(args.candidates, parse_dates=["signal_time"])
    ta = pd.read_csv(args.ta, parse_dates=["time"])
    cand = cand.sort_values("signal_time").reset_index(drop=True)
    ta = ta.sort_values("time").reset_index(drop=True)
    df = pd.merge_asof(
        cand,
        ta,
        left_on="signal_time",
        right_on="time",
        direction="backward",
        tolerance=pd.Timedelta("10min"),
    )
    df = df.dropna(subset=["support_confluence"]).reset_index(drop=True)
    df["side_sign"] = np.where(df["side"] > 0, 1.0, -1.0)
    df = add_directional(df)
    print(f"candidates={len(cand)} merged={len(df)}  {df.signal_time.min()} .. {df.signal_time.max()}")

    train_end = pd.Timestamp(args.train_end, tz="UTC")
    val_end = pd.Timestamp(args.val_end, tz="UTC")
    train = df[df.signal_time < train_end]
    val = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    test = df[df.signal_time >= val_end]

    print("\n--- baseline ---")
    rows = [line("all", train, val, test)]
    print("\n--- individual aligned patterns ---")
    for name in ["engulf", "hammer", "harami", "piercing", "star", "tweezer", "strong_close"]:
        rows.append(line(f"{name}_align == 1", train[train[f"{name}_align"] == 1], val[val[f"{name}_align"] == 1], test[test[f"{name}_align"] == 1]))
        rows.append(line(f"{name}_against == 1", train[train[f"{name}_against"] == 1], val[val[f"{name}_against"] == 1], test[test[f"{name}_against"] == 1]))

    print("\n--- pattern scores ---")
    for col in ["rev_align", "rev_against", "cont_align", "cont_against"]:
        for th in (1, 2):
            rows.append(line(f"{col} >= {th}", train[train[col] >= th], val[val[col] >= th], test[test[col] >= th]))
    for col in ["support_align", "opposing_confluence"]:
        for th in (1, 2):
            rows.append(line(f"{col} >= {th}", train[train[col] >= th], val[val[col] >= th], test[test[col] >= th]))

    print("\n--- room to next level (ATR) ---")
    for th in (1.0, 1.5, 2.0, 2.5, 3.0, 4.0):
        rows.append(line(f"room_align >= {th:g}", train[train.room_align >= th], val[val.room_align >= th], test[test.room_align >= th]))
        rows.append(line(f"room_align <= {th:g}", train[train.room_align <= th], val[val.room_align <= th], test[test.room_align <= th]))

    print("\n--- breakouts / failed breakouts ---")
    rows.append(line("break_align >= 1", train[train.break_align >= 1], val[val.break_align >= 1], test[test.break_align >= 1]))
    rows.append(line("false_break_against >= 1", train[train.false_break_against >= 1], val[val.false_break_against >= 1], test[test.false_break_against >= 1]))
    rows.append(line("break_align>=1 & cont_align>=1", train[(train.break_align >= 1) & (train.cont_align >= 1)], val[(val.break_align >= 1) & (val.cont_align >= 1)], test[(test.break_align >= 1) & (test.cont_align >= 1)]))

    print("\n--- composite TA score ---")
    for th in (1, 2, 3, 4, 5):
        rows.append(line(f"ta_align >= {th}", train[train.ta_align >= th], val[val.ta_align >= th], test[test.ta_align >= th]))
    for th in (1, 2, 3):
        rows.append(line(f"ta_net >= {th}", train[train.ta_net >= th], val[val.ta_net >= th], test[test.ta_net >= th]))

    print("\n--- combinations ---")
    combos = {
        "rev>=1 + support>=1": lambda x: (x.rev_align >= 1) & (x.support_align >= 1),
        "rev>=1 + opposing==0": lambda x: (x.rev_align >= 1) & (x.opposing_confluence == 0),
        "rev>=1 + room>=2": lambda x: (x.rev_align >= 1) & (x.room_align >= 2),
        "rev>=1 + support>=1 + room>=2": lambda x: (x.rev_align >= 1) & (x.support_align >= 1) & (x.room_align >= 2),
        "support>=1 + room>=2": lambda x: (x.support_align >= 1) & (x.room_align >= 2),
        "opposing==0 + room>=2": lambda x: (x.opposing_confluence == 0) & (x.room_align >= 2),
        "rev>=1 + opposing==0 + room>=2": lambda x: (x.rev_align >= 1) & (x.opposing_confluence == 0) & (x.room_align >= 2),
        "ta_align>=3 + room>=2": lambda x: (x.ta_align >= 3) & (x.room_align >= 2),
        "ta_net>=2 + room>=2": lambda x: (x.ta_net >= 2) & (x.room_align >= 2),
        "ta_net>=2 + opposing==0": lambda x: (x.ta_net >= 2) & (x.opposing_confluence == 0),
        "mode0 rev>=1 + support>=1": lambda x: (x["mode"] == 0) & (x.rev_align >= 1) & (x.support_align >= 1),
        "mode0 rev>=1 + opposing==0": lambda x: (x["mode"] == 0) & (x.rev_align >= 1) & (x.opposing_confluence == 0),
        "mode1 cont>=1 + break>=1": lambda x: (x["mode"] == 1) & (x.cont_align >= 1) & (x.break_align >= 1),
        "mode1 break>=1 + room>=2": lambda x: (x["mode"] == 1) & (x.break_align >= 1) & (x.room_align >= 2),
        "mode1 ta_net>=2": lambda x: (x["mode"] == 1) & (x.ta_net >= 2),
    }
    for name, fn in combos.items():
        rows.append(line(name, train[fn(train)], val[fn(val)], test[fn(test)]))

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
    res.to_csv(args.out, index=False)
    print(f"\nSaved: {args.out}")

    # Descriptive check on the actual tester trades.
    actual_path = Path(args.actual)
    if actual_path.exists():
        actual = pd.read_csv(actual_path, parse_dates=["entry_time"])
        actual["entry_time"] = pd.to_datetime(actual["entry_time"], utc=True)
        actual = actual.sort_values("entry_time").reset_index(drop=True)
        actual = pd.merge_asof(
            actual,
            ta,
            left_on="entry_time",
            right_on="time",
            direction="backward",
            tolerance=pd.Timedelta("10min"),
        )
        actual = actual.dropna(subset=["support_confluence"]).reset_index(drop=True)
        actual["side_sign"] = np.where(actual["side"] == "LONG", 1.0, -1.0)
        actual = add_directional(actual)
        cols = [
            "entry_time", "side", "r", "bull_engulf", "bear_engulf", "hammer", "shooting_star",
            "bull_reversal_score", "bear_reversal_score", "support_confluence", "resistance_confluence",
            "room_to_resistance_atr", "room_to_support_atr", "ta_align", "ta_conflict",
        ]
        actual[cols].to_csv(OUT / "actual_trades_ta.csv", index=False)
        print("\n--- actual trades TA snapshot ---")
        print(actual[["entry_time", "side", "r", "rev_align", "rev_against", "support_align", "opposing_confluence", "room_align", "ta_align", "ta_conflict"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
