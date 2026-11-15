#!/usr/bin/env python3
"""Fast M5 hypothesis scan on broker tape only — find something worth MT5 optimization.

Does not promote; writes research/results/mt5_m5_hypothesis_scan.csv
"""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from bar_store import load_m5_mt5_only
from scalp_lane_m5 import london_orb, _metrics
from m5_execution_cost import round_trip_cost_price

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CFG = REPO / "configs" / "scalp_lane.json"
OUT = HERE / "results" / "mt5_m5_hypothesis_scan.csv"


def session_momentum(df: pd.DataFrame, sym: str, hour: int, hold: int, orb: dict) -> pd.DataFrame:
    trades = []
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    for i in range(len(df) - hold - 2):
        t = df.index[i]
        if t.hour != hour:
            continue
        side = 1 if c.iloc[i] > o.iloc[i] else -1
        entry_i = i + 1
        if entry_i >= len(df):
            break
        entry = float(df.iloc[entry_i]["open"])
        cost = round_trip_cost_price(sym, df.iloc[entry_i], slippage_pts=float(orb.get("slippage_points", 1)))
        exit_i = min(entry_i + hold, len(df) - 1)
        exit_px = float(df.iloc[exit_i]["close"])
        pnl = side * (exit_px - entry) - cost
        trades.append({"exit_time": df.index[exit_i], "return_pct": pnl / entry * 100.0})
    return pd.DataFrame(trades)


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    orb = cfg["london_orb"]
    w = cfg.get("bars", {}).get("tester_oos", {})
    t0, t1 = w.get("from", "2024-07-01"), w.get("to", "2026-01-01")
    rows = []
    for sym in cfg.get("symbols", ["EURUSD"]):
        try:
            m5, meta = load_m5_mt5_only(sym.upper())
        except FileNotFoundError as e:
            print(e)
            continue
        m5 = m5[(m5.index >= pd.Timestamp(t0)) & (m5.index < pd.Timestamp(t1))]
        for box_h in ([5, 6], [6, 7], [7, 8]):
            for tp_r in (1.0, 1.2, 1.5):
                loc = {**cfg, "london_orb": {**orb, "box_hours_utc": box_h, "tp_r_multiple": tp_r}}
                tr = london_orb(m5, sym, loc)
                if tr.empty:
                    continue
                r = pd.Series(tr["return_pct"].values / 100.0, index=pd.to_datetime(tr["exit_time"]))
                m = _metrics(r)
                rows.append(
                    {
                        "sym": sym,
                        "hypothesis": "london_orb",
                        "box_hours": str(box_h),
                        "tp_r": tp_r,
                        **m,
                    }
                )
        for hour, hold in product((7, 8, 9), (6, 12, 24)):
            tr = session_momentum(m5, sym, hour, hold, orb)
            if tr.empty:
                continue
            r = pd.Series(tr["return_pct"].values / 100.0, index=pd.to_datetime(tr["exit_time"]))
            m = _metrics(r)
            rows.append({"sym": sym, "hypothesis": f"session_mom_h{hour}_hold{hold}", **m})
    if not rows:
        print("No broker M5 — run .\\scripts\\run_scalp_lane.ps1 -Export")
        return 1
    df = pd.DataFrame(rows).sort_values(["pf", "total_pct"], ascending=False)
    df.to_csv(OUT, index=False)
    print(df.head(15).to_string(index=False))
    print(f"\nWrote {OUT} (best PF on broker tester_oos window)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
