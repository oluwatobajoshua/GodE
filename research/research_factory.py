#!/usr/bin/env python3
"""Study → train → test factory (carry core + GBP H1 satellite).

Single entry point for weekly research. No live deployment.

Run:
    .\\.venv\\Scripts\\python.exe research\\research_factory.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PY = REPO / ".venv" / "Scripts" / "python.exe"
RESULTS = HERE / "results"
LOG = RESULTS / "research_factory_manifest.json"
ACTIVE = REPO / "configs" / "research_hypothesis_active.json"


def run_step(name: str, script: Path, *args: str) -> dict:
    if not script.exists():
        return {"step": name, "status": "skip", "reason": "missing"}
    cmd = [str(PY if PY.exists() else sys.executable), str(script), *args]
    proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "step": name,
        "status": "ok" if proc.returncode == 0 else "failed",
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout[-2000:] if proc.stdout else "",
        "stderr_tail": proc.stderr[-500:] if proc.stderr else "",
    }


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    steps = [
        ("study_pip_budget", HERE / "daily_pip_budget.py", ()),
        ("train_filter_learn", HERE / "fxmopol_filter_learn.py", ("--baseline", "prime")),
        ("test_stack_pnl", HERE / "fxmopol_stack_pnl.py", ("--symbol", "GBPUSD")),
        ("test_h1_promotion", HERE / "h1_promotion.py", ()),
        ("train_carry_grid", HERE / "carry_train_grid.py", ()),
        ("test_carry_promotion", HERE / "carry_promotion.py", ()),
        ("report_dashboard", HERE / "profit_dashboard.py", ()),
        ("test_portfolio_combine", HERE / "portfolio_combine.py", ()),
        ("parity_check", HERE / "ea_stack_parity_check.py", ()),
        ("strategy_certification", HERE / "strategy_certification.py", ()),
        ("profitable_sleeve", HERE / "profitable_sleeve.py", ()),
    ]
    log = []
    for name, script, args in steps:
        log.append(run_step(name, script, *args))

    carry_v = json.loads((RESULTS / "carry_promotion_verdict.json").read_text(encoding="utf-8")) if (
        RESULTS / "carry_promotion_verdict.json"
    ).exists() else {}
    carry_pick = json.loads((RESULTS / "carry_train_pick.json").read_text(encoding="utf-8")) if (
        RESULTS / "carry_train_pick.json"
    ).exists() else {}
    h1_v = json.loads((RESULTS / "h1_promotion_verdict.json").read_text(encoding="utf-8")) if (
        RESULTS / "h1_promotion_verdict.json"
    ).exists() else {}

    active = {
        "primary_hypothesis": "carry_mom_filter",
        "secondary_hypothesis": "GBPUSD_H1_chart_prime_professor_governor",
        "primary_verdict": carry_v.get("promoted_3split"),
        "secondary_verdict": h1_v.get("role"),
        "allocation_model": "portfolio_combine (85/15 core/satellite)",
        "next_action_carry": "optimize DD/cost on design only" if carry_v.get("promoted_3split") else "fix carry splits",
        "next_action_h1": "MT5 parity + grow OOS n" if h1_v.get("role") == "overlay_only" else "re-test",
        "carry_train_pick": carry_pick.get("pick"),
        "carry_train_val_oos_pass": carry_pick.get("val_oos_still_pass"),
    }
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE.write_text(json.dumps(active, indent=2), encoding="utf-8")

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "steps": log,
        "active_hypotheses": active,
    }
    LOG.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== Research factory complete ===")
    print(f"Primary (carry) 3-split: {active.get('primary_verdict')}")
    print(f"Secondary (H1) role:     {active.get('secondary_verdict')}")
    print(f"Config: {ACTIVE}")
    print(f"Log:    {LOG}")
    failed = [s for s in log if s.get("status") == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
