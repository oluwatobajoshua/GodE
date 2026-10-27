#!/usr/bin/env python3
"""Simulate alternative exit policies on reconstructed tester trades.

The input CSV is produced by research/analyze_actual_trades.py.  Each trade is
replayed bar-by-bar from the entry bar using the EA's own initial stop distance
and take-profit distance.  The simulation is intentionally conservative:

* the entry bar itself is skipped, so we never use a high/low that happened
  before the fill;
* if a bar touches both the stop and the target, the stop is assumed first;
* a partial fill is executed at the trigger price, then the remainder is moved
  to break-even (if that policy is enabled);
* trailing stops are moved on bar close, not on intrabar highs.

This makes the comparison useful for deciding what to change in MQL5 rather
than producing an optimistic backtest.
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402

POINT = core.POINT
OUT = HERE / "results"


def replay(
    bars: pd.DataFrame,
    entry_time: pd.Timestamp,
    side: str,
    entry: float,
    sl_dist: float,
    tp_dist: float,
    partial_r: float,
    partial_frac: float,
    be_r: float,
    trail_trigger_r: float,
    trail_distance_r: float,
    time_stop_bars: int,
) -> dict:
    """Return net R and exit reason for one trade under one policy."""
    after = bars[bars["time"] > entry_time]
    if after.empty:
        return {"r": 0.0, "reason": "no_data", "partial": False, "bars": 0}

    long = side == "LONG"
    sign = 1.0 if long else -1.0
    stop = entry - sign * sl_dist
    target = entry + sign * tp_dist
    remaining = 1.0
    realized_r = 0.0
    partial_done = False
    be_done = False
    reason = "time"
    bars_used = 0
    max_r = 0.0

    for _, bar in after.iterrows():
        bars_used += 1
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])

        favorable = (high - entry) * sign
        adverse = (entry - low) * sign
        max_r = max(max_r, favorable / sl_dist)

        # Conservative same-bar ambiguity: stop wins over target.
        hit_stop = (low <= stop) if long else (high >= stop)
        hit_target = (high >= target) if long else (low <= target)

        # A partial is filled at the trigger before the remainder can stop.
        if partial_r > 0 and partial_frac > 0 and not partial_done:
            partial_price = entry + sign * partial_r * sl_dist
            partial_hit = (high >= partial_price) if long else (low <= partial_price)
            if partial_hit:
                realized_r += partial_frac * partial_r
                remaining -= partial_frac
                partial_done = True
                if be_r > 0:
                    stop = entry
                    be_done = True

        if hit_stop:
            realized_r += remaining * ((stop - entry) * sign / sl_dist)
            reason = "be" if partial_done and abs(stop - entry) < 1e-12 else "sl"
            return {"r": realized_r, "reason": reason, "partial": partial_done, "bars": bars_used, "mfe": max_r}

        if hit_target:
            realized_r += remaining * (tp_dist / sl_dist)
            return {"r": realized_r, "reason": "tp", "partial": partial_done, "bars": bars_used, "mfe": max_r}

        # Break-even without a partial.
        if be_r > 0 and not be_done and favorable >= be_r * sl_dist:
            stop = entry
            be_done = True

        # Bar-close trailing stop.
        if trail_trigger_r > 0 and trail_distance_r >= 0 and favorable >= trail_trigger_r * sl_dist:
            if long:
                stop = max(stop, close - trail_distance_r * sl_dist)
            else:
                stop = min(stop, close + trail_distance_r * sl_dist)

        if time_stop_bars > 0 and bars_used >= time_stop_bars:
            realized_r += remaining * ((close - entry) * sign / sl_dist)
            return {"r": realized_r, "reason": "time", "partial": partial_done, "bars": bars_used, "mfe": max_r}

    last = after.iloc[-1]
    realized_r += remaining * ((float(last["close"]) - entry) * sign / sl_dist)
    return {"r": realized_r, "reason": reason, "partial": partial_done, "bars": bars_used, "mfe": max_r}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", default=str(OUT / "actual_trades_analysis.csv"))
    parser.add_argument("--sl-atr", type=float, default=2.0)
    parser.add_argument("--tp-r", type=float, default=3.0)
    parser.add_argument("--max-bars", type=int, default=288)
    parser.add_argument("--out", default=str(OUT / "exit_policy_comparison.csv"))
    args = parser.parse_args()

    t = pd.read_csv(args.trades, parse_dates=["entry_time", "exit_time"])
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True)
    bars = core.load_data("M5")[["time", "open", "high", "low", "close"]].sort_values("time").reset_index(drop=True)

    # Recompute true risk with the actual SL multiple used by the run.
    t["vol_mult"] = np.where(t["atr_ratio"] > 1.3, 1.25, np.where(t["atr_ratio"] < 0.7, 0.85, 1.0))
    t["sl_points_true"] = np.maximum((t["atr"] / POINT) * args.sl_atr * t["vol_mult"], 25.0)
    t["risk_true"] = t["volume"] * t["sl_points_true"]
    t["tp_dist"] = t["sl_points_true"] * POINT * args.tp_r
    t["sl_dist"] = t["sl_points_true"] * POINT

    policies = []
    # Baseline: stop + target only.
    policies.append(
        dict(name="sl_tp", partial_r=0.0, partial_frac=0.0, be_r=0.0, trail_trigger_r=0.0, trail_distance_r=0.0, time_stop_bars=0)
    )
    # Break-even only, at several R thresholds.
    for be in (0.75, 1.0, 1.25, 1.5, 2.0):
        policies.append(
            dict(name=f"be_{be:g}", partial_r=0.0, partial_frac=0.0, be_r=be, trail_trigger_r=0.0, trail_distance_r=0.0, time_stop_bars=0)
        )
    # Partial at several R, remainder to break-even.
    for pr, frac in itertools.product((0.75, 1.0, 1.25, 1.5, 2.0), (0.25, 0.5, 0.75)):
        policies.append(
            dict(name=f"partial_{pr:g}_{int(frac*100)}pct", partial_r=pr, partial_frac=frac, be_r=pr, trail_trigger_r=0.0, trail_distance_r=0.0, time_stop_bars=0)
        )
    # Partial plus a trailing stop on the remainder.
    for pr, tr, td in itertools.product((1.0, 1.5), (1.0, 1.5, 2.0), (0.5, 1.0, 1.5)):
        policies.append(
            dict(name=f"partial_{pr:g}_trail_{tr:g}_{td:g}", partial_r=pr, partial_frac=0.5, be_r=pr, trail_trigger_r=tr, trail_distance_r=td, time_stop_bars=0)
        )
    # Trailing without a partial.
    for tr, td in itertools.product((1.0, 1.5, 2.0), (0.5, 1.0, 1.5)):
        policies.append(
            dict(name=f"trail_{tr:g}_{td:g}", partial_r=0.0, partial_frac=0.0, be_r=0.0, trail_trigger_r=tr, trail_distance_r=td, time_stop_bars=0)
        )
    # Time stops.
    for n in (6, 12, 18, 24, 36, 48, 72):
        policies.append(
            dict(name=f"time_{n}", partial_r=0.0, partial_frac=0.0, be_r=0.0, trail_trigger_r=0.0, trail_distance_r=0.0, time_stop_bars=n)
        )

    rows = []
    for p in policies:
        sims = []
        for _, row in t.iterrows():
            sims.append(
                replay(
                    bars, row.entry_time, row.side, row.entry, row.sl_dist, row.tp_dist,
                    p["partial_r"], p["partial_frac"], p["be_r"],
                    p["trail_trigger_r"], p["trail_distance_r"], p["time_stop_bars"],
                )
            )
        s = pd.DataFrame(sims)
        t["sim_r"] = s["r"].to_numpy()
        t["sim_reason"] = s["reason"].to_numpy()
        t["sim_bars"] = s["bars"].to_numpy()
        t["sim_mfe"] = s["mfe"].to_numpy()
        net = float((t["sim_r"] * t["risk_true"]).sum())
        rows.append(
            {
                "policy": p["name"],
                **p,
                "net_usd": round(net, 2),
                "avg_r": round(float(t["sim_r"].mean()), 4),
                "total_r": round(float(t["sim_r"].sum()), 2),
                "wins": int((t["sim_r"] > 0).sum()),
                "win_pct": round(float((t["sim_r"] > 0).mean() * 100), 1),
                "tp_exits": int((t["sim_reason"] == "tp").sum()),
                "be_exits": int((t["sim_reason"] == "be").sum()),
                "sl_exits": int((t["sim_reason"] == "sl").sum()),
                "time_exits": int((t["sim_reason"] == "time").sum()),
                "avg_mfe": round(float(t["sim_mfe"].mean()), 3),
            }
        )

    res = pd.DataFrame(rows).sort_values("net_usd", ascending=False)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(args.out, index=False)
    print(f"trades={len(t)}  baseline_net={float((t['r']*t['risk_true']).sum()):.2f}")
    print(res.to_string(index=False))
    print(f"\nSaved: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
