#!/usr/bin/env python3
"""Backtest micro stretch-fade (SOV-SCOUT logic) on H1 or M5 CSV data."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "micro_opportunity_summary.csv"
COST_BPS = 3.0


def load(sym: str, tf: str) -> pd.DataFrame:
    path = DATA / f"{sym}_{tf}.csv"
    d = pd.read_csv(path, parse_dates=["time"])
    tcol = "time"
    d[tcol] = pd.to_datetime(d[tcol], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index(tcol).sort_index()


def backtest(df: pd.DataFrame, hold: int = 3, tp_atr: float = 0.45, sl_atr: float = 0.55) -> pd.Series:
    c = df["close"].astype(float)
    h, l = df["high"].astype(float), df["low"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    zwin = 8
    z = (c - c.rolling(zwin).mean()) / c.rolling(zwin).std()
    delta = c.diff()
    up = delta.clip(lower=0).rolling(2).mean()
    dn = (-delta.clip(upper=0)).rolling(2).mean()
    rs = up / dn.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    side = pd.Series(0, index=c.index)
    side[(rsi <= 15) & (z <= -1.2)] = 1
    side[(rsi >= 85) & (z >= 1.2)] = -1
    rets = []
    idx = []
    i = 20
    while i < len(c) - hold - 1:
        if side.iloc[i] == 0:
            i += 1
            continue
        s = side.iloc[i]
        entry = c.iloc[i + 1]
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            i += 1
            continue
        tp = entry + s * a * tp_atr
        sl = entry - s * a * sl_atr
        exit_px = c.iloc[i + 1 + hold]
        for j in range(1, hold + 1):
            hi, lo, cl = h.iloc[i + 1 + j], l.iloc[i + 1 + j], c.iloc[i + 1 + j]
            if s > 0 and (lo <= sl or hi >= tp):
                exit_px = tp if hi >= tp else sl
                break
            if s < 0 and (hi >= sl or lo <= tp):
                exit_px = tp if lo <= tp else sl
                break
            exit_px = cl
        gross = s * (exit_px - entry) / entry
        rets.append(gross - COST_BPS / 10000.0)
        idx.append(c.index[i + 1])
        i += hold + 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def main() -> int:
    rows = []
    for sym in ("EURUSD", "GBPUSD", "USDJPY"):
        for tf in ("H1", "M5"):
            path = DATA / f"{sym}_{tf}.csv"
            if not path.exists():
                continue
            r = backtest(load(sym, tf))
            if r.empty:
                continue
            eq = (1 + r).cumprod()
            sharpe = r.mean() / r.std() * np.sqrt(252 * (24 if tf == "H1" else 288)) if r.std() > 0 else 0
            rows.append({
                "symbol": sym, "tf": tf, "n": len(r),
                "winrate": round((r > 0).mean() * 100, 1),
                "avg_r": round(r.mean() * 10000, 2),
                "sharpe": round(sharpe, 2),
                "total_pct": round((eq.iloc[-1] - 1) * 100, 2),
            })
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(pd.DataFrame(rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
