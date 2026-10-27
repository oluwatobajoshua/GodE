#!/usr/bin/env python3
"""Does scheduled event risk explain or filter the M5 candidate edge?

The free FXMacroData calendar covers only the last ~90 days, so this is a
*test-split-only* screening exercise, not a full train/validation/test study.
The calendar itself is point-in-time safe (the schedule is known in advance);
risk sentiment is not, so its results are labelled exploratory.

The important question is not "does a high-impact day look good in one slice?"
but "does the same event condition help across the strategy, and is it broad
enough to survive multiple testing?"
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

EVENT_DAY_COLS = [
    "is_cpi_day",
    "is_nfp_day",
    "is_fomc_day",
    "is_pce_day",
    "is_gdp_day",
    "is_retail_day",
    "is_ppi_day",
    "is_claims_day",
    "is_confidence_day",
]


def hour_from_cyclic(sin_values: pd.Series, cos_values: pd.Series) -> pd.Series:
    return ((np.degrees(np.arctan2(sin_values, cos_values)) / 15.0) % 24.0).round().astype(int)


def metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0, "days": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0}
    wins = d["r_multiple"] > 0
    gross_profit = float(d.loc[wins, "r_multiple"].sum())
    gross_loss = float(-d.loc[~wins, "r_multiple"].sum())
    return {
        "n": int(len(d)),
        "days": int(d["signal_time"].dt.date.nunique()),
        "win": round(float(wins.mean() * 100), 1),
        "avg": round(float(d["r_multiple"].mean()), 3),
        "total": round(float(d["r_multiple"].sum()), 1),
        "pf": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0,
    }


def add_row(
    rows: list[dict],
    scope: str,
    label: str,
    frame: pd.DataFrame,
    note: str = "",
) -> None:
    m = metrics(frame)
    rows.append(
        {
            "scope": scope,
            "filter": label,
            "n": m["n"],
            "days": m["days"],
            "win_pct": m["win"],
            "avg_r": m["avg"],
            "total_r": m["total"],
            "pf": m["pf"],
            "note": note,
        }
    )


def show(scope: str, label: str, frame: pd.DataFrame, note: str = "") -> None:
    m = metrics(frame)
    print(
        f"{scope:<18} {label:<30} n={m['n']:4d} d={m['days']:3d} "
        f"win={m['win']:5.1f}% avgR={m['avg']:+.3f} totalR={m['total']:+7.1f} "
        f"PF={m['pf']:6.2f}" + (f"  {note}" if note else "")
    )


def evaluate_scope(
    df: pd.DataFrame,
    scope: str,
    base: pd.DataFrame,
    rows: list[dict],
) -> None:
    print(f"\n=== {scope} ===")
    show(scope, "baseline", base)
    add_row(rows, scope, "baseline", base)

    if "any_high_impact" not in base:
        return

    for label, mask in (
        ("high-impact day", base["any_high_impact"]),
        ("no high-impact day", ~base["any_high_impact"]),
    ):
        show(scope, label, base[mask])
        add_row(rows, scope, label, base[mask])

    for col in EVENT_DAY_COLS:
        if col not in base:
            continue
        subset = base[base[col]]
        if subset.empty:
            continue
        show(scope, col, subset)
        add_row(rows, scope, col, subset)

    # Event proximity, tested on the whole covered test set.
    for minutes in (60, 120, 240):
        for phase in ("pre", "post"):
            col = f"hi_{phase}_{minutes}m"
            if col not in base:
                continue
            subset = base[base[col]]
            if subset.empty:
                continue
            show(scope, col, subset)
            add_row(rows, scope, col, subset)

    # Coarse distance to the next high-impact event.
    if "next_hi_minutes" in base:
        nxt = base["next_hi_minutes"]
        buckets = {
            "next_hi_<=60m": nxt <= 60,
            "next_hi_60-240m": (nxt > 60) & (nxt <= 240),
            "next_hi_240-360m": (nxt > 240) & (nxt <= 360),
            "next_hi_>360m": nxt > 360,
        }
        for label, mask in buckets.items():
            subset = base[mask]
            if subset.empty:
                continue
            show(scope, label, subset)
            add_row(rows, scope, label, subset)

    if "risk_regime_prev" in base:
        for regime in ("risk_on", "neutral", "risk_off"):
            subset = base[base["risk_regime_prev"] == regime]
            if subset.empty:
                continue
            show(scope, regime, subset, note="risk data not PIT-safe")
            add_row(rows, scope, regime, subset, note="risk data not PIT-safe")


def main() -> int:
    path = MODEL / "candidates_events_M5.csv"
    if not path.exists():
        print(f"[FAIL] missing {path}; run research/event_features.py")
        return 1

    df = pd.read_csv(path, parse_dates=["signal_time"])
    df["signal_time"] = pd.to_datetime(df["signal_time"], utc=True)
    df["hour"] = hour_from_cyclic(df["hour_sin"], df["hour_cos"])
    df["r_multiple"] = pd.to_numeric(df["r_multiple"], errors="coerce")
    df = df.dropna(subset=["r_multiple", "signal_time"])

    test = df[df["signal_time"] >= "2026-07-01"].copy()
    covered = test[test["calendar_covered"].fillna(False)].copy()
    hour7 = covered[(covered["mode"] == 1) & (covered["hour"] == 7)].copy()
    mode1 = covered[covered["mode"] == 1].copy()
    mode0 = covered[covered["mode"] == 0].copy()

    print("Event-risk screening")
    print(
        f"calendar-covered test rows={len(covered):,}  "
        f"high-impact days={covered.loc[covered['any_high_impact'], 'signal_time'].dt.date.nunique()}"
    )
    print(
        f"hour-7 breakout: n={len(hour7)} days={hour7['signal_time'].dt.date.nunique()} "
        "| only test-split coverage, so this is exploratory"
    )

    rows: list[dict] = []
    evaluate_scope(df, "all test", covered, rows)
    evaluate_scope(mode1, "mode1 breakout", mode1, rows)
    evaluate_scope(mode0, "mode0 mean-rev", mode0, rows)
    evaluate_scope(hour7, "mode1 hour7", hour7, rows)

    out = pd.DataFrame(rows)
    out.to_csv(OUT / "event_risk.csv", index=False)
    with (OUT / "event_risk.txt").open("w", encoding="utf-8") as handle:
        handle.write("Event-risk screening (test split only; free 90-day calendar)\n")
        handle.write(
            f"calendar-covered test rows={len(covered):,}; "
            f"hour-7 breakout n={len(hour7)}\n\n"
        )
        handle.write(out.to_string(index=False))
        handle.write("\n")
    print(f"\n[OK] wrote {OUT / 'event_risk.txt'} and event_risk.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
