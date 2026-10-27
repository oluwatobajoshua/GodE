#!/usr/bin/env python3
"""Low-turnover D1 momentum research across major FX pairs.

This tests two classic FX strategies that are structurally different from the
failed intraday breakout/mean-reversion rules:

1. Time-series momentum (TSMOM): long a pair if its 12-month return is
   positive, short if negative.  Rebalanced monthly.
2. Cross-sectional momentum (XSMOM): rank pairs by 12-month return, long the
   top bucket and short the bottom bucket.  Rebalanced monthly.

Both are low-turnover, so the spread cost is negligible.  Signals use the
month-end close and are applied on the next trading day.

Splits:
* design:      2006-01-01 .. 2014-12-31
* validation:  2015-01-01 .. 2019-12-31
* OOS:         2020-01-01 .. 2026-10-26
* recent:      2025-01-01 .. 2026-10-26

Run:
    .\\.venv\\Scripts\\python.exe research\\d1_momentum.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))

SYMBOLS = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "AUDUSD": 0.00001,
    "NZDUSD": 0.00001,
    "USDCAD": 0.00001,
    "USDCHF": 0.00001,
    "EURGBP": 0.00001,
    "EURCHF": 0.00001,
    "USDJPY": 0.001,
    "EURJPY": 0.001,
    "AUDJPY": 0.001,
    "CADJPY": 0.001,
}

TARGET_VOL = 0.10
COST_PTS = 12.0


def load_closes() -> pd.DataFrame:
    frames = {}
    for sym in SYMBOLS:
        path = DATA / f"{sym}_D1.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, usecols=["time", "close"], parse_dates=["time"])
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(None)
        df = df.drop_duplicates("time").set_index("time")["close"].sort_index()
        frames[sym] = df
    closes = pd.DataFrame(frames).sort_index()
    return closes


def monthly_signal(daily_signal: pd.DataFrame, index: pd.DatetimeIndex) -> pd.DataFrame:
    """Use the last signal of each month, applied on the next trading day."""
    m = daily_signal.resample("ME").last()
    return m.reindex(index, method="ffill").shift(1)


def costs_for_weights(weights: pd.DataFrame, closes: pd.DataFrame) -> pd.Series:
    points = pd.Series(SYMBOLS)
    per_unit = (COST_PTS * points) / closes
    turn = weights.diff().abs()
    return (turn * per_unit).sum(axis=1, min_count=1).fillna(0.0)


def metrics(returns: pd.Series, label: str) -> dict:
    r = returns.dropna()
    if r.empty:
        return {"strategy": label, "start": "", "end": "", "years": 0, "cagr": 0, "vol": 0, "sharpe": 0, "maxdd": 0, "total": 0}
    eq = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    vol = r.std() * np.sqrt(252)
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {
        "strategy": label,
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "years": round(years, 1),
        "cagr": round(cagr * 100, 2),
        "vol": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(dd * 100, 2),
        "total": round((eq.iloc[-1] - 1) * 100, 2),
    }


def split_returns(r: pd.Series) -> dict[str, pd.Series]:
    return {
        "design_2006_2014": r[(r.index >= "2006-01-01") & (r.index < "2015-01-01")],
        "validation_2015_2019": r[(r.index >= "2015-01-01") & (r.index < "2020-01-01")],
        "OOS_2020_2026": r[r.index >= "2020-01-01"],
        "recent_2025_2026": r[r.index >= "2025-01-01"],
        "full_2006_2026": r[r.index >= "2006-01-01"],
    }


def run_tsmom(closes: pd.DataFrame, lookback: int = 252, vol_target: float = TARGET_VOL) -> tuple[pd.Series, pd.DataFrame]:
    rets = closes.pct_change()
    mom = closes / closes.shift(lookback) - 1
    sig = np.sign(mom)
    sig = monthly_signal(sig, closes.index)
    vol = rets.rolling(20).std() * np.sqrt(252)
    scalar = (vol_target / vol).clip(upper=2.0)
    n_active = sig.notna().sum(axis=1).replace(0, np.nan)
    weights = (sig * scalar).div(n_active, axis=0)
    gross = weights.abs().sum(axis=1)
    # Portfolio-level vol targeting using past 60-day realized vol.
    raw = (weights * rets).sum(axis=1, min_count=1)
    pvol = raw.rolling(60).std() * np.sqrt(252)
    port_scalar = (vol_target / pvol).clip(upper=3.0).shift(1)
    weights = weights.mul(port_scalar, axis=0)
    cost = costs_for_weights(weights, closes)
    r = (weights * rets).sum(axis=1, min_count=1) - cost
    return r.dropna(), weights


def run_xsmom(closes: pd.DataFrame, lookback: int = 252, top_frac: float = 0.3, vol_target: float = TARGET_VOL) -> tuple[pd.Series, pd.DataFrame]:
    rets = closes.pct_change()
    mom = closes / closes.shift(lookback) - 1
    rank = mom.rank(axis=1, pct=True)
    long = rank >= (1 - top_frac)
    short = rank <= top_frac
    n_long = long.sum(axis=1).replace(0, np.nan)
    n_short = short.sum(axis=1).replace(0, np.nan)
    raw_w = long.div(n_long, axis=0).fillna(0) - short.div(n_short, axis=0).fillna(0)
    raw_w = monthly_signal(raw_w, closes.index)
    raw = (raw_w * rets).sum(axis=1, min_count=1)
    pvol = raw.rolling(60).std() * np.sqrt(252)
    scalar = (vol_target / pvol).clip(upper=3.0).shift(1)
    weights = raw_w.mul(scalar, axis=0)
    cost = costs_for_weights(weights, closes)
    r = (weights * rets).sum(axis=1, min_count=1) - cost
    return r.dropna(), weights


def main() -> int:
    closes = load_closes()
    print(f"pairs: {list(closes.columns)}")
    print(f"data: {closes.index.min().date()} -> {closes.index.max().date()}, rows={len(closes)}")

    strategies = {}
    for lookback in (126, 252):
        for vol_target in (0.08, 0.10):
            strategies[f"tsmom_{lookback}_v{int(vol_target*100)}"] = run_tsmom(closes, lookback, vol_target)
    for lookback in (126, 252):
        for top_frac in (0.25, 0.33):
            strategies[f"xsmom_{lookback}_top{int(top_frac*100)}"] = run_xsmom(closes, lookback, top_frac)

    rows = []
    curves = {}
    for name, (r, w) in strategies.items():
        curves[name] = r
        for split, part in split_returns(r).items():
            rows.append({"name": name, "split": split, **metrics(part, name)})

    summary = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "d1_momentum_summary.csv", index=False)
    pd.DataFrame(curves).to_csv(OUT / "d1_momentum_returns.csv")

    print("\n=== full period ===")
    print(summary[summary["split"] == "full_2006_2026"].sort_values("sharpe", ascending=False).to_string(index=False))
    print("\n=== by split ===")
    print(summary[summary["split"] != "full_2006_2026"].sort_values(["name", "split"]).to_string(index=False))

    piv = summary.pivot(index="name", columns="split", values="cagr")
    print("\n=== CAGR by split ===")
    print(piv.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
