#!/usr/bin/env python3
"""GBP H1 sleep profile — one brain refresh + morning analyst report.

Attach: GBPUSD H1, SovereignFxmopolEA, PRESET_GBP_PLAYBOOK, fxmopol.tpl
Daemons (optional overnight): .\\scripts\\start_century_stack.ps1

Run:
    .\\.venv\\Scripts\\python.exe python\\sovereign_sleep_stack.py prepare
    .\\.venv\\Scripts\\python.exe python\\sovereign_sleep_stack.py morning
    .\\.venv\\Scripts\\python.exe python\\sovereign_sleep_stack.py full
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
RESULTS = REPO / "research" / "results"
LOG_DIR = RESULTS / "logs"
MANIFEST = RESULTS / "sovereign_sleep_gbp_h1_manifest.json"
PROFILE_DOC = REPO / "configs" / "sovereign_sleep_gbp_h1.json"

SLEEP_PROFILE = {
    "name": "sovereign_sleep_gbp_h1",
    "symbol": "GBPUSD",
    "period": "H1",
    "ea": "SovereignFxmopolEA.mq5",
    "preset": "PRESET_GBP_PLAYBOOK",
    "chart_template": "fxmopol.tpl",
    "brain_files": [
        "market_snapshot.json",
        "prophet_forecasts.json",
        "microstructure_manifest.json",
        "century_directives.json",
        "fxmopol_trade_learn.json",
    ],
    "tester_profile": "fxmopol_gbp_playbook",
    "research_only": True,
}


def _run(script: Path, *, cwd: Path | None = None) -> tuple[int, float]:
    exe = PY if PY.exists() else Path(sys.executable)
    t0 = time.time()
    proc = subprocess.run(
        [str(exe), str(script)],
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, round(time.time() - t0, 1)


def prepare() -> dict:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    steps: list[dict] = []
    for name, script in (
        ("horizon_sklearn", REPO / "research" / "ml" / "horizon_forecast_sklearn.py"),
        ("prophet", REPO / "research" / "prophet_multitf.py"),
        ("microstructure", REPO / "research" / "ml" / "microstructure_pipeline.py"),
        ("professor", REPO / "python" / "market_ingest.py"),
        ("stack_pnl", REPO / "research" / "fxmopol_stack_pnl.py"),
        ("adapt", REPO / "python" / "fxmopol_adapt.py"),
    ):
        if not script.exists():
            steps.append({"step": name, "status": "skip", "reason": "missing script"})
            continue
        cwd = REPO / "research" if script.parent.name == "research" else REPO
        code, sec = _run(script, cwd=cwd)
        steps.append({"step": name, "exit_code": code, "elapsed_sec": sec, "status": "ok" if code == 0 else "failed"})
    return steps


def morning(symbol: str = "GBPUSD") -> str:
    brief_script = REPO / "python" / "prophet_analyst_brief.py"
    if brief_script.exists():
        exe = PY if PY.exists() else Path(sys.executable)
        proc = subprocess.run(
            [str(exe), str(brief_script), "--symbol", symbol],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        text = proc.stdout or ""
    else:
        text = "(prophet_analyst_brief.py missing)"

    lines = [text, "", "=== Sleep profile checklist ==="]
    for rel in SLEEP_PROFILE["brain_files"]:
        p = REPO / "backtest" / "mt5_files" / "Sovereign" / rel
        rp = RESULTS / rel if rel == "market_snapshot.json" else p
        ok = p.exists() or (RESULTS / rel).exists()
        lines.append(f"  [{'OK' if ok else 'MISSING'}] {rel}")

    monthly = RESULTS / "prophet_GBPUSD_H1_monthly.csv"
    if monthly.exists():
        import pandas as pd

        md = pd.read_csv(monthly)
        if len(md):
            tail = md.tail(6)
            lines.append("")
            lines.append("Prophet GBPUSD H1 — last 6 months (research proxy):")
            for _, row in tail.iterrows():
                lines.append(f"  {row.get('date')} return%={row.get('return_pct')}")
    log_path = LOG_DIR / "fxmopol_autonomous.log"
    if log_path.exists():
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
        lines.append("")
        lines.append("Autonomous log (last 8 lines):")
        lines.extend(f"  {ln}" for ln in tail)

    lines.append("")
    lines.append("Live: attach GBPUSD H1, apply fxmopol.tpl, EA preset GBP_PLAYBOOK, InpEcosystemProfessor=true")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Sovereign sleep stack GBP H1")
    p.add_argument("mode", choices=("prepare", "morning", "full"), nargs="?", default="full")
    args = p.parse_args()

    run_id = uuid.uuid4().hex[:12]
    rec: dict = {
        "run_id": run_id,
        "profile": SLEEP_PROFILE,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if args.mode in ("prepare", "full"):
        rec["prepare"] = prepare()
    if args.mode in ("morning", "full"):
        report = morning()
        rec["morning_report"] = report
        print(report)

    if PROFILE_DOC.exists():
        rec["config"] = json.loads(PROFILE_DOC.read_text(encoding="utf-8"))
    MANIFEST.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    print(f"\n[sleep] manifest -> {MANIFEST}")
    failed = any(s.get("exit_code", 0) not in (0, None) for s in rec.get("prepare", []))
    return 1 if failed and args.mode != "morning" else 0


if __name__ == "__main__":
    raise SystemExit(main())
