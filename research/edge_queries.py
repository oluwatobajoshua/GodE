#!/usr/bin/env python3
"""Focused, pre-registered edge queries on the candidate set.

These are deliberately small in number.  The goal is to see whether a simple
trend-aligned breakout or short-only filter survives validation and test, not to
search hundreds of combinations until one looks good in-sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

MODEL_DIR = HERE / "models"
OUT = HERE / "results"


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["side_sign"] = np.where(df["side"] > 0, 1.0, -1.0)
    df["momentum_align"] = df["side_sign"] * df["ret1_atr"]
    df["d1_align"] = df["side_sign"] * df["d1_dist_sma200_atr"]
    df["h1_align"] = df["side_sign"] * df["h1_dist_sma200_atr"]
    df["h1_di_align"] = df["side_sign"] * df["h1_di_diff_scaled"]
    df["hour"] = ((np.degrees(np.arctan2(df["hour_sin"], df["hour_cos"])) / 15.0) % 24.0).round().astype(int)
    df["weekday"] = ((np.degrees(np.arctan2(df["weekday_sin"], df["weekday_cos"])) / (360.0 / 7.0)) % 7.0).round().astype(int)
    return df


def m(d: pd.DataFrame) -> dict:
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


def line(name: str, tr: pd.DataFrame, va: pd.DataFrame, te: pd.DataFrame) -> None:
    a, b, c = m(tr), m(va), m(te)
    print(
        f"{name:<48} TR n={a['n']:4d} w={a['win']:5.1f}% aR={a['avg']:+.3f} tR={a['total']:+7.1f} "
        f"| VA n={b['n']:4d} w={b['win']:5.1f}% aR={b['avg']:+.3f} tR={b['total']:+7.1f} "
        f"| TE n={c['n']:4d} w={c['win']:5.1f}% aR={c['avg']:+.3f} tR={c['total']:+7.1f} PF={c['pf']:.2f}"
    )


def main() -> int:
    df = pd.read_csv(MODEL_DIR / "candidates_M5.csv", parse_dates=["signal_time"])
    df = add_features(df).sort_values("signal_time").reset_index(drop=True)
    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = df[df.signal_time < train_end]
    va = df[(df.signal_time >= train_end) & (df.signal_time < val_end)]
    te = df[df.signal_time >= val_end]

    print("--- trend / side / engine structure ---")
    for name, fn in {
        "all": lambda x: x.index == x.index,
        "mode1 breakout": lambda x: x["mode"] == 1,
        "mode0 mean-rev": lambda x: x["mode"] == 0,
        "short all": lambda x: x.side_sign < 0,
        "long all": lambda x: x.side_sign > 0,
        "mode1 short": lambda x: (x["mode"] == 1) & (x.side_sign < 0),
        "mode1 long": lambda x: (x["mode"] == 1) & (x.side_sign > 0),
        "mode0 short": lambda x: (x["mode"] == 0) & (x.side_sign < 0),
        "mode0 long": lambda x: (x["mode"] == 0) & (x.side_sign > 0),
        "short & d1_align<0": lambda x: (x.side_sign < 0) & (x.d1_align < 0),
        "short & d1_align<1": lambda x: (x.side_sign < 0) & (x.d1_align < 1),
        "short & h1_align<0": lambda x: (x.side_sign < 0) & (x.h1_align < 0),
        "mode1 short & d1_align<0": lambda x: (x["mode"] == 1) & (x.side_sign < 0) & (x.d1_align < 0),
        "mode1 short & d1_align<1": lambda x: (x["mode"] == 1) & (x.side_sign < 0) & (x.d1_align < 1),
        "mode1 short & h1_di_align>0": lambda x: (x["mode"] == 1) & (x.side_sign < 0) & (x.h1_di_align > 0),
        "mode1 short & momentum>=0": lambda x: (x["mode"] == 1) & (x.side_sign < 0) & (x.momentum_align >= 0),
        "mode1 short & rw>=5": lambda x: (x["mode"] == 1) & (x.side_sign < 0) & (x.range_width_atr >= 5),
        "mode1 short & adx>=25": lambda x: (x["mode"] == 1) & (x.side_sign < 0) & (x.h1_adx_scaled >= 0.5),
        "short & momentum>=0": lambda x: (x.side_sign < 0) & (x.momentum_align >= 0),
        "short & rw>=5": lambda x: (x.side_sign < 0) & (x.range_width_atr >= 5),
        "short & d1<0 & momentum>=0": lambda x: (x.side_sign < 0) & (x.d1_align < 0) & (x.momentum_align >= 0),
        "short & d1<0 & rw>=5": lambda x: (x.side_sign < 0) & (x.d1_align < 0) & (x.range_width_atr >= 5),
        "short & d1<0 & adx>=25": lambda x: (x.side_sign < 0) & (x.d1_align < 0) & (x.h1_adx_scaled >= 0.5),
        "short & d1<0 & box>=0.2": lambda x: (x.side_sign < 0) & (x.d1_align < 0),
        "long & d1_align>0": lambda x: (x.side_sign > 0) & (x.d1_align > 0),
        "long & d1_align>1": lambda x: (x.side_sign > 0) & (x.d1_align > 1),
        "mode1 long & d1_align>0": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.d1_align > 0),
        "mode1 long & d1_align>1": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.d1_align > 1),
        "mode1 long & h1_di_align>0": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.h1_di_align > 0),
        "mode1 long & momentum>=0": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.momentum_align >= 0),
        "mode1 long & adx>=25": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.h1_adx_scaled >= 0.5),
        "mode1 long & rw>=5": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.range_width_atr >= 5),
        "mode1 long & d1>0 & adx>=25": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.d1_align > 0) & (x.h1_adx_scaled >= 0.5),
        "mode1 long & d1>0 & momentum>=0": lambda x: (x["mode"] == 1) & (x.side_sign > 0) & (x.d1_align > 0) & (x.momentum_align >= 0),
        "long & d1>0 & momentum>=0": lambda x: (x.side_sign > 0) & (x.d1_align > 0) & (x.momentum_align >= 0),
    }.items():
        try:
            line(name, tr[fn(tr)], va[fn(va)], te[fn(te)])
        except Exception as e:
            print(f"{name}: {e}")

    print("\n--- entry hour for short-only ---")
    for hr in range(7, 17):
        fn = lambda x, hr=hr: (x.side_sign < 0) & (x.hour == hr)
        line(f"short hour {hr}", tr[fn(tr)], va[fn(va)], te[fn(te)])

    print("\n--- day-of-week for short-only ---")
    for wd in range(5):
        fn = lambda x, wd=wd: (x.side_sign < 0) & (x.weekday == wd)
        line(f"short weekday {wd}", tr[fn(tr)], va[fn(va)], te[fn(te)])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
