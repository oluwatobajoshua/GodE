#!/usr/bin/env python3
"""Explain tester PnL vs chart-filter counterfactual (implementation gap)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
TRADES = HERE / "results" / "tester_trades_fxmopol_gbp_playbook_GBPUSD_H1.csv"
REVIEW = HERE / "results" / "tester_review_fxmopol_gbp_playbook_GBPUSD_H1.json"


def main() -> int:
    if not TRADES.exists():
        print(f"Missing {TRADES}; run tester then fxmopol_tester_review.py")
        return 1
    t = pd.read_csv(TRADES)
    all_net = t["profit"].sum()
    good = t[~t["should_have_skipped"]]
    bad = t[t["should_have_skipped"]]
    print("=== FXMOPOL profit diagnosis (GBP playbook tester) ===")
    print(f"All trades: n={len(t)} net={all_net:.2f}")
    print(f"If chart filters matched research: n={len(good)} net={good['profit'].sum():.2f}")
    print(f"Bad entries only: n={len(bad)} net={bad['profit'].sum():.2f}")
    sl = t[t["exit_reason"] == "stop_loss"]
    print(f"Stop-loss exits: n={len(sl)} net={sl['profit'].sum():.2f} (signal-exit mode reduces these)")
    by = t.groupby("chart_state")["profit"].sum()
    print("\nBy chart_state (net):")
    for k, v in by.items():
        print(f"  {k}: {v:.2f}")
    if REVIEW.exists():
        print("\nStored review:", json.loads(REVIEW.read_text())["losses_from_bad_entries"], "losses from bad entries")
    print("\nSee docs/FXMOPOL_PROFIT_DIAGNOSIS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
