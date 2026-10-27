#!/usr/bin/env python3
"""Rerun the research suite on independent HistData candles.

The Deriv-Demo data may be a B-book feed with synthetic spread.  This runner
points every research script at `research/data/histdata/` and writes results to
`research/results/histdata/` so the original results are preserved.

Usage:
    .\\.venv\\Scripts\\python.exe research/run_independent_validation.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "histdata"
OUT = HERE / "results" / "histdata"

SCRIPTS = [
    "h1_trend.py",
    "h1_trend_portfolio.py",
    "h1_meanrev.py",
    "d1_trend_portfolio.py",
    "d1_momentum.py",
    "cot_sentiment.py",
    "carry.py",
]


def main() -> int:
    if not DATA.exists() or not any(DATA.glob("*_H1.csv")):
        raise SystemExit(f"no independent data in {DATA}; run download_histdata.py first")
    OUT.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["SOVEREIGN_DATA_DIR"] = str(DATA)
    env["SOVEREIGN_RESULTS_DIR"] = str(OUT)
    # HistData's 2026 archive is not available yet, so the H1 scripts use 2024
    # as the design year and 2025 as the untouched out-of-sample year.
    env["SOVEREIGN_DESIGN_YEAR"] = "2024"

    for script in SCRIPTS:
        path = HERE / script
        if not path.exists():
            print(f"[SKIP] {script} missing")
            continue
        log = OUT / f"{path.stem}.txt"
        print(f"\n=== {script} ===")
        with log.open("w", encoding="utf-8") as fh:
            proc = subprocess.run(
                [sys.executable, str(path)],
                cwd=str(HERE.parent),
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                text=True,
            )
        text = log.read_text(encoding="utf-8", errors="replace")
        print(text)
        if proc.returncode != 0:
            print(f"[FAIL] {script} exit={proc.returncode}; see {log}")
    print(f"\n[done] results in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
