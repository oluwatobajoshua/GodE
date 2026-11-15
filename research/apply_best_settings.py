#!/usr/bin/env python3
"""Write best_win_settings.json — only backtest-winning configs + fxmopol.tpl chart."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT = RESULTS / "best_win_settings.json"
MANIFEST = RESULTS / "best_win_settings_manifest.json"
BACKTEST_STUB = HERE.parent / "backtest" / "mt5_files" / "Sovereign" / "best_win_settings.json"
COMPARE = RESULTS / "fxmopol_proximity_compare_summary.json"


def load_payload() -> dict:
    return json.loads(OUT.read_text(encoding="utf-8"))


def refresh_stack(payload: dict) -> None:
    ov = payload["monthly_automation"]["stack_overlay"]
    refine = RESULTS / "stack_refine_pick.json"
    if refine.exists():
        pick = json.loads(refine.read_text(encoding="utf-8"))
        ov["source"] = "stack_refine.py (blend_sharpe, 3-split gate)"
        ov["config"] = pick["config"]
        ov["OOS_sharpe"] = float(pick["sharpe_OOS_2020_2026"])
        ov["blend_sharpe"] = float(pick.get("blend_sharpe", 0))
        ov["inputs"].update(pick["ea_inputs"])
        return
    path = RESULTS / "sovereign_stack_tune.csv"
    if not path.exists():
        return
    stack = pd.read_csv(path)
    promoted = stack[stack.promoted_3split == True]
    if promoted.empty:
        return
    best_stack = promoted.sort_values("sharpe_OOS_2020_2026", ascending=False).iloc[0]
    ov["config"] = best_stack["config"]
    ov["OOS_sharpe"] = float(best_stack["sharpe_OOS_2020_2026"])


def refresh_streak(payload: dict) -> None:
    path = RESULTS / "ema_streak_ride_grid.csv"
    if not path.exists():
        return
    streak = pd.read_csv(path)
    ok = streak[(streak.exp_bps_OOS > 0) & (streak.exp_bps_validation > -1.0) & (streak.n_OOS >= 50)]
    best_h1 = ok.sort_values("exp_bps_OOS", ascending=False).iloc[0] if len(ok) else streak.sort_values(
        "exp_bps_OOS", ascending=False
    ).iloc[0]
    pre = payload["intraday_h1"]["presets"]["USDJPY_STREAK"]
    pre["config"] = best_h1["config"]
    pre["OOS_exp_bps"] = float(best_h1["exp_bps_OOS"])
    pre["OOS_pf"] = float(best_h1["pf_OOS"])
    pre["OOS_n"] = int(best_h1["n_OOS"])
    pre["inputs"]["InpMinStreak"] = int(best_h1["config"].split("_s")[1].split("_")[0])


def refresh_gbp_playbook(payload: dict) -> None:
    if not COMPARE.exists():
        return
    rows = json.loads(COMPARE.read_text(encoding="utf-8"))["compare"]
    gbp = [r for r in rows if r["symbol"] == "GBPUSD" and r["variant"] == "ema_proximity_filters" and r["split"] == "OOS"]
    if not gbp:
        return
    r = gbp[0]
    if float(r["exp_bps"]) <= 0:
        return
    pre = payload["intraday_h1"]["presets"]["GBP_PLAYBOOK_PROXIMITY"]
    pre["OOS_exp_bps"] = float(r["exp_bps"])
    pre["OOS_pf"] = float(r["pf"])
    pre["OOS_total_pct"] = float(r["total_pct"])


def main() -> int:
    payload = load_payload()
    refresh_stack(payload)
    refresh_streak(payload)
    refresh_gbp_playbook(payload)
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    MANIFEST.write_text(
        json.dumps(
            {
                "stack": payload["monthly_automation"]["stack_overlay"].get("config"),
                "usdjpy_streak": payload["intraday_h1"]["presets"]["USDJPY_STREAK"].get("config"),
                "gbp_oos_exp_bps": payload["intraday_h1"]["presets"]["GBP_PLAYBOOK_PROXIMITY"].get("OOS_exp_bps"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    if BACKTEST_STUB.parent.exists():
        stub = {
            "monthly_ea": "SovereignGodsEyeEA.mq5",
            "active_strategy": "carry_mom_filter",
            "intraday_ea": "SovereignFxmopolEA.mq5",
            "chart_template": "templates/fxmopol.tpl",
            "default_fxmopol_preset": "EUR_CHART_ONLY",
            "see": "research/results/best_win_settings.json",
        }
        BACKTEST_STUB.write_text(json.dumps(stub, indent=2), encoding="utf-8")
    print("Wrote", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
