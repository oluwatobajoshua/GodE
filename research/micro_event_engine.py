#!/usr/bin/env python3
"""Lane 3: event-style M5 backtester (step toward tick microstructure).

Processes bars sequentially with spread + slippage; no lookahead on signals.
Exports summary for EURUSD M5 by default; extend symbols via --symbol.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "micro_event_summary.csv"


def load_m5(symbol: str) -> pd.DataFrame:
    p = DATA / f"{symbol}_M5.csv"
    if not p.exists():
        p = DATA / "histdata" / f"{symbol}_M5.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def simulate(df: pd.DataFrame, spread_pts: float = 2.0, hold: int = 6) -> pd.Series:
    c = df["close"].astype(float)
    delta = c.diff()
    up = delta.clip(lower=0).rolling(2).mean()
    dn = (-delta.clip(upper=0)).rolling(2).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    tr = pd.concat([(df["high"] - df["low"]).astype(float),
                    (df["high"] - c.shift()).abs(),
                    (df["low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    pt = 0.00001
    cost = spread_pts * pt
    rets = []
    idx = []
    i = 30
    while i < len(df) - hold - 2:
        if rsi.iloc[i] <= 12:
            side = 1
        elif rsi.iloc[i] >= 88:
            side = -1
        else:
            i += 1
            continue
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            i += 1
            continue
        entry = c.iloc[i + 1] + side * cost
        sl = entry - side * a * 0.5
        tp = entry + side * a * 0.35
        exit_px = c.iloc[i + 1 + hold]
        for j in range(1, hold + 1):
            hi = float(df["high"].iloc[i + 1 + j])
            lo = float(df["low"].iloc[i + 1 + j])
            if side > 0 and (lo <= sl or hi >= tp):
                exit_px = tp if hi >= tp else sl
                break
            if side < 0 and (hi >= sl or lo <= tp):
                exit_px = tp if lo <= tp else sl
                break
            exit_px = float(c.iloc[i + 1 + j])
        gross = side * (exit_px - entry) / entry
        rets.append(gross - cost / entry)
        idx.append(c.index[i + 1])
        i += hold + 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--spread", type=float, default=2.0)
    args = ap.parse_args()
    df = load_m5(args.symbol)
    r = simulate(df, spread_pts=args.spread)
    if r.empty:
        print("No trades")
        return 1
    eq = (1 + r).cumprod()
    sharpe = r.mean() / r.std() * np.sqrt(252 * 288) if r.std() > 0 else 0
    row = {
        "symbol": args.symbol,
        "n": len(r),
        "winrate": round((r > 0).mean() * 100, 1),
        "sharpe": round(sharpe, 2),
        "total_pct": round((eq.iloc[-1] - 1) * 100, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        old = pd.read_csv(OUT)
        old = old[old["symbol"] != args.symbol]
        pd.concat([old, pd.DataFrame([row])], ignore_index=True).to_csv(OUT, index=False)
    else:
        pd.DataFrame([row]).to_csv(OUT, index=False)
    print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
