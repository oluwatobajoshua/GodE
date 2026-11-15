#!/usr/bin/env python3
"""H1 swing (literature: 1-7 day holds beat M5 ORB on majors) — broker H1 tape.

SMA 20/50 cross + 5-bar hold, spread proxy 1 pip RT on majors.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bar_store import load_h1, load_h1_optional
from period_returns import print_split_monthly, publish_results

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = HERE / "results" / "swing_h1_lane.json"
CFG = REPO / "configs" / "swing_h1_lane.json"

SPLITS = {
    "tester_oos": ("2024-07-01", "2026-01-01"),
    "design": ("2020-01-01", "2024-07-01"),
}


def _metrics(r: pd.Series) -> dict:
    if r.empty:
        return {"n": 0, "total_pct": 0.0, "pf": 0.0}
    wins = r[r > 0]
    losses = r[r <= 0]
    gp, gl = wins.sum(), -losses.sum()
    return {
        "n": int(len(r)),
        "total_pct": round(float(r.sum() * 100), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else 0.0,
    }


def swing_sma(df: pd.DataFrame, sym: str, *, hold_bars: int = 5, cost_pips: float = 1.0) -> pd.DataFrame:
    c = df["close"].astype(float)
    pip = 0.01 if "JPY" in sym else 0.0001
    cost = cost_pips * pip
    fast = c.rolling(20).mean()
    slow = c.rolling(50).mean()
    sig = (fast > slow).astype(int) - (fast < slow).astype(int)
    trades = []
    i = 51
    while i < len(df) - hold_bars - 1:
        side = int(sig.iloc[i - 1])
        if side == 0:
            i += 1
            continue
        entry = float(df.iloc[i]["open"])
        exit_px = float(df.iloc[i + hold_bars]["close"])
        pnl = side * (exit_px - entry) - cost
        trades.append(
            {
                "exit_time": df.index[i + hold_bars],
                "return_pct": pnl / entry * 100.0,
                "strategy": "h1_sma_swing",
            }
        )
        i += hold_bars
    return pd.DataFrame(trades)


def main() -> int:
    syms = ["EURUSD", "GBPUSD"]
    if CFG.exists():
        syms = json.loads(CFG.read_text(encoding="utf-8-sig")).get("symbols", syms)
    payload = {"symbols": {}, "note": "Research successor to M5 ORB — not EA-wired yet"}
    for sym in syms:
        try:
            df = load_h1(sym)
        except FileNotFoundError:
            df = load_h1_optional(sym, "histdata")
            if df is None:
                continue
        tr = swing_sma(df, sym)
        if tr.empty:
            continue
        r = pd.Series(tr["return_pct"].values / 100.0, index=pd.to_datetime(tr["exit_time"]))
        tag = f"swing_h1_{sym}"
        publish_results(tag, r)
        print_split_monthly(tag, r, SPLITS)
        row = {"trades": len(tr), "splits": {}, "full": _metrics(r)}
        for name, (a, b) in SPLITS.items():
            part = r[(r.index >= pd.Timestamp(a)) & (r.index < pd.Timestamp(b))]
            row["splits"][name] = _metrics(part)
        payload["symbols"][sym] = row
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
