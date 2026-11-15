#!/usr/bin/env python3
"""Certify strategies against configs/strategy_quality_bar.json.

Outputs:
  research/results/strategy_certification_verdict.json
  configs/good_strategies.json  (human router)

Run:
    .\\.venv\\Scripts\\python.exe research\\strategy_certification.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CFG = REPO / "configs" / "strategy_quality_bar.json"
OUT = HERE / "results" / "strategy_certification_verdict.json"
ROUTER = REPO / "configs" / "good_strategies.json"

CARRY_V = HERE / "results" / "carry_promotion_verdict.json"
SCALP_MAN = HERE / "results" / "scalp_lane_manifest.json"
SCALP_V = HERE / "results" / "scalp_promotion_verdict.json"
H1_V = HERE / "results" / "h1_promotion_verdict.json"
ORB_MAN = HERE / "results" / "london_orb_tester_manifest.json"
DISCOVERY = HERE / "results" / "discovery_proof.csv"
DEMO = HERE / "results" / "forward_demo_manifest.json"


def load_cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8"))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def tier_block(*, research: bool, desk: bool, live: bool, reasons: list[str], gates: dict) -> dict:
    return {
        "research_good": research,
        "desk_good": desk and research,
        "live_good": live and desk and research,
        "reasons": reasons,
        "gates_passed": gates,
    }


def certify_carry(gates: dict) -> dict:
    v = load_json(CARRY_V)
    reasons: list[str] = []
    g_passed: dict[str, bool] = {}
    if not v:
        reasons.append("missing carry_promotion_verdict.json")
        return tier_block(research=False, desk=False, live=False, reasons=reasons, gates=g_passed)

    splits = v.get("splits") or {}
    req = gates.get("splits_required", [])
    min_pf = float(gates.get("min_pf_per_split", 1.0))
    for sp in req:
        s = splits.get(sp) or {}
        ok = float(s.get("sum_pct", 0)) > float(gates.get("min_sum_pct_per_split", 0)) and float(
            s.get("pf", 0)
        ) >= min_pf
        g_passed[f"split_{sp}"] = ok
        if not ok:
            reasons.append(f"{sp}: sum_pct/pf fail")

    research = bool(v.get("promoted_3split")) and all(g_passed.values())
    g_passed["promoted_3split"] = bool(v.get("promoted_3split"))

    desk_rules = gates.get("desk_good") or {}
    oos = splits.get("OOS_2020_2026") or {}
    desk_ok = (
        float(oos.get("sharpe", 0)) >= float(desk_rules.get("min_oos_sharpe", 0))
        and float(oos.get("cagr", 0)) >= float(desk_rules.get("min_oos_cagr_pct", 0))
        and float(oos.get("max_dd", 0)) > float(desk_rules.get("max_oos_dd_pct", -100))
    )
    g_passed["desk_oos_sharpe_cagr_dd"] = desk_ok
    if research and not desk_ok:
        reasons.append(
            f"desk gate: OOS sharpe {oos.get('sharpe')} cagr {oos.get('cagr')} dd {oos.get('max_dd')}"
        )

    live = False
    if DEMO.exists():
        demo = load_json(DEMO)
        live = bool(demo.get("carry_mom_filter", {}).get("forward_pass"))
        if not live:
            reasons.append("forward_demo_manifest: carry not forward_pass")
    elif research:
        reasons.append("live_good requires forward_demo_manifest.json (not run)")

    return {
        "sleeve_id": "carry_mom_filter",
        "family": gates.get("family", "d1_carry"),
        "ea": "SovereignGodsEyeEA.mq5",
        "chart": "D1",
        **tier_block(research=research, desk=research and desk_ok, live=live, reasons=reasons, gates=g_passed),
        "evidence": {"carry_promotion_verdict": str(CARRY_V), "splits": splits},
    }


def certify_scalp_candidate(sym: str, strat: str, gates: dict, cand: dict, bar: dict) -> dict:
    reasons: list[str] = []
    g_passed: dict[str, bool] = {}

    chosen = bar.get("chosen")
    g_passed["bar_mt5_only"] = chosen == gates.get("bar_source", "mt5_only")
    if not g_passed["bar_mt5_only"]:
        reasons.append(f"bar source {chosen} != mt5_only")

    n = int(cand.get("oos_n") or 0)
    total = float(cand.get("oos_total_pct") or 0)
    pf = float(cand.get("oos_pf") or 0)
    g_passed["min_trades"] = n >= int(gates["min_oos_trades"])
    g_passed["min_pf"] = pf >= float(gates["min_oos_pf"])
    g_passed["min_total_pct"] = total > float(gates["min_oos_total_pct"])
    if not g_passed["min_trades"]:
        reasons.append(f"OOS trades {n} < {gates['min_oos_trades']}")
    if not g_passed["min_pf"]:
        reasons.append(f"OOS PF {pf} < {gates['min_oos_pf']}")
    if not g_passed["min_total_pct"]:
        reasons.append(f"OOS total {total}% <= 0")

    py_ok = all(g_passed.get(k) for k in ("bar_mt5_only", "min_trades", "min_pf", "min_total_pct"))

    mt5 = cand.get("mt5_parity") or {}
    if gates.get("require_mt5_parity") and sym == "EURUSD" and strat == "london_orb":
        g_passed["mt5_parity"] = bool(mt5.get("parity_ok"))
        mt5_pf = float(mt5.get("mt5_pf") or 0)
        mt5_net = float(mt5.get("mt5_net") or 0)
        g_passed["mt5_pf"] = mt5_pf >= float(gates["min_mt5_pf"])
        g_passed["mt5_net"] = mt5_net > float(gates["min_mt5_net"])
        if not g_passed.get("mt5_parity"):
            reasons.extend(mt5.get("reasons") or ["MT5 parity failed"])
        research = py_ok and all(g_passed.get(k) for k in ("mt5_parity", "mt5_pf", "mt5_net"))
    else:
        g_passed["mt5_parity"] = True
        reasons.append("scalp MT5 certify wired only for EURUSD london_orb")
        research = False

    desk_extra = gates.get("desk_good_extra") or {}
    min_monthly = int(desk_extra.get("min_trades_per_month_oos", 8))
    # approximate from manifest if present
    desk_ok = research
    man = load_json(SCALP_MAN)
    sym_block = (man.get("symbols") or {}).get(sym, {})
    strat_block = (sym_block.get("strategies") or {}).get(strat, {})
    oos_key = gates.get("oos_split_key", "tester_oos")
    oos_n = int((strat_block.get("splits") or {}).get(oos_key, {}).get("n") or n)
    months = 18  # tester_oos default span approx
    tpm = oos_n / max(months, 1)
    g_passed["trades_per_month"] = tpm >= min_monthly
    if research and tpm < min_monthly:
        reasons.append(f"trades/month ~{tpm:.1f} < {min_monthly}")
        desk_ok = False

    live = False
    if DEMO.exists():
        key = f"{sym}_{strat}"
        live = bool(load_json(DEMO).get(key, {}).get("forward_pass"))

    return {
        "sleeve_id": f"{sym}:{strat}",
        "family": gates.get("family"),
        **tier_block(research=research, desk=desk_ok, live=live, reasons=reasons, gates=g_passed),
        "evidence": {"scalp_promotion_candidate": cand},
    }


def certify_scalp(gates: dict) -> list[dict]:
    v = load_json(SCALP_V)
    man = load_json(SCALP_MAN)
    out = []
    for cand in v.get("candidates", []):
        sym = cand.get("symbol", "")
        strat = cand.get("strategy", "")
        bar = ((man.get("symbols") or {}).get(sym) or {}).get("bar_meta") or {}
        out.append(certify_scalp_candidate(sym, strat, gates, cand, bar))
    return out


def certify_h1(gates: dict) -> dict:
    v = load_json(H1_V)
    reasons: list[str] = []
    g_passed: dict[str, bool] = {}
    promoted = bool(v.get("promoted")) or v.get("role") == gates.get("promoted_role")
    g_passed["promoted"] = promoted
    if not promoted:
        reasons.append(f"role {v.get('role')} not promoted")
    n = int(v.get("oos_n") or 0)
    pf = float(v.get("oos_pf") or 0)
    g_passed["min_trades"] = n >= int(gates["min_oos_trades"])
    g_passed["min_pf"] = pf >= float(gates["min_oos_pf"])
    research = promoted and g_passed["min_trades"] and g_passed["min_pf"]
    return {
        "sleeve_id": v.get("symbol", "GBPUSD") + ":h1_fxmopol",
        "family": gates.get("family"),
        **tier_block(research=research, desk=False, live=False, reasons=reasons, gates=g_passed),
        "evidence": v,
    }


def certify_discovery(gates: dict) -> list[dict]:
    if not DISCOVERY.exists():
        return []
    d = pd.read_csv(DISCOVERY)
    req = gates.get("splits_required", [])
    min_pf = float(gates["min_pf"])
    min_sh = float(gates["min_sharpe"])
    max_dd = float(gates["max_dd_pct"])
    out = []
    for hyp, g in d.groupby("hypothesis"):
        if not all(sp in set(g["split"]) for sp in req):
            continue
        reasons = []
        g_passed = {}
        ok = True
        for sp in req:
            row = g[g["split"] == sp].iloc[0]
            pf = float(row["pf"]) if pd.notna(row["pf"]) else 0.0
            sh = float(row["sharpe"]) if pd.notna(row["sharpe"]) else 0.0
            dd = float(row["maxdd"]) if pd.notna(row["maxdd"]) else -100.0
            st = str(row["status"]) == "pass" if gates.get("status_must_be_pass") else True
            part = pf >= min_pf and sh >= min_sh and dd >= max_dd and st
            g_passed[sp] = part
            if not part:
                ok = False
                reasons.append(f"{sp} fail pf/sh/dd/status")
        if not ok:
            continue
        out.append(
            {
                "sleeve_id": f"discovery:{hyp}",
                "family": gates.get("family"),
                **tier_block(research=True, desk=False, live=False, reasons=reasons, gates=g_passed),
            }
        )
    return out


def main() -> int:
    bar = load_cfg()
    gates = bar.get("gates") or {}

    sleeves: list[dict] = []
    sleeves.append(certify_carry(gates.get("carry_monthly") or {}))
    sleeves.extend(certify_scalp(gates.get("scalp_intraday") or {}))
    sleeves.append(certify_h1(gates.get("h1_overlay") or {}))
    sleeves.extend(certify_discovery(gates.get("discovery_daily") or {}))

    research_good = [s for s in sleeves if s.get("research_good")]
    desk_good = [s for s in sleeves if s.get("desk_good")]
    live_good = [s for s in sleeves if s.get("live_good")]

    router = {
        "schema_version": bar.get("schema_version", 1),
        "definition": bar.get("definition_one_liner"),
        "frozen_live_policy": bar.get("frozen_live_policy", True),
        "research_good": [s["sleeve_id"] for s in research_good],
        "desk_good": [s["sleeve_id"] for s in desk_good],
        "live_good": [s["sleeve_id"] for s in live_good],
        "trade_in_mt5_default": research_good[0]["sleeve_id"] if research_good else None,
        "tiers_explained": bar.get("labels"),
    }

    payload = {
        "config": str(CFG),
        "generated_from": {
            "carry": str(CARRY_V),
            "scalp": str(SCALP_V),
            "h1": str(H1_V),
            "discovery": str(DISCOVERY),
        },
        "tiers": bar.get("tiers"),
        "sleeves": sleeves,
        "summary": router,
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    ROUTER.write_text(json.dumps(router, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2))
    print(f"\nWrote {OUT}\nWrote {ROUTER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
