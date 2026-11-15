#!/usr/bin/env python3
"""Compare MT5 tester trades vs Python chart_prime stack (rule alignment).

Run after tester:
    .\\.venv\\Scripts\\python.exe research\\ea_stack_parity_check.py

Inputs:
  research/results/tester_trades_fxmopol_gbp_playbook_GBPUSD_H1.csv
  research/results/tester_review_fxmopol_gbp_playbook_GBPUSD_H1.json
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TRADES = HERE / "results" / "tester_trades_fxmopol_gbp_playbook_GBPUSD_H1.csv"
REVIEW = HERE / "results" / "tester_review_fxmopol_gbp_playbook_GBPUSD_H1.json"
OUT = HERE / "results" / "ea_stack_parity_report.json"
AUDIT = HERE / "results" / "mt5_data_plane_audit.json"


def python_stack_oos_trades() -> pd.DataFrame:
    from fxmopol_chart_core import DEFAULT_WEEKDAY_MASK  # noqa: PLC0415
    from fxmopol_playbook import SPLITS, load_h1, replay_trades  # noqa: PLC0415

    h1 = load_h1("GBPUSD")
    tr = replay_trades(
        h1,
        ema_mid=50,
        symbol="GBPUSD",
        good_sessions_only=True,
        weekday_mask=DEFAULT_WEEKDAY_MASK,
    )
    if tr.empty:
        return tr
    et = pd.to_datetime(tr["entry_time"])
    a, b = SPLITS["OOS"]
    return tr[(et >= pd.Timestamp(a)) & (et < pd.Timestamp(b))]


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--run-tester", action="store_true")
    p.add_argument("--from", dest="date_from", default="2024.07.01")
    p.add_argument("--to", dest="date_to", default="2026.01.01")
    args = p.parse_args()

    if args.run_tester:
        ps1 = REPO / "scripts" / "run_fxmopol_tester.ps1"
        if ps1.exists():
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
                "-Profile",
                "fxmopol_gbp_playbook",
                "-From",
                args.date_from,
                "-To",
                args.date_to,
                "-NoVisual",
            ]
            print("Running MT5 tester...", " ".join(cmd))
            subprocess.run(cmd, cwd=str(REPO), check=False)
            review = REPO / "research" / "fxmopol_tester_review.py"
            report = REPO / "backtest" / "reports" / "fxmopol_gbp_playbook_GBPUSD_H1.htm"
            if review.exists() and report.exists():
                subprocess.run([sys.executable, str(review), str(report)], cwd=str(REPO), check=False)

    py_oos = python_stack_oos_trades()
    report: dict = {
        "python_chart_prime_oos_trades": len(py_oos),
        "python_oos_net_pct": float(py_oos["return_pct"].sum()) if len(py_oos) else 0.0,
        "tester_trades_csv": str(TRADES),
        "tester_review_json": str(REVIEW),
    }

    if TRADES.exists():
        t = pd.read_csv(TRADES)
        report["tester_n"] = len(t)
        report["tester_net_profit"] = float(t["profit"].sum()) if "profit" in t.columns else None
        if "should_have_skipped" in t.columns:
            bad = t[t["should_have_skipped"]]
            good = t[~t["should_have_skipped"]]
            report["tester_bad_entries_n"] = len(bad)
            report["tester_bad_net"] = float(bad["profit"].sum())
            report["tester_aligned_n"] = len(good)
            report["tester_aligned_net"] = float(good["profit"].sum())
    else:
        report["tester_note"] = "No tester CSV — run with --run-tester (MT5 required)"

    if REVIEW.exists():
        report["stored_review"] = json.loads(REVIEW.read_text(encoding="utf-8"))

    report["parity_ok"] = (
        report.get("tester_bad_entries_n", 0) == 0 or report.get("tester_aligned_net", 0) > report.get("tester_bad_net", 0)
    )
    if AUDIT.exists():
        try:
            audit = json.loads(AUDIT.read_text(encoding="utf-8"))
            report["data_plane"] = {
                "go": audit.get("data_plane_go"),
                "canonical_bar_source": audit.get("canonical_bar_source"),
            }
            report["parity_ok"] = bool(report["parity_ok"] and audit.get("data_plane_go"))
        except json.JSONDecodeError:
            report["data_plane_note"] = "audit json unreadable"
    else:
        report["data_plane_note"] = "run scripts/sync_mt5_data_plane.ps1"

    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
