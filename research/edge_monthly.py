#!/usr/bin/env python3
"""Monthly stability check for a discovered conditional edge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402
import edge_scan  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly edge stability")
    parser.add_argument("--condition", default="range+asian")
    parser.add_argument("--side", default="short", choices=["long", "short"])
    parser.add_argument("--horizon", type=int, default=96)
    parser.add_argument("--timeframe", default="M5")
    args = parser.parse_args()

    df = core.load_data(args.timeframe)
    df = edge_scan.add_regime(df)
    conditions = edge_scan.add_conditions(df)
    if args.condition not in conditions:
        print(f"Unknown condition. Examples: {', '.join(list(conditions)[:20])}")
        return 1

    mask = conditions[args.condition]
    atr_pts = df["atr"] / core.POINT
    cost = 11.0 / atr_pts
    entry = df["open"].shift(-1)
    exit_ = df["close"].shift(-args.horizon)
    if args.side == "long":
        ret = (exit_ - entry) / df["atr"] - cost
    else:
        ret = (entry - exit_) / df["atr"] - cost
    valid = mask & ret.notna() & (atr_pts > 0)
    x = df.loc[valid, ["time"]].copy()
    x["edge"] = ret[valid]
    x["win"] = x["edge"] > 0
    x["month"] = x["time"].dt.strftime("%Y-%m")
    x["hour"] = df.loc[valid, "hour"].to_numpy()

    print(f"condition={args.condition} side={args.side} horizon={args.horizon} bars")
    print(
        x.groupby("month")
        .agg(n=("edge", "size"), edge=("edge", "mean"), win=("win", "mean"), total=("edge", "sum"))
        .assign(win=lambda d: d["win"] * 100)
        .to_string()
    )
    print("\nBy hour:")
    print(
        x.groupby("hour")
        .agg(n=("edge", "size"), edge=("edge", "mean"), win=("win", "mean"))
        .assign(win=lambda d: d["win"] * 100)
        .to_string()
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
