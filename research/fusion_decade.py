#!/usr/bin/env python3
"""Lane 1: fuse monthly carry_mom with daily novel overlay (vol-of-vol defensive)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = HERE / "results" / "fusion_decade_summary.csv"
SPLITS = {
    "design": ("2008-01-01", "2015-01-01"),
    "validation": ("2015-01-01", "2019-01-01"),
    "OOS": ("2019-01-01", "2026-12-31"),
}

PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
]


def load_d1() -> pd.DataFrame:
    frames = {}
    for p in PAIRS:
        path = HERE / "data" / f"{p}_D1.csv"
        if not path.exists():
            continue
        d = pd.read_csv(path)
        d.columns = [c.lower() for c in d.columns]
        tcol = "time" if "time" in d.columns else "date"
        d[tcol] = pd.to_datetime(d[tcol], utc=True, errors="coerce").dt.tz_convert(None)
        d = d.dropna(subset=[tcol]).drop_duplicates(tcol).set_index(tcol).sort_index()
        frames[p] = d["close"].astype(float)
    return pd.DataFrame(frames).sort_index().ffill(limit=3)


def metrics(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    if r.empty or r.std() == 0:
        return {"sleeve": label, "cagr": 0, "sharpe": 0, "maxdd": 0, "pf": 0, "n": 0}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * np.sqrt(252)
    maxdd = (eq / eq.cummax() - 1).min()
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "sleeve": label,
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "pf": round(pf, 2) if pd.notna(pf) else None,
        "n": len(r),
    }


def daily_overlay_returns(prices: pd.DataFrame) -> pd.Series:
    from discovery_novel import nov_vov_defensive_carry  # noqa: WPS433

    w = nov_vov_defensive_carry(prices).fillna(0.0)
    gross = w.abs().sum(axis=1).replace(0, np.nan)
    w = w.div(gross, axis=0).fillna(0.0)
    rets = prices.pct_change()
    turnover = w.diff().abs().sum(axis=1) / 2.0
    port = (w.shift(1) * rets).sum(axis=1) - turnover * 2.0 / 10000.0
    return port.dropna()


def monthly_carry_returns() -> pd.Series:
    path = HERE / "results" / "carry_returns.csv"
    if not path.exists():
        subprocess.check_call([sys.executable, str(HERE / "carry.py")], cwd=str(REPO))
    curves = pd.read_csv(path, index_col=0, parse_dates=True)
    r = curves["carry_mom_filter"].dropna()
    return r


def fuse(carry_m: pd.Series, daily: pd.Series, novel_w: float = 0.25) -> pd.Series:
    daily = daily.reindex(carry_m.index, method="ffill").fillna(0.0)
    carry_d = carry_m.resample("D").ffill().reindex(daily.index).ffill().fillna(0.0)
    novel_d = daily
    combined = (1 - novel_w) * carry_d + novel_w * novel_d
    return combined.resample("ME").sum()


def main() -> int:
    prices = load_d1()
    daily = daily_overlay_returns(prices)
    carry_m = monthly_carry_returns()
    fused_m = fuse(carry_m, daily, novel_w=0.25)

    rows = []
    for name, series in [
        ("carry_mom_only", carry_m),
        ("fusion_carry_75_novel_25", fused_m),
    ]:
        for split, (a, b) in SPLITS.items():
            part = series[(series.index >= a) & (series.index < b)]
            rows.append({"split": split, **metrics(part, name)})

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(df.to_string(index=False))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
