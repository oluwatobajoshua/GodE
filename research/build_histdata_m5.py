#!/usr/bin/env python3
"""Build an M5 CSV from HistData M1 for the intraday EA validation.

This reuses the HistData downloader and keeps only the M5 aggregate.  The M5
file is written to `research/data/histdata/` so `core.load_data()` can use the
same H1/D1 files for higher-timeframe context.

Run:
    .\\.venv\\Scripts\\python.exe research/build_histdata_m5.py --pair EURUSD --start-year 2010 --end-year 2025
"""

from __future__ import annotations

import argparse
import shutil
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from download_histdata import OUT, TMP, download_one

HERE = Path(__file__).resolve().parent


def parse_m1(zip_path: Path) -> pd.DataFrame | None:
    TMP.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                print(f"[FAIL] no CSV in {zip_path.name}")
                return None
            z.extract(names[0], TMP)
            csv_path = TMP / names[0]
    except Exception as exc:
        print(f"[FAIL] unzip {zip_path.name}: {exc}")
        return None

    try:
        df = pd.read_csv(
            csv_path,
            sep=";",
            header=None,
            names=["time", "open", "high", "low", "close", "volume"],
        )
    except Exception as exc:
        print(f"[FAIL] parse {csv_path.name}: {exc}")
        return None
    finally:
        csv_path.unlink(missing_ok=True)

    df["time"] = pd.to_datetime(df["time"], format="%Y%m%d %H%M%S", errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time")
    if df.empty:
        return None
    df["time"] = (
        df["time"]
        .dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
        .dt.tz_convert("UTC")
    )
    df = df.dropna(subset=["time"]).set_index("time")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    m5 = (
        df.resample("5min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    m5 = m5.rename(columns={"volume": "tick_volume"})
    m5["spread"] = 0
    m5["real_volume"] = 0
    return m5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="EURUSD")
    ap.add_argument("--start-year", type=int, default=2010)
    ap.add_argument("--end-year", type=int, default=2025)
    args = ap.parse_args()

    pair = args.pair.upper()
    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    frames: list[pd.DataFrame] = []
    for year in range(args.start_year, args.end_year + 1):
        zip_path = OUT / f"{pair}_M1_{year}.zip"
        if not zip_path.exists():
            print(f"[DL] {pair} {year}", flush=True)
            zip_path = download_one(session, pair, year)
            if zip_path is None:
                continue
            time.sleep(0.5)
        print(f"[M5] {pair} {year}", flush=True)
        m5 = parse_m1(zip_path)
        if m5 is not None and not m5.empty:
            frames.append(m5)
        zip_path.unlink(missing_ok=True)

    if not frames:
        print("[FAIL] no M5 frames")
        return 1
    out = pd.concat(frames, ignore_index=True).drop_duplicates("time").sort_values("time")
    path = OUT / f"{pair}_M5.csv"
    # Merge with an existing file so a retry for one missing year does not
    # overwrite the full history.
    if path.exists():
        old = pd.read_csv(path, parse_dates=["time"])
        out = pd.concat([old, out], ignore_index=True).drop_duplicates("time").sort_values("time")
    out.to_csv(path, index=False)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"[DONE] {path} rows={len(out)} {out.time.min()} .. {out.time.max()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
