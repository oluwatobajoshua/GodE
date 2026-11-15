#!/usr/bin/env python3
"""Lane 2: non-G10-spot sleeves — simple 12-1 time-series momentum on alt symbols.

Uses broker-exported series where present (BTCUSD H1, XAUUSD M5). Pre-declared
BTC 3-split windows (2018+). Not wired to GodsEye unless promoted_3split.

Run:
    .\\.venv\\Scripts\\python.exe research\\alt_sleeve_scan.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from carry import metrics

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "alt_sleeve_scan.csv"
MANIFEST = HERE / "results" / "alt_sleeve_manifest.json"

BTC_SPLITS = {
    "design": ("2018-01-01", "2021-01-01"),
    "validation": ("2021-01-01", "2023-07-01"),
    "OOS": ("2023-07-01", "2032-01-01"),
}

XAU_SPLITS = {
    "design": ("2005-01-01", "2015-01-01"),
    "validation": ("2015-01-01", "2020-01-01"),
    "OOS": ("2020-01-01", "2032-01-01"),
}


def load_ohlc(path: Path) -> pd.Series:
    if not path.exists():
        return pd.Series(dtype=float)
    d = pd.read_csv(path, usecols=["time", "close"], parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
    d = d.drop_duplicates("time").set_index("time")["close"].sort_index()
    m = d.resample("ME").last()
    m.index = m.index.to_period("M").to_timestamp()
    return m.pct_change().fillna(0.0)


def tsmom12(r: pd.Series) -> pd.Series:
    sig = np.sign(r.rolling(12).apply(lambda x: np.prod(1 + x) - 1, raw=True)).shift(1)
    strat = (sig * r).fillna(0.0)
    return strat


def split_ok(r: pd.Series, splits: dict[str, tuple[str, str]]) -> tuple[bool, list[dict]]:
    rows = []
    ok = True
    for name, (a, b) in splits.items():
        part = r[(r.index >= a) & (r.index < b)]
        if len(part) < 6:
            ok = False
            rows.append({"split": name, "n": len(part), "ok": False})
            continue
        m = metrics(part, name)
        pf = part[part > 0].sum() / abs(part[part < 0].sum()) if (part < 0).any() else float("nan")
        part_ok = m["cagr"] > 0 and m["sharpe"] > 0 and (pf >= 1.0 if pf == pf else False)
        ok = ok and part_ok
        rows.append(
            {
                "split": name,
                "n": len(part),
                "cagr": m["cagr"],
                "sharpe": m["sharpe"],
                "maxdd": m["maxdd"],
                "ok": part_ok,
            }
        )
    return ok, rows


def main() -> int:
    specs = [
        ("BTCUSD", DATA / "BTCUSD_H1.csv", BTC_SPLITS, 10.0),
        ("XAUUSD", DATA / "XAUUSD_M5.csv", XAU_SPLITS, 12.0),
        ("DXYUSD", DATA / "DXYUSD_M5.csv", XAU_SPLITS, 12.0),
    ]
    manifest_rows = []
    csv_rows = []

    for symbol, path, splits, cost_bps in specs:
        spot = load_ohlc(path)
        if spot.empty:
            csv_rows.append({"symbol": symbol, "status": "missing_data", "path": str(path)})
            continue
        strat = tsmom12(spot)
        # monthly turnover cost ~1 rebalance
        strat = strat - cost_bps / 10000.0
        ok, split_rows = split_ok(strat, splits)
        full = metrics(strat.dropna(), "full")
        csv_rows.append(
            {
                "symbol": symbol,
                "status": "ok",
                "promoted_3split": ok,
                "cagr_full": full["cagr"],
                "sharpe_full": full["sharpe"],
                "maxdd_full": full["maxdd"],
                "bars": len(spot),
            }
        )
        manifest_rows.append(
            {
                "symbol": symbol,
                "sleeve": "tsmom_12_1",
                "promoted_3split": ok,
                "full": full,
                "splits": split_rows,
                "wire_to_ea": False,
            }
        )

    pd.DataFrame(csv_rows).to_csv(OUT, index=False)
    def _scalar(v):
        if isinstance(v, (np.floating, np.integer)):
            return float(v)
        if isinstance(v, (np.bool_, bool)):
            return bool(v)
        return v

    def _jsonable(rows: list) -> list:
        out = []
        for row in rows:
            item = {k: _scalar(v) for k, v in row.items() if k not in ("full", "splits")}
            item["full"] = {kk: _scalar(vv) for kk, vv in row.get("full", {}).items()}
            item["splits"] = [{kk: _scalar(vv) for kk, vv in s.items()} for s in row.get("splits", [])]
            out.append(item)
        return out

    MANIFEST.write_text(
        json.dumps(
            {
                "lane": 2,
                "note": "Alt assets are scanned only; carry stack remains default certified sleeve.",
                "sleeves": _jsonable(manifest_rows),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(pd.DataFrame(csv_rows).to_string(index=False))
    print(f"Wrote {OUT.name}, {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
