#!/usr/bin/env python3
"""Time-of-day / session analysis with the corrected hour mapping.

The earlier hour filter code decoded the cyclic hour feature with an extra
+12 shift, so any old "hour 7" result was actually hour 19.  This script
recomputes the real server/UTC hour from the candidate data and checks whether
a time window is stable across train, validation and test instead of only
looking profitable in one slice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODEL = HERE / "models"
OUT = HERE / "results"


def hour_from_cyclic(sin_values: pd.Series, cos_values: pd.Series) -> pd.Series:
    return ((np.degrees(np.arctan2(sin_values, cos_values)) / 15.0) % 24.0).round().astype(int)


def metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0}
    wins = d["r"] > 0
    return {
        "n": int(len(d)),
        "win": round(float(wins.mean() * 100.0), 1),
        "avg": round(float(d["r"].mean()), 3),
        "total": round(float(d["r"].sum()), 1),
    }


def main() -> int:
    cand = pd.read_csv(MODEL / "candidates_M5.csv", parse_dates=["signal_time"])
    cand["hour"] = hour_from_cyclic(cand["hour_sin"], cand["hour_cos"])
    cand["month"] = cand["signal_time"].dt.strftime("%Y-%m")
    cand["r"] = cand["r_multiple"]
    cand["session"] = pd.cut(
        cand["hour"],
        bins=[-1, 6, 11, 16, 23],
        labels=["Asia 0-6", "London 7-11", "NY 12-16", "Late 17-23"],
    )

    train_end = pd.Timestamp("2026-05-01", tz="UTC")
    val_end = pd.Timestamp("2026-07-01", tz="UTC")
    tr = cand[cand.signal_time < train_end]
    va = cand[(cand.signal_time >= train_end) & (cand.signal_time < val_end)]
    te = cand[cand.signal_time >= val_end]

    print("=== actual candidate hour check ===")
    print(cand[["signal_time", "hour", "hour_sin", "hour_cos"]].head(4).to_string(index=False))
    print(f"\nrows={len(cand)}  train={len(tr)} val={len(va)} test={len(te)}")

    print("\n=== hour stability (fixed 3R/1R candidate exits) ===")
    print(f"{'hour':>4} | {'train n':>7} {'avgR':>7} | {'val n':>6} {'avgR':>7} | {'test n':>6} {'avgR':>7} {'win%':>5} | stable?")
    rows = []
    for hour in range(24):
        a = metrics(tr[tr.hour == hour])
        b = metrics(va[va.hour == hour])
        c = metrics(te[te.hour == hour])
        stable = (a["n"] >= 10 and b["n"] >= 5 and c["n"] >= 10 and
                  a["avg"] > 0 and b["avg"] > 0 and c["avg"] > 0)
        rows.append({"hour": hour, **{f"train_{k}": v for k, v in a.items()},
                     **{f"val_{k}": v for k, v in b.items()},
                     **{f"test_{k}": v for k, v in c.items()},
                     "stable": stable})
        print(f"{hour:>4} | {a['n']:>7} {a['avg']:>+7.3f} | {b['n']:>6} {b['avg']:>+7.3f} "
              f"| {c['n']:>6} {c['avg']:>+7.3f} {c['win']:>5.1f} | {'YES' if stable else ''}")
    hour_df = pd.DataFrame(rows)
    hour_df.to_csv(OUT / "time_of_day_hours.csv", index=False)

    print("\n=== session stability ===")
    for session in cand.session.cat.categories:
        a = metrics(tr[tr.session == session])
        b = metrics(va[va.session == session])
        c = metrics(te[te.session == session])
        print(f"{session:<13} TR n={a['n']:4d} aR={a['avg']:+.3f} | VA n={b['n']:3d} aR={b['avg']:+.3f} "
              f"| TE n={c['n']:4d} aR={c['avg']:+.3f} win={c['win']:5.1f}%")

    print("\n=== hour stability by engine ===")
    for mode in (0, 1):
        print(f"\n-- mode {mode} --")
        print(f"{'hour':>4} | {'train n':>7} {'avgR':>7} | {'val n':>6} {'avgR':>7} | {'test n':>6} {'avgR':>7}")
        for hour in range(24):
            a = metrics(tr[(tr.hour == hour) & (tr["mode"] == mode)])
            b = metrics(va[(va.hour == hour) & (va["mode"] == mode)])
            c = metrics(te[(te.hour == hour) & (te["mode"] == mode)])
            if a["n"] + b["n"] + c["n"] == 0:
                continue
            print(f"{hour:>4} | {a['n']:>7} {a['avg']:>+7.3f} | {b['n']:>6} {b['avg']:>+7.3f} "
                  f"| {c['n']:>6} {c['avg']:>+7.3f}")

    print("\n=== month x hour net R (all candidates) ===")
    pivot = cand.pivot_table(index="hour", columns="month", values="r", aggfunc="sum").fillna(0.0)
    print(pivot.round(1).to_string())

    # Robustness: combine only hours that are positive in train, validation and test.
    good_hours = hour_df[
        (hour_df.train_n >= 10) & (hour_df.val_n >= 5) & (hour_df.test_n >= 10) &
        (hour_df.train_avg > 0) & (hour_df.val_avg > 0) & (hour_df.test_avg > 0)
    ].hour.tolist()
    print(f"\n=== hours positive in all three splits ===\n{good_hours}")
    if good_hours:
        mask = cand.hour.isin(good_hours)
        for name, d in [("train", tr), ("val", va), ("test", te)]:
            m = metrics(d[d.hour.isin(good_hours)])
            print(f"{name:<5} n={m['n']:4d} win={m['win']:5.1f}% avgR={m['avg']:+.3f} totalR={m['total']:+.1f}")

    # Check whether the best hours survive a different exit policy.
    policy_path = OUT / "candidate_exit_policies.csv"
    if policy_path.exists():
        pol = pd.read_csv(policy_path)
        base = pol[pol.policy == "base_tp3"].copy()
        base["hour"] = hour_from_cyclic(base["signal_time"].map(lambda x: np.sin(2*np.pi*pd.Timestamp(x).hour/24.0)),
                                        base["signal_time"].map(lambda x: np.cos(2*np.pi*pd.Timestamp(x).hour/24.0)))
        # simpler: derive directly from timestamp
        base["hour"] = pd.to_datetime(base["signal_time"], utc=True).dt.hour
        print("\n=== exit-policy check: top test hours (base vs time_48) ===")
        for hour in [7, 8, 9, 10, 11, 12, 13, 14, 15, 16]:
            b = base[(base.hour == hour) & (base.signal_time >= str(val_end))]
            t48 = pol[(pol.policy == "time_48")].copy()
            t48["hour"] = pd.to_datetime(t48["signal_time"], utc=True).dt.hour
            c = t48[(t48.hour == hour) & (t48.signal_time >= str(val_end))]
            print(f"hour {hour:>2}: base n={len(b):3d} aR={b.exit_r.mean():+.3f} | time_48 n={len(c):3d} aR={c.exit_r.mean():+.3f}")

    print(f"\nSaved: {OUT / 'time_of_day_hours.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
