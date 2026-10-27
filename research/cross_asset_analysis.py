#!/usr/bin/env python3
"""Evaluate macro / sentiment features on the labelled candidate set.

The split is chronological and fixed before the test is run:
    train  2026-01-01 .. 2026-04-30
    valid  2026-05-01 .. 2026-06-30
    test   2026-07-01 .. 2026-10-31

A feature is only interesting if it improves validation *and* test, not just
the training period.  Quantile cut points come from train only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MODEL = HERE / "models"
OUT = HERE / "results"


def metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    w = d["r_multiple"] > 0
    gp = float(d.loc[w, "r_multiple"].sum())
    gl = float(-d.loc[~w, "r_multiple"].sum())
    return {
        "n": int(len(d)),
        "win": round(float(w.mean() * 100), 1),
        "avg": round(float(d["r_multiple"].mean()), 3),
        "total": round(float(d["r_multiple"].sum()), 1),
        "pf": round(gp / gl, 2) if gl > 0 else 999.0,
    }


def line(name: str, tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame) -> dict:
    a, b, c = metrics(tr), metrics(va), metrics(te)
    print(
        f"{name:<46} TR n={a['n']:4d} w={a['win']:5.1f}% aR={a['avg']:+.3f} tR={a['total']:+7.1f} "
        f"| VA n={b['n']:4d} w={b['win']:5.1f}% aR={b['avg']:+.3f} tR={b['total']:+7.1f} "
        f"| TE n={c['n']:4d} w={c['win']:5.1f}% aR={c['avg']:+.3f} tR={c['total']:+7.1f} PF={c['pf']:.2f}"
    )
    return {"name": name, "train": a, "val": b, "test": c}


def add_directional(df: pd.DataFrame) -> pd.DataFrame:
    side = df["side"].astype(float)
    new = {
        "dxy_align": -side * df["dxy_ret6_atr"],
        "dxy_align12": -side * df["dxy_ret12_atr"],
        "dxy_trend_align": -side * df["dxy_dist_sma50_atr"],
        "vix_align": -side * df["vix_chg6_z"],
        "risk_off_align": -side * df["risk_off"],
        "gold_align": side * df["gold_ret6_atr"],
        "flow_align": side * df["flow_12"],
        "flow48_align": side * df["flow_48"],
        "eur_align": side * df["eur_strength_12"],
        "usd_align": -side * df["usd_strength_12"],
        "dxy_align_lag": -side * df["dxy_ret6_lag1"],
        "flow_align_lag": side * df["flow_12_lag1"],
        "vix_align_lag": -side * df["vix_chg6_lag1"],
    }
    return pd.concat([df, pd.DataFrame(new, index=df.index)], axis=1)


def qbin(
    df: pd.DataFrame,
    col: str,
    train: pd.DataFrame,
    labels: list[str] | None = None,
    q: int = 5,
) -> pd.Series:
    edges = train[col].quantile(np.linspace(0, 1, q + 1)).drop_duplicates().to_numpy().copy()
    if len(edges) < 3:
        return pd.Series("all", index=df.index)
    edges[0] = -np.inf
    edges[-1] = np.inf
    n_bins = len(edges) - 1
    use_labels = labels[:n_bins] if labels is not None else None
    return pd.cut(df[col], bins=edges, labels=use_labels, include_lowest=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", default=str(MODEL / "candidates_M5.csv"))
    parser.add_argument("--cross-asset", default=str(MODEL / "cross_asset_M5.csv"))
    parser.add_argument("--actual", default=str(OUT / "actual_trades_analysis.csv"))
    parser.add_argument("--train-end", default="2026-05-01")
    parser.add_argument("--val-end", default="2026-07-01")
    parser.add_argument("--out", default=str(OUT / "cross_asset_analysis.csv"))
    args = parser.parse_args()

    cand = pd.read_csv(args.candidates, parse_dates=["signal_time"]).sort_values("signal_time")
    ca = pd.read_csv(args.cross_asset, parse_dates=["time"]).sort_values("time")
    df = pd.merge_asof(
        cand,
        ca,
        left_on="signal_time",
        right_on="time",
        direction="backward",
        tolerance=pd.Timedelta("6min"),
    )
    before = len(df)
    df = df.dropna(subset=["dxy_ret6_atr", "vix_chg6_z", "flow_12", "d1_dist_sma200_atr"]).reset_index(drop=True)
    df = add_directional(df)
    print(f"candidates={len(cand)} merged={before} with_macro={len(df)}")
    print(f"range {df.signal_time.min()} .. {df.signal_time.max()}")

    train_end = pd.Timestamp(args.train_end, tz="UTC")
    val_end = pd.Timestamp(args.val_end, tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    rows = [line("baseline", tr, va, te)]
    print("\n--- directional macro booleans ---")
    bool_feats = [
        "dxy_align", "dxy_align12", "dxy_trend_align", "vix_align", "risk_off_align",
        "gold_align", "flow_align", "flow48_align", "eur_align", "usd_align",
        "dxy_align_lag", "flow_align_lag", "vix_align_lag",
    ]
    for col in bool_feats:
        for th in (0.0,):
            name = f"{col} > {th:g}"
            fn = lambda x, c=col, t=th: x[c] > t
            rows.append(line(name, tr[fn(tr)], va[fn(va)], te[fn(te)]))

    print("\n--- macro vote combinations ---")
    tr_vote = (tr["dxy_align"] > 0).astype(int) + (tr["vix_align"] > 0).astype(int) + (tr["flow_align"] > 0).astype(int) + (tr["gold_align"] > 0).astype(int)
    va_vote = (va["dxy_align"] > 0).astype(int) + (va["vix_align"] > 0).astype(int) + (va["flow_align"] > 0).astype(int) + (va["gold_align"] > 0).astype(int)
    te_vote = (te["dxy_align"] > 0).astype(int) + (te["vix_align"] > 0).astype(int) + (te["flow_align"] > 0).astype(int) + (te["gold_align"] > 0).astype(int)
    for th in (2, 3, 4):
        rows.append(line(f"macro_votes >= {th}", tr[tr_vote >= th], va[va_vote >= th], te[te_vote >= th]))
        rows.append(line(f"macro_votes == {th}", tr[tr_vote == th], va[va_vote == th], te[te_vote == th]))

    print("\n--- pre-registered combinations ---")
    combos = {
        "dxy>0 & flow>0": lambda x: (x.dxy_align > 0) & (x.flow_align > 0),
        "dxy>0 & vix>0": lambda x: (x.dxy_align > 0) & (x.vix_align > 0),
        "flow>0 & vix>0": lambda x: (x.flow_align > 0) & (x.vix_align > 0),
        "dxy>0 & flow>0 & vix>0": lambda x: (x.dxy_align > 0) & (x.flow_align > 0) & (x.vix_align > 0),
        "dxy>0 & flow>0 & gold>0": lambda x: (x.dxy_align > 0) & (x.flow_align > 0) & (x.gold_align > 0),
        "dxy_trend>0 & flow>0": lambda x: (x.dxy_trend_align > 0) & (x.flow_align > 0),
        "dxy_trend>0 & vix>0": lambda x: (x.dxy_trend_align > 0) & (x.vix_align > 0),
        "dxy_lag>0 & flow_lag>0": lambda x: (x.dxy_align_lag > 0) & (x.flow_align_lag > 0),
        "dxy_lag>0 & vix_lag>0": lambda x: (x.dxy_align_lag > 0) & (x.vix_align_lag > 0),
        "mode0 dxy>0": lambda x: (x["mode"] == 0) & (x.dxy_align > 0),
        "mode0 flow>0": lambda x: (x["mode"] == 0) & (x.flow_align > 0),
        "mode0 dxy>0 & flow>0": lambda x: (x["mode"] == 0) & (x.dxy_align > 0) & (x.flow_align > 0),
        "mode1 dxy>0": lambda x: (x["mode"] == 1) & (x.dxy_align > 0),
        "mode1 flow>0": lambda x: (x["mode"] == 1) & (x.flow_align > 0),
        "mode1 dxy>0 & flow>0": lambda x: (x["mode"] == 1) & (x.dxy_align > 0) & (x.flow_align > 0),
        "short & dxy>0": lambda x: (x.side < 0) & (x.dxy_align > 0),
        "short & flow>0": lambda x: (x.side < 0) & (x.flow_align > 0),
        "long & dxy>0": lambda x: (x.side > 0) & (x.dxy_align > 0),
        "long & flow>0": lambda x: (x.side > 0) & (x.flow_align > 0),
    }
    for name, fn in combos.items():
        rows.append(line(name, tr[fn(tr)], va[fn(va)], te[fn(te)]))

    print("\n--- quantiles (train edges, test shown) ---")
    for col in ["dxy_align", "dxy_align12", "dxy_trend_align", "vix_align", "flow_align", "flow48_align", "gold_align", "vix_z", "vix_pct"]:
        btr = qbin(tr, col, tr, [f"{col}_Q{i+1}" for i in range(5)])
        bva = qbin(va, col, tr, [f"{col}_Q{i+1}" for i in range(5)])
        bte = qbin(te, col, tr, [f"{col}_Q{i+1}" for i in range(5)])
        for label in btr.dropna().unique():
            rows.append(line(str(label), tr[btr == label], va[bva == label], te[bte == label]))

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

    actual_path = Path(args.actual)
    if actual_path.exists():
        actual = pd.read_csv(actual_path).sort_values("entry_time")
        actual["entry_time"] = pd.to_datetime(actual["entry_time"], utc=True)
        actual = pd.merge_asof(
            actual,
            ca,
            left_on="entry_time",
            right_on="time",
            direction="backward",
            tolerance=pd.Timedelta("6min"),
        )
        actual = actual.dropna(subset=["dxy_ret6_atr", "vix_chg6_z", "flow_12"]).reset_index(drop=True)
        actual["side"] = np.where(actual["side"] == "LONG", 1.0, -1.0)
        actual = add_directional(actual)
        cols = [
            "entry_time", "side", "r", "dxy_align", "dxy_trend_align", "vix_align",
            "flow_align", "gold_align", "risk_off_align", "vix_z",
        ]
        actual[cols].to_csv(OUT / "actual_trades_macro.csv", index=False)
        print("\n--- actual trades macro snapshot ---")
        print(actual[cols].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
