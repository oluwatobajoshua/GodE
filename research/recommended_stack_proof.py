#!/usr/bin/env python3
"""Certified automation path: promoted Sovereign Stack on carry_mom (not H1 FXMOPOL).

Reads config from research/results/best_win_settings.json, replays monthly returns
on design / validation / OOS, writes a clear profitability verdict JSON.

Run:
    .\\.venv\\Scripts\\python.exe research\\recommended_stack_proof.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from carry import CURRENCIES, load_monthly_prices, load_rates, metrics, split_returns
from period_returns import print_split_monthly, publish_results
from sovereign_stack_tune import Cfg, SPLITS, build_series, cfg_from_key, profit_factor

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "results" / "best_win_settings.json"
VERDICT = HERE / "results" / "recommended_stack_verdict.json"

REFINE_PICK = HERE / "results" / "stack_refine_pick.json"


def promoted_cfg() -> Cfg:
    if REFINE_PICK.exists():
        key = json.loads(REFINE_PICK.read_text(encoding="utf-8"))["config"]
        return cfg_from_key(key)
    if SETTINGS.exists():
        key = json.loads(SETTINGS.read_text(encoding="utf-8"))["monthly_automation"]["stack_overlay"]["config"]
        return cfg_from_key(key)
    return Cfg(12, 4, 18.0, 30.0, 0.75, None, True)

SPLIT_PRINT = {
    "design_2005_2014": ("2005-01-01", "2015-01-01"),
    "validation_2015_2019": ("2015-01-01", "2020-01-01"),
    "OOS_2020_2026": ("2020-01-01", "2030-01-01"),
}


def split_profit_ok(r: pd.Series) -> bool:
    r = r.dropna()
    if len(r) < 12:
        return False
    m = metrics(r, "x")
    pf = profit_factor(r)
    return bool(m["cagr"] > 0 and m["sharpe"] > 0 and pd.notna(pf) and pf >= 1.0)


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    promoted = promoted_cfg()
    series = build_series(spot, diff, price, promoted)
    name = f"recommended_{promoted.key()}"
    publish_results(name, series, already_monthly=True)
    print_split_monthly(name, series, SPLIT_PRINT, already_monthly=True)

    parts = split_returns(series)
    rows = []
    ok_splits = 0
    for sp in SPLITS:
        part = parts[sp]
        m = metrics(part, sp)
        pf = profit_factor(part)
        total_pct = ((1 + part).prod() - 1) * 100 if len(part) else 0.0
        ok = split_profit_ok(part)
        ok_splits += int(ok)
        rows.append(
            {
                "split": sp,
                "months": len(part),
                "total_return_pct": round(total_pct, 2),
                "cagr_pct": m["cagr"],
                "sharpe": m["sharpe"],
                "maxdd_pct": m["maxdd"],
                "profit_factor": round(pf, 2) if pd.notna(pf) else None,
                "profitable_split": ok,
            }
        )
        print(
            f"{sp}: total%={total_pct:.2f} CAGR={m['cagr']:.2f}% "
            f"Sharpe={m['sharpe']:.2f} maxDD={m['maxdd']:.1f}% PF={pf:.2f} ok={ok}"
        )

    full = series.dropna()
    full_m = metrics(full, "full")
    three_split = ok_splits == 3

    settings_note = ""
    if SETTINGS.exists():
        settings_note = json.loads(SETTINGS.read_text(encoding="utf-8")).get("monthly_automation", {}).get(
            "stack_overlay", {}
        ).get("config", "")

    verdict = {
        "strategy": "SOV_CARRY_MOM + Sovereign Stack v1",
        "config": promoted.key(),
        "best_win_settings_config": settings_note,
        "h1_fxmopol_promoted": False,
        "splits": rows,
        "full_period": {
            "months": len(full),
            "cagr_pct": full_m["cagr"],
            "sharpe": full_m["sharpe"],
            "maxdd_pct": full_m["maxdd"],
        },
        "profitable_all_3_splits": three_split,
        "deployable_live": False,
        "deployable_reason": (
            "Project FROZEN; monthly FX premia only — verify MT5 tester + broker swaps; "
            "not steady daily profit; historical max DD ~26%+ on simpler carry variants."
        ),
        "next_commands": [
            ".\\scripts\\run_recommended_stack.ps1 -SkipPlaybook",
            ".\\scripts\\run_gods_eye_tester.ps1 -From 2020.01.01 -To 2026.01.01 -NoVisual",
        ],
    }
    VERDICT.write_text(json.dumps(verdict, indent=2), encoding="utf-8")

    print("\n=== VERDICT ===")
    print(f"Promoted stack ({promoted.key()}): 3-split profitable = {three_split}")
    print(f"Full-period CAGR {full_m['cagr']:.2f}% Sharpe {full_m['sharpe']:.2f} maxDD {full_m['maxdd']:.1f}%")
    print("H1 FXMOPOL: NOT certified (use fxmopol_playbook_d1_compare.csv).")
    print(f"Live deploy allowed by repo policy: {verdict['deployable_live']}")
    print(f"Wrote {VERDICT.name}")
    return 0 if three_split else 1


if __name__ == "__main__":
    raise SystemExit(main())
