#!/usr/bin/env python3
"""Scalp lane promotion — Python OOS + MT5 tester parity required for london_orb."""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAN = HERE / "results" / "scalp_lane_manifest.json"
CFG = HERE.parent / "configs" / "scalp_lane.json"
OUT = HERE / "results" / "scalp_promotion_verdict.json"
TESTER_MAN = HERE / "results" / "london_orb_tester_manifest.json"
UTC_PICK = HERE / "results" / "london_orb_utc_pick.json"
GROSS = HERE / "results" / "london_orb_gross_net.json"


def tester_parity(sym: str, strat: str, rules: dict) -> dict:
    base = {
        "required": bool(rules.get("require_mt5_parity")),
        "manifest": str(TESTER_MAN),
        "parity_ok": False,
        "mt5_pf": None,
        "mt5_net": None,
        "mt5_trades": None,
        "utc_offset_pinned": None,
        "reasons": [],
    }
    if strat != "london_orb" or sym != "EURUSD":
        base["required"] = False
        base["parity_ok"] = True
        return base
    if not rules.get("require_mt5_parity"):
        base["parity_ok"] = True
        return base
    src = TESTER_MAN
    if UTC_PICK.exists():
        pick = json.loads(UTC_PICK.read_text(encoding="utf-8"))
        if pick.get("pick"):
            p = pick["pick"]
            base["utc_offset_pinned"] = p.get("utc_offset")
            base["mt5_pf"] = p.get("pf")
            base["mt5_net"] = p.get("net_profit")
            base["mt5_trades"] = p.get("trades")
            if pick.get("parity_ok"):
                base["parity_ok"] = True
                return base
    if not TESTER_MAN.exists():
        base["reasons"].append("missing london_orb_tester_manifest (run run_london_orb_promote.ps1)")
        return base
    tm = json.loads(TESTER_MAN.read_text(encoding="utf-8"))
    base["parity_ok"] = bool(tm.get("parity_ok"))
    met = tm.get("mt5_metrics") or {}
    import re

    def f(s: str) -> float:
        return float(re.sub(r"[^\d.\-]", "", str(s).replace(" ", "")) or "0")

    base["mt5_pf"] = f(met.get("Profit Factor", "0"))
    base["mt5_net"] = f(met.get("Total Net Profit", "0"))
    base["mt5_trades"] = int(f(met.get("Total Trades", "0")))
    min_pf = float(rules.get("min_mt5_pf", rules.get("min_oos_pf", 1.05)))
    if base["mt5_net"] <= 0:
        base["reasons"].append(f"MT5 net {base['mt5_net']} <= 0")
        base["parity_ok"] = False
    if base["mt5_pf"] < min_pf:
        base["reasons"].append(f"MT5 PF {base['mt5_pf']} < {min_pf}")
        base["parity_ok"] = False
    if not tm.get("parity_ok"):
        base["reasons"].append(tm.get("parity_note", "tester parity_ok false"))
    return base


def _cfg() -> dict:
    return json.loads(CFG.read_text(encoding="utf-8-sig"))


def main() -> int:
    if not MAN.exists():
        print(f"Run research/scalp_lane_m5.py first ({MAN})")
        return 1
    full = _cfg()
    rules = full["promotion"]
    oos_key = str(rules.get("oos_split_key", "OOS"))
    man = json.loads(MAN.read_text(encoding="utf-8"))
    picks = []
    for sym, block in man.get("symbols", {}).items():
        bar = block.get("bar_meta") or {}
        for strat, data in block.get("strategies", {}).items():
            splits = data.get("splits") or {}
            oos = splits.get(oos_key) or splits.get("OOS") or {}
            val = splits.get("validation") or {}
            n = int(oos.get("n") or 0)
            total = float(oos.get("total_pct") or 0)
            pf = float(oos.get("pf") or 0)
            val_pct = float(val.get("total_pct") or 0)
            val_pf = float(val.get("pf") or 0)
            reasons = []
            if rules.get("require_mt5_tape_for_python") and bar.get("chosen") != "mt5":
                reasons.append(
                    f"Python not on broker M5 (chosen={bar.get('chosen')}); "
                    "run .\\scripts\\run_scalp_mt5_truth.ps1 -Export"
                )
            if rules.get("require_mt5_tester_for_all") and (
                strat != "london_orb" or sym != "EURUSD"
            ):
                reasons.append("no MT5 Strategy Tester certify path (wired: london_orb EURUSD only)")
            if strat == "london_orb" and GROSS.exists():
                gross = json.loads(GROSS.read_text(encoding="utf-8"))
                if not gross.get("gross_viable"):
                    reasons.append(
                        f"london_orb gross kill: {gross.get('diagnosis')} (see london_orb_gross_net.json)"
                    )
            if n < int(rules["min_oos_trades"]):
                reasons.append(f"OOS trades {n} < {rules['min_oos_trades']}")
            if total <= float(rules.get("oos_total_pct_gt", 0)):
                reasons.append(f"OOS total {total}% <= 0")
            if pf < float(rules["min_oos_pf"]) and n >= 30:
                reasons.append(f"OOS PF {pf} < {rules['min_oos_pf']}")
            if rules.get("require_validation_pass") and (val_pct <= 0 or val_pf < rules["min_oos_pf"]):
                reasons.append(f"validation total {val_pct}% pf {val_pf}")

            tpar = tester_parity(sym, strat, rules)
            if tpar["required"] and not tpar["parity_ok"]:
                reasons.extend(tpar.get("reasons") or ["MT5 parity failed"])

            promoted = len(reasons) == 0
            role = "scalp_sleeve" if promoted else "research_only"
            if strat == "london_orb" and sym == "EURUSD" and promoted and tpar["required"]:
                role = "scalp_sleeve_mt5_certified"
            picks.append(
                {
                    "symbol": sym,
                    "strategy": strat,
                    "promoted": promoted,
                    "role": role,
                    "oos_n": n,
                    "oos_total_pct": total,
                    "oos_pf": pf,
                    "reasons": reasons,
                    "mt5_parity": tpar,
                    "ea": "SovereignLondonOrbEA.mq5" if strat == "london_orb" else "SovereignEmaTrendEA.mq5",
                }
            )
    payload = {
        "rule": rules,
        "m5_policy": man.get("m5_policy"),
        "oos_split_key": oos_key,
        "candidates": picks,
        "any_promoted": any(p["promoted"] for p in picks),
    }
    OUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
