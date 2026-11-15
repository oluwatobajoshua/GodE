#!/usr/bin/env python3
"""FXMOPOL autonomous stack — one command, full improve loop.

Order:
  1. Price-action parity (EA vs chart_core)
  2. Playbook backtest (open timing; PA + D1 + proximity)
  3. Session/weekday calendar on trades
  4. Learn filters from baseline (design) + KPIs
  5. Offline adapt from performance CSVs (if present)

Logging: research/results/logs/fxmopol_autonomous.log
Manifest: research/results/fxmopol_autonomous_manifest.json

Run:
    .\\scripts\\run_fxmopol_autonomous.ps1
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from sovereign_stack_log import setup_stack_logger

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "research" / "results"
LOG_DIR = RESULTS / "logs"
OUT_MANIFEST = RESULTS / "fxmopol_autonomous_manifest.json"
STDERR_TAIL = 4000


def _rel_script(script: Path) -> str:
    try:
        return str(script.relative_to(ROOT))
    except ValueError:
        return str(script)


def run_step(logger, name: str, script: Path, *, required: bool) -> dict:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)

    rec: dict = {
        "step": name,
        "required": required,
        "script": _rel_script(script),
        "exit_code": None,
        "elapsed_sec": 0.0,
        "status": "pending",
    }

    if not script.exists():
        rec.update({"status": "error", "error": "missing script", "exit_code": 1})
        logger.error("[%s] missing script %s", name, script)
        return rec

    cwd = str(ROOT / "research") if script.parent.name == "research" else str(ROOT)
    logger.info("========== %s (cwd=%s) ==========", name, cwd)
    t0 = time.time()
    try:
        proc = subprocess.run(
            [str(py), str(script)],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        rec["elapsed_sec"] = round(time.time() - t0, 1)
        rec["exit_code"] = proc.returncode
        if proc.stdout:
            for line in proc.stdout.splitlines():
                logger.info("[%s] %s", name, line)
        if proc.returncode == 0:
            rec["status"] = "ok"
            logger.info("[%s] finished OK in %.1fs", name, rec["elapsed_sec"])
        else:
            rec["status"] = "failed"
            tail = (proc.stderr or proc.stdout or "")[-STDERR_TAIL:]
            rec["stderr_tail"] = tail
            logger.error("[%s] exit=%s stderr_tail:\n%s", name, proc.returncode, tail)
    except Exception as exc:
        rec["elapsed_sec"] = round(time.time() - t0, 1)
        rec["status"] = "exception"
        rec["exit_code"] = 1
        rec["error"] = str(exc)
        rec["traceback"] = traceback.format_exc()
        logger.exception("[%s] subprocess exception", name)

    return rec


def load_json(name: str, logger) -> dict | None:
    p = RESULTS / name
    if not p.exists():
        logger.debug("artifact missing: %s", name)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("invalid json %s: %s", name, exc)
        return None


def main() -> int:
    run_id = uuid.uuid4().hex[:12]
    logger = setup_stack_logger("fxmopol_autonomous", LOG_DIR)
    logger.info("run_id=%s root=%s", run_id, ROOT)

    steps_spec = [
        ("parity", ROOT / "research" / "fxmopol_price_action_parity.py", True),
        ("playbook_backtest", ROOT / "research" / "fxmopol_playbook.py", True),
        ("session_calendar", ROOT / "research" / "fxmopol_session_calendar.py", False),
        ("filter_learn", ROOT / "research" / "fxmopol_filter_learn.py", True),
        ("trade_adapt", ROOT / "python" / "fxmopol_adapt.py", False),
        ("microstructure", ROOT / "research" / "ml" / "microstructure_pipeline.py", False),
        ("stack_pnl", ROOT / "research" / "fxmopol_stack_pnl.py", False),
        ("profit_dashboard", ROOT / "research" / "profit_dashboard.py", False),
        ("h1_promotion", ROOT / "research" / "h1_promotion.py", False),
        ("portfolio_combine", ROOT / "research" / "portfolio_combine.py", False),
        ("ecosystem_professor", ROOT / "python" / "market_ingest.py", False),
        ("prophet_multitf", ROOT / "research" / "prophet_multitf.py", False),
    ]

    log: list[dict] = []
    worst = 0
    failed_required = False

    for name, path, required in steps_spec:
        try:
            rec = run_step(logger, name, path, required=required)
        except Exception:
            rec = {
                "step": name,
                "required": required,
                "status": "exception",
                "exit_code": 1,
                "traceback": traceback.format_exc(),
            }
            logger.exception("unhandled wrapper error for %s", name)
        log.append(rec)
        code = rec.get("exit_code") or 0
        if code != 0:
            worst = max(worst, code if code > 0 else 1)
            if required:
                failed_required = True
                logger.error("required step %s failed — continuing for learning artifacts", name)

    manifest = {
        "run_id": run_id,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "log_file": str((LOG_DIR / "fxmopol_autonomous.log").relative_to(ROOT)),
        "failed_required": failed_required,
        "steps": log,
        "artifacts": {
            "playbook_summary": "fxmopol_playbook_summary.csv",
            "learned_filters": "fxmopol_learned_filters.json",
            "filter_learn_summary": "fxmopol_filter_learn_summary.csv",
            "session_calendar": "fxmopol_session_calendar.json",
            "trade_learn": "fxmopol_trade_learn.json",
            "price_action_parity": "fxmopol_price_action_parity.json",
            "market_snapshot": "market_snapshot.json",
        },
        "learned_kpis": load_json("fxmopol_learned_filters.json", logger),
        "playbook_manifest": load_json("fxmopol_playbook_manifest.json", logger),
    }

    try:
        OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info("wrote manifest %s", OUT_MANIFEST)
    except OSError as exc:
        logger.exception("could not write manifest: %s", exc)
        worst = max(worst, 1)

    learned = manifest.get("learned_kpis") or {}
    if isinstance(learned, dict) and "symbols" in learned:
        for sym, data in learned["symbols"].items():
            bk = data.get("baseline_kpis", {})
            fk = data.get("filtered_kpis_all", {})
            logger.info(
                "KPI %s: baseline loss%%=%s payoff=%s | filtered loss%%=%s payoff=%s",
                sym,
                bk.get("loss_rate"),
                bk.get("payoff_ratio"),
                fk.get("loss_rate"),
                fk.get("payoff_ratio"),
            )

    logger.info("done worst_exit=%s failed_required=%s", worst, failed_required)
    return 1 if failed_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
