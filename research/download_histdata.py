#!/usr/bin/env python3
"""Download HistData.com M1 FX data and aggregate it to H1/D1.

HistData requires a session token and a POST to get.php.  The raw M1 ZIP is
processed one year at a time and deleted after aggregation so the repository
does not fill with gigabytes of minute bars.

Timestamps in the HistData ASCII files are New York local time; they are
converted to UTC before aggregation.

Usage:
    .\\.venv\\Scripts\\python.exe research/download_histdata.py --pairs EURUSD GBPUSD --start-year 2024 --end-year 2024
"""

from __future__ import annotations

import argparse
import re
import shutil
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "histdata"
TMP = OUT / "_tmp"
BASE = "https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/{pair}/{year}"
POST = "https://www.histdata.com/get.php"


def download_one(session: requests.Session, pair: str, year: int) -> Path | None:
    url = BASE.format(pair=pair.lower(), year=year)
    r = session.get(url, timeout=60)
    if r.status_code != 200:
        print(f"[FAIL] page {pair} {year}: HTTP {r.status_code}")
        return None
    m = re.search(r'name="tk" id="tk" value="([^"]+)"', r.text)
    if not m:
        print(f"[FAIL] token {pair} {year}")
        return None
    data = {
        "tk": m.group(1),
        "date": str(year),
        "datemonth": str(year),
        "platform": "ASCII",
        "timeframe": "M1",
        "fxpair": pair.upper(),
    }
    session.headers.update(
        {
            "Referer": url,
            "Origin": "https://www.histdata.com",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    p = session.post(POST, data=data, timeout=180)
    if p.status_code != 200 or not p.content.startswith(b"PK"):
        print(
            f"[FAIL] download {pair} {year}: HTTP {p.status_code}, "
            f"{len(p.content)} bytes, type={p.headers.get('content-type')}, "
            f"url={p.url}, history={[(h.status_code, h.headers.get('location')) for h in p.history]}"
        )
        return None
    path = OUT / f"{pair}_M1_{year}.zip"
    path.write_bytes(p.content)
    return path


def aggregate(pair: str, year: int, zip_path: Path) -> tuple[pd.DataFrame, pd.DataFrame] | None:
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
        print(f"[FAIL] empty {pair} {year}")
        return None
    # HistData timestamps are New York local time.
    df["time"] = (
        df["time"]
        .dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward")
        .dt.tz_convert("UTC")
    )
    df = df.dropna(subset=["time"]).set_index("time")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])

    h1 = df.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    d1 = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return h1, d1


def append_pair(pair: str, h1: pd.DataFrame, d1: pd.DataFrame) -> None:
    for tf, d in (("H1", h1), ("D1", d1)):
        path = OUT / f"{pair}_{tf}.csv"
        out = d.reset_index().rename(columns={"time": "time", "volume": "tick_volume"})
        out["spread"] = 0
        out["real_volume"] = 0
        if path.exists():
            old = pd.read_csv(path, parse_dates=["time"])
            out = pd.concat([old, out], ignore_index=True)
        out = out.drop_duplicates("time").sort_values("time")
        if tf == "D1":
            # Drop the partial Sunday UTC bar; the Monday bar already contains
            # the Sunday 22:00-24:00 session.
            out = out[out["time"].dt.dayofweek < 5]
        out.to_csv(path, index=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", nargs="+", default=["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF"])
    ap.add_argument("--start-year", type=int, default=2010)
    ap.add_argument("--end-year", type=int, default=2026)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"

    for pair in args.pairs:
        pair = pair.upper()
        for year in range(args.start_year, args.end_year + 1):
            zip_path = OUT / f"{pair}_M1_{year}.zip"
            if not zip_path.exists():
                print(f"[DL] {pair} {year}", flush=True)
                zip_path = download_one(session, pair, year)
                if zip_path is None:
                    continue
                time.sleep(0.5)
            print(f"[AGG] {pair} {year}", flush=True)
            res = aggregate(pair, year, zip_path)
            if res is None:
                continue
            h1, d1 = res
            append_pair(pair, h1, d1)
            zip_path.unlink(missing_ok=True)
    shutil.rmtree(TMP, ignore_errors=True)
    print("[DONE]", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
