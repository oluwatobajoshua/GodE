#!/usr/bin/env python3
"""Compare FXMOPOL playbook with vs without EMA distance filters — monthly/daily proof.

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_proximity_compare.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fxmopol_playbook import SPLITS, backtest, load_h1, metrics
from period_returns import publish_results

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "fxmopol_proximity_compare_summary.json"
COMPARE_MONTHLY = HERE / "results" / "fxmopol_proximity_compare_monthly.csv"


def run_sym(sym: str, ema_mid: int) -> dict:
    import fxmopol_playbook as pb

    h1 = load_h1(sym)
    rows = []
    series = {}
    for label, use_f in (("baseline_no_proximity", False), ("ema_proximity_filters", True)):
        pb.USE_PROXIMITY_FILTERS = use_f
        r = backtest(h1, ema_mid=ema_mid)
        name = f"playbook_{sym}_mid{ema_mid}_{label}"
        series[label] = r
        publish_results(name, r)
        for split, (a, b) in SPLITS.items():
            part = r[(r.index >= a) & (r.index < b)]
            rows.append({"symbol": sym, "variant": label, "split": split, **metrics(part)})
    return {"symbol": sym, "metrics": rows, "series": series}


def merge_monthly(sym: str, ema_mid: int) -> None:
    base = pd.read_csv(HERE / "results" / f"playbook_{sym}_mid{ema_mid}_baseline_no_proximity_monthly.csv")
    prox = pd.read_csv(HERE / "results" / f"playbook_{sym}_mid{ema_mid}_ema_proximity_filters_monthly.csv")
    base["variant"] = "baseline"
    prox["variant"] = "proximity"
    both = pd.concat([base, prox], ignore_index=True)
    if COMPARE_MONTHLY.exists():
        old = pd.read_csv(COMPARE_MONTHLY)
        old = old[old["symbol"] != sym]
        both["symbol"] = sym
        both = pd.concat([old, both], ignore_index=True)
    else:
        both["symbol"] = sym
    both.to_csv(COMPARE_MONTHLY, index=False)


def main() -> int:
    all_metrics = []
    for sym, mid in (("EURUSD", 50), ("GBPUSD", 50), ("USDJPY", 100)):
        try:
            res = run_sym(sym, mid)
            all_metrics.extend(res["metrics"])
            merge_monthly(sym, mid)
        except FileNotFoundError as e:
            print(f"Skip {sym} (no H1 data): {e}")
    OUT.write_text(json.dumps({"compare": all_metrics}, indent=2), encoding="utf-8")

    print("=== Did proximity filters improve edge? (OOS focus) ===")
    df = pd.DataFrame(all_metrics)
    for sym in df["symbol"].unique():
        sub = df[(df["symbol"] == sym) & (df["split"] == "OOS")]
        print(f"\n{sym} OOS:")
        print(sub[["variant", "n", "exp_bps", "pf", "winrate", "total_pct"]].to_string(index=False))
        b = sub[sub["variant"] == "baseline_no_proximity"]
        p = sub[sub["variant"] == "ema_proximity_filters"]
        if len(b) and len(p):
            better = p.iloc[0]["exp_bps"] > b.iloc[0]["exp_bps"]
            print(f"  -> exp_bps improved: {better} ({b.iloc[0]['exp_bps']} -> {p.iloc[0]['exp_bps']})")
    print(f"\nWrote {OUT}, {COMPARE_MONTHLY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
