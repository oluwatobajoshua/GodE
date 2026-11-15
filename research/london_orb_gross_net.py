#!/usr/bin/env python3
"""London ORB gross vs net on broker M5 — kill switch before more tuning.

Literature: gross edge often < round-trip spread (Holdout Labs, forexsignals.io).
Run:
    .\\.venv\\Scripts\\python.exe research\\london_orb_gross_net.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from bar_store import load_m5_mt5_only
from scalp_lane_m5 import _metrics, london_orb

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CFG = REPO / "configs" / "scalp_lane.json"
OUT = HERE / "results" / "london_orb_gross_net.json"


def run_zero_cost(df: pd.DataFrame, sym: str, cfg: dict) -> pd.DataFrame:
    import scalp_lane_m5 as sl

    def zcost(_sym: str, _row: pd.Series, _orb: dict) -> float:
        return 0.0

    old = sl._rt_cost
    sl._rt_cost = zcost
    try:
        return london_orb(df, sym, cfg)
    finally:
        sl._rt_cost = old


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    w = cfg.get("bars", {}).get("tester_oos", {})
    t0, t1 = w.get("from", "2024-07-01"), w.get("to", "2026-01-01")
    rows = []
    for sym in ["EURUSD"]:
        m5, meta = load_m5_mt5_only(sym)
        m5 = m5[(m5.index >= pd.Timestamp(t0)) & (m5.index < pd.Timestamp(t1))]
        tr_net = london_orb(m5, sym, cfg)
        tr_gross = run_zero_cost(m5, sym, cfg)
        for label, tr in ("net", tr_net), ("gross", tr_gross):
            if tr.empty:
                m = {"n": 0, "total_pct": 0.0, "pf": 0.0}
            else:
                r = pd.Series(tr["return_pct"].values / 100.0, index=pd.to_datetime(tr["exit_time"]))
                m = _metrics(r)
            rows.append({"sym": sym, "mode": label, **m})
    gross = next(x for x in rows if x["mode"] == "gross")
    net = next(x for x in rows if x["mode"] == "net")
    gross_viable = gross["pf"] >= 1.05 and gross["total_pct"] > 0
    net_viable = net["pf"] >= 1.05 and net["total_pct"] > 0
    verdict = {
        "window": {"from": t0, "to": t1},
        "bar_source": "mt5_only",
        "rows": rows,
        "gross_viable": gross_viable,
        "net_viable": net_viable,
        "diagnosis": (
            "cost_kills_marginal_edge"
            if gross_viable and not net_viable
            else ("no_gross_edge_stop_orb" if not gross_viable else "rare_both_pass")
        ),
        "external_reference": "docs/FX_EXTERNAL_INSIGHTS.md",
        "action": "discontinue_london_orb_primary" if not gross_viable else "cost_filters_only",
    }
    OUT.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    print(json.dumps(verdict, indent=2))
    print(f"\nWrote {OUT}")
    return 0 if net_viable else 1


if __name__ == "__main__":
    raise SystemExit(main())
