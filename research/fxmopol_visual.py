#!/usr/bin/env python3
"""FXMOPOL visual chart spec (MT mobile): EMA21, EMA100, SMMA100, RSI(9) 20/50/80, fractals.

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_visual.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "fxmopol_visual_summary.csv"
SYMS = ("USDJPY", "EURUSD", "GBPUSD")
SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}
COST_BPS = 3.0
MAX_DAY = 3


def load_h1(sym: str) -> pd.DataFrame:
    for p in (DATA / "histdata" / f"{sym}_H1.csv", DATA / f"{sym}_H1.csv"):
        if p.exists():
            d = pd.read_csv(p, parse_dates=["time"])
            d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
            return d.set_index("time").sort_index()
    raise FileNotFoundError(sym)


def smma(close: pd.Series, period: int) -> pd.Series:
    out = np.full(len(close), np.nan)
    c = close.values.astype(float)
    if len(c) < period:
        return pd.Series(out, index=close.index)
    out[period - 1] = c[:period].mean()
    for i in range(period, len(c)):
        out[i] = (out[i - 1] * (period - 1) + c[i]) / period
    return pd.Series(out, index=close.index)


def fractal_low(l: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(l):
        return False
    v = l.iloc[i]
    return v < l.iloc[i - 1] and v < l.iloc[i - 2] and v < l.iloc[i + 1] and v < l.iloc[i + 2]


def fractal_high(h: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(h):
        return False
    v = h.iloc[i]
    return v > h.iloc[i - 1] and v > h.iloc[i - 2] and v > h.iloc[i + 1] and v > h.iloc[i + 2]


def criminal(o, h, l, c, side: int) -> bool:
    body, rng = abs(c - o), h - l
    if rng <= 0 or body / rng < 0.55:
        return False
    return (side > 0 and c > o and (h - c) / rng <= 0.25) or (side < 0 and c < o and (c - l) / rng <= 0.25)


def stack_trend(c, ema21, ema50, smma100, i: int) -> int:
    if any(pd.isna(x.iloc[i]) for x in (ema21, ema50, smma100)):
        return 0
    if c.iloc[i] > ema21.iloc[i] > ema50.iloc[i] and c.iloc[i] > smma100.iloc[i] and ema21.iloc[i] > ema21.iloc[i - 2]:
        return 1
    if c.iloc[i] < ema21.iloc[i] < ema50.iloc[i] and c.iloc[i] < smma100.iloc[i] and ema21.iloc[i] < ema21.iloc[i - 2]:
        return -1
    return 0


def rsi9(close: pd.Series) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(9).mean()
    down = (-delta.clip(upper=0)).rolling(9).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def near_fractal(h, l, i: int, side: int, lookback: int = 8) -> bool:
    for j in range(1, lookback + 1):
        k = i - j
        if k < 2:
            continue
        if side > 0 and fractal_low(l, k) and l.iloc[i] <= l.iloc[k] * 1.002:
            return True
        if side < 0 and fractal_high(h, k) and h.iloc[i] >= h.iloc[k] * 0.998:
            return True
    return False


def backtest(h1: pd.DataFrame, manual_level: float = 0.0) -> pd.Series:
    c = h1["close"].astype(float)
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    o = h1["open"].astype(float)
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    smma100 = smma(c, 100)
    r = rsi9(c)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    day = h1.index.normalize()
    counts: dict = {}
    rets, idx = [], []

    for i in range(105, len(h1) - 12):
        dk = day[i]
        counts.setdefault(dk, 0)
        if counts[dk] >= MAX_DAY:
            continue
        side = stack_trend(c, ema21, ema50, smma100, i)
        if side == 0:
            continue
        if side > 0 and not (50 < r.iloc[i] < 80):
            continue
        if side < 0 and not (20 < r.iloc[i] < 50):
            continue
        if not near_fractal(h, l, i, side):
            continue
        if not criminal(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], side):
            continue
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        if manual_level > 0 and side > 0 and abs(c.iloc[i] - manual_level) > a * 0.6:
            continue
        if manual_level > 0 and side < 0 and abs(c.iloc[i] - manual_level) > a * 0.6:
            continue

        entry = c.iloc[i + 1]
        sl_px = entry - side * a * 1.0
        tp_px = entry + side * a * 2.0
        hold = 12
        exit_px = c.iloc[i + 1 + hold]
        for j in range(1, hold + 1):
            hi, lo = h.iloc[i + 1 + j], l.iloc[i + 1 + j]
            if side > 0 and (lo <= sl_px or hi >= tp_px):
                exit_px = tp_px if hi >= tp_px else sl_px
                break
            if side < 0 and (hi >= sl_px or lo <= tp_px):
                exit_px = tp_px if lo <= tp_px else sl_px
                break
            exit_px = c.iloc[i + 1 + j]
        gross = side * (exit_px - entry) / entry - COST_BPS / 10000.0
        rets.append(gross)
        idx.append(h1.index[i + 1])
        counts[dk] += 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def stats(r: pd.Series) -> dict:
    if len(r) < 10:
        return {"n": len(r), "exp_bps": -999, "pf": 0, "winrate": 0}
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    return {
        "n": len(r),
        "exp_bps": round(r.mean() * 10000, 2),
        "pf": round(pf, 2),
        "winrate": round(100 * (r > 0).mean(), 1),
    }


def main() -> int:
    rows = []
    for sym in SYMS:
        try:
            h1 = load_h1(sym)
        except FileNotFoundError:
            continue
        trade_r = backtest(h1)
        for label, (a, b) in SPLITS.items():
            part = trade_r[(trade_r.index >= a) & (trade_r.index < b)]
            row = {"symbol": sym, "split": label, **stats(part)}
            rows.append(row)
        from period_returns import print_split_monthly, publish_results

        publish_results(f"fxmopol_visual_{sym}", trade_r)
        print_split_monthly(f"fxmopol_visual_{sym}", trade_r, SPLITS)
    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"Per-trade split metrics → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
