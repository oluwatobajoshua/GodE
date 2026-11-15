#!/usr/bin/env python3
"""Unified profit dashboard — stack P&L + carry_mom + lanes (last 12 months).

Run:
    .\\.venv\\Scripts\\python.exe research\\profit_dashboard.py
    .\\.venv\\Scripts\\python.exe research\\profit_dashboard.py --run-stack
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RESULTS = HERE / "results"
OUT_JSON = RESULTS / "profit_dashboard.json"
OUT_CSV = RESULTS / "profit_dashboard_summary.csv"


def _last12_monthly(csv_path: Path, strategy_col: str = "strategy") -> pd.DataFrame:
    if not csv_path.exists():
        return pd.DataFrame()
    df = pd.read_csv(csv_path, parse_dates=["date"])
    if "period" in df.columns:
        df = df[df["period"] == "month"]
    if strategy_col in df.columns and df[strategy_col].nunique() > 1:
        pass
    return df.sort_values("date").tail(12)


def load_carry_last12() -> pd.DataFrame:
    p = RESULTS / "carry_mom_filter_monthly.csv"
    if not p.exists():
        from export_period_results import carry_mom_filter_series, load_monthly_prices, load_rates  # noqa: PLC0415
        from carry import CURRENCIES  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        rates = load_rates()
        spot, price = load_monthly_prices()
        idx = spot.index.intersection(rates.index)
        spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
        diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
        from period_returns import publish_results  # noqa: PLC0415

        publish_results("carry_mom_filter", carry_mom_filter_series(spot, diff, price), already_monthly=True)
    return _last12_monthly(RESULTS / "carry_mom_filter_monthly.csv")


def load_stack_modes() -> dict:
    man = RESULTS / "fxmopol_stack_pnl_manifest.json"
    if not man.exists():
        return {}
    return json.loads(man.read_text(encoding="utf-8")).get("modes", {})


def load_stack_monthly(mode: str, symbol: str = "GBPUSD") -> pd.DataFrame:
    slug = f"stack_{symbol}_mid50_{mode}"
    p = RESULTS / f"{slug}_monthly.csv"
    return _last12_monthly(p) if p.exists() else pd.DataFrame()


def load_lanes() -> dict:
    p = RESULTS / "lanes_summary.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def build_summary_rows() -> list[dict]:
    rows: list[dict] = []
    carry = load_carry_last12()
    for _, r in carry.iterrows():
        rows.append(
            {
                "lane": "carry_mom_filter",
                "horizon": "monthly",
                "date": r["date"],
                "return_pct": r["return_pct"],
                "trades": r.get("trades", 1),
                "equity": r.get("equity"),
            }
        )
    for mode in ("chart_prime", "professor_governor"):
        mdf = load_stack_monthly(mode)
        for _, r in mdf.iterrows():
            rows.append(
                {
                    "lane": f"fxmopol_{mode}",
                    "horizon": "monthly",
                    "date": r["date"],
                    "return_pct": r["return_pct"],
                    "trades": r.get("trades", 0),
                    "equity": r.get("equity"),
                }
            )
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-stack", action="store_true", help="Run fxmopol_stack_pnl.py first")
    p.add_argument("--symbol", default="GBPUSD")
    args = p.parse_args()

    if args.run_stack:
        py = REPO / ".venv" / "Scripts" / "python.exe"
        subprocess.run([str(py), str(HERE / "fxmopol_stack_pnl.py"), "--symbol", args.symbol], check=False)

    lanes = load_lanes()
    stack_modes = load_stack_modes()
    carry12 = load_carry_last12()
    gov12 = load_stack_monthly("professor_governor", args.symbol)
    prime12 = load_stack_monthly("chart_prime", args.symbol)

    print("=== Sovereign profit dashboard (last 12 calendar months) ===\n")

    if not carry12.empty:
        print("--- carry_mom_filter (monthly D1 sleeve) ---")
        print(carry12[["date", "return_pct", "trades", "equity", "win_period"]].to_string(index=False))
        print(f"sum_return%={carry12['return_pct'].sum():.2f}\n")
    else:
        print("--- carry_mom_filter: no data (run export_period_results.py) ---\n")

    for label, df in (("chart_prime", prime12), ("professor_governor", gov12)):
        if df.empty:
            print(f"--- FXMOPOL {label}: no monthly CSV (run fxmopol_stack_pnl.py) ---\n")
            continue
        print(f"--- FXMOPOL GBP H1 stack: {label} ---")
        print(df[["date", "return_pct", "trades", "equity", "win_period"]].to_string(index=False))
        print(f"sum_return%={df['return_pct'].sum():.2f}\n")

    if lanes:
        l1 = lanes.get("lanes", {}).get("lane1_ecn", {})
        print("--- Lane 1 (carry ECN) 3-split ---")
        for row in l1.get("rows", [])[:3]:
            print(f"  cost_pts={row.get('cost_pts')} cagr={row.get('cagr_full')}% profitable_3split={row.get('profitable_3split')}")
        print()

    if stack_modes:
        print("--- GBP stack full-sample (manifest) ---")
        for mode in ("chart_loose", "chart_prime", "professor_governor"):
            m = stack_modes.get(mode, {})
            full = m.get("full", {})
            oos = m.get("splits", {}).get("OOS", {})
            print(
                f"  {mode}: trades={m.get('trades')} full%={full.get('total_pct')} "
                f"OOS n={oos.get('n')} OOS%={oos.get('total_pct')}"
            )

    rows = build_summary_rows()
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    payload = {
        "generated_by": "research/profit_dashboard.py",
        "symbol": args.symbol,
        "carry_last12_sum_pct": float(carry12["return_pct"].sum()) if not carry12.empty else None,
        "stack_modes": stack_modes,
        "lanes_lane1_note": lanes.get("lanes", {}).get("lane1_ecn", {}).get("note"),
        "default_h1_metric": "professor_governor",
        "certified_slow_sleeve": "carry_mom_filter",
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUT_JSON.name}, {OUT_CSV.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
