#!/usr/bin/env python3
"""Freeze monthly/daily result CSVs + chart spec manifest for fine-tune baseline."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RESULTS = HERE / "results"
OUT = RESULTS / "snapshot_manifest.json"
ARCHIVE = RESULTS / "snapshots"

MONTHLY_GLOB = "*_monthly.csv"
KEY_FILES = (
    "monthly_returns.csv",
    "daily_returns.csv",
    "carry_mom_filter_monthly.csv",
    "stack_tune_default_monthly.csv",
    "ema_streak_ride_monthly.csv",
    "fx_evidence_monthly_returns.csv",
)


def last_n_months(path: Path, n: int = 12) -> list[dict]:
    if not path.exists():
        return []
    df = pd.read_csv(path)
    if "date" not in df.columns:
        return []
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    if "strategy" in df.columns and df["strategy"].nunique() > 1:
        parts = [g.tail(n) for _, g in df.groupby("strategy")]
        df = pd.concat(parts, ignore_index=True)
    else:
        df = df.tail(n)
    for col in df.select_dtypes(include=["datetime64"]).columns:
        df[col] = df[col].dt.strftime("%Y-%m-%d")
    return df.to_dict(orient="records")


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    arch = ARCHIVE / stamp
    arch.mkdir(parents=True, exist_ok=True)

    copied = []
    for name in KEY_FILES:
        src = RESULTS / name
        if src.exists():
            shutil.copy2(src, arch / name)
            copied.append(name)

    for src in RESULTS.glob(MONTHLY_GLOB):
        if src.name not in copied:
            shutil.copy2(src, arch / src.name)
            copied.append(src.name)

    manifest = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "chart_spec_doc": "docs/FXMOPOL_CHART_SNAPSHOT.md",
        "chart_stack": "EMA21 + EMA50 + SMMA100 close; RSI levels 20/50/80",
        "playbook_doc": "docs/FXMOPOL_CHART_SNAPSHOT.md",
        "eurusd_example": "2025-11-02..06 long 1.119-1.121 exit 1.124-1.126; short 1.120-1.121 cover 1.114-1.115",
        "fine_tune_queue": [
            "stop_hunt_reclaim while above SMMA100",
            "stack_regime gate (M bars above all MAs)",
            "level_tp at InpManualLevel; RSI 80/20 exits",
            "EURUSD + USDJPY H1; daily/monthly reporting only",
        ],
        "archive_dir": str(arch.relative_to(REPO)),
        "files": copied,
        "carry_mom_last_12": last_n_months(RESULTS / "carry_mom_filter_monthly.csv"),
        "stack_tune_last_12": last_n_months(RESULTS / "stack_tune_default_monthly.csv"),
        "note": "User believes chart trend-window + stop-hunt entries differ from current backtest rules.",
    }
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.copy2(OUT, arch / "snapshot_manifest.json")

    print(f"Snapshot saved: {arch}")
    print(f"Manifest: {OUT}")
    print(f"carry_mom months archived: {len(manifest['carry_mom_last_12'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
