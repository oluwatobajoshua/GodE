#!/usr/bin/env python3
"""Mine interpretable no-trade rules from historical Sovereign candidates.

The output is a small decision tree. Leaves with negative training expectancy
become hard veto regions. The tree is evaluated on validation and test months
before any rule is exported to MQL5.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor, export_text

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import candidates  # noqa: E402

MODEL_DIR = HERE / "models"
OUT = HERE / "results"


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "win": 0.0, "avg_r": 0.0, "total_r": 0.0, "pf": 0.0}
    wins = df["r_multiple"] > 0
    gp = df.loc[wins, "r_multiple"].sum()
    gl = -df.loc[~wins, "r_multiple"].sum()
    return {
        "n": int(len(df)),
        "win": round(float(wins.mean() * 100), 2),
        "avg_r": round(float(df["r_multiple"].mean()), 4),
        "total_r": round(float(df["r_multiple"].sum()), 3),
        "pf": round(float(gp / gl), 3) if gl > 0 else 999.0,
    }


def evaluate_mask(name: str, part: pd.DataFrame, keep: np.ndarray) -> dict:
    kept = part[keep]
    skipped = part[~keep]
    result = {
        "name": name,
        "kept": metrics(kept),
        "skipped": metrics(skipped),
        "skip_pct": round(float((~keep).mean() * 100), 2) if len(part) else 0.0,
    }
    print(
        f"{name:>10}: kept n={result['kept']['n']:4d} win={result['kept']['win']:5.1f}% "
        f"avgR={result['kept']['avg_r']:+.3f} totalR={result['kept']['total_r']:+.1f} "
        f"PF={result['kept']['pf']:.2f} | skipped n={result['skipped']['n']:4d} "
        f"avgR={result['skipped']['avg_r']:+.3f} totalR={result['skipped']['total_r']:+.1f}"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=3)
    parser.add_argument("--leaf", type=int, default=100)
    parser.add_argument("--train-end", default="2026-05-01")
    parser.add_argument("--val-end", default="2026-07-01")
    parser.add_argument("--skip-below", type=float, default=-0.10)
    args = parser.parse_args()

    path = MODEL_DIR / "candidates_M5.csv"
    df = pd.read_csv(path, parse_dates=["signal_time"])
    df["signal_time"] = pd.to_datetime(df["signal_time"], utc=True)
    df = df.sort_values("signal_time").reset_index(drop=True)

    train_end = pd.Timestamp(args.train_end, tz="UTC")
    val_end = pd.Timestamp(args.val_end, tz="UTC")
    train = df[df["signal_time"] < train_end]
    val = df[(df["signal_time"] >= train_end) & (df["signal_time"] < val_end)]
    test = df[df["signal_time"] >= val_end]
    print(f"candidates={len(df)} train={len(train)} val={len(val)} test={len(test)}")

    X_cols = candidates.FEATURE_NAMES
    tree = DecisionTreeRegressor(
        max_depth=args.depth,
        min_samples_leaf=args.leaf,
        random_state=7,
        ccp_alpha=0.0,
    )
    tree.fit(train[X_cols], train["r_multiple"])

    train_pred = tree.predict(train[X_cols])
    val_pred = tree.predict(val[X_cols])
    test_pred = tree.predict(test[X_cols])

    print("\n--- tree ---")
    print(export_text(tree, feature_names=X_cols, decimals=3))
    leaves = pd.DataFrame(
        {
            "leaf": tree.apply(train[X_cols]),
            "r": train["r_multiple"].to_numpy(),
        }
    )
    leaf_stats = leaves.groupby("leaf").agg(n=("r", "size"), avg_r=("r", "mean"), total_r=("r", "sum"))
    print("\n--- train leaves ---")
    print(leaf_stats.sort_values("avg_r", ascending=False).to_string())

    print("\n--- baseline / filter ---")
    evaluate_mask("train", train, np.ones(len(train), dtype=bool))
    evaluate_mask("val", val, np.ones(len(val), dtype=bool))
    evaluate_mask("test", test, np.ones(len(test), dtype=bool))

    keep_train = train_pred >= args.skip_below
    keep_val = val_pred >= args.skip_below
    keep_test = test_pred >= args.skip_below
    baseline = {
        "train": metrics(train),
        "val": metrics(val),
        "test": metrics(test),
    }
    filtered = {
        "train": evaluate_mask("train", train, keep_train),
        "val": evaluate_mask("val", val, keep_val),
        "test": evaluate_mask("test", test, keep_test),
    }

    # Export only the negative-expectancy leaves as a compact rule set.
    leaf_centers = []
    for leaf in sorted(leaf_stats.index):
        mask = tree.apply(train[X_cols]) == leaf
        if mask.sum() == 0:
            continue
        leaf_centers.append(
            {
                "leaf": int(leaf),
                "n_train": int(mask.sum()),
                "impurity": float(tree.tree_.impurity[leaf]),
                "value": float(tree.tree_.value[leaf][0][0]),
            }
        )
    payload = {
        "features": X_cols,
        "depth": args.depth,
        "min_samples_leaf": args.leaf,
        "skip_below": args.skip_below,
        "tree": tree.tree_.__getstate__(),
        "leaf_values": [float(v) for v in tree.tree_.value[:, 0, 0]],
        "thresholds": [float(v) for v in tree.tree_.threshold],
        "children_left": [int(v) for v in tree.tree_.children_left],
        "children_right": [int(v) for v in tree.tree_.children_right],
        "feature": [int(v) for v in tree.tree_.feature],
        "baseline": baseline,
        "filtered": filtered,
        "leaf_centers": leaf_centers,
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    (MODEL_DIR / "no_trade_tree.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {MODEL_DIR / 'no_trade_tree.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
