#!/usr/bin/env python3
"""FXMOPOL — merged criminal candle + BabyPips MTF/SR/fib/pivot stack.

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "fxmopol_summary.csv"
SYMS = ("EURUSD", "GBPUSD")
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


def h4_from_h1(h1: pd.DataFrame) -> pd.DataFrame:
    return h1.resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def pivots(h1: pd.DataFrame) -> pd.DataFrame:
    d1 = h1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    p = (d1["high"] + d1["low"] + d1["close"]) / 3.0
    return pd.DataFrame({"S1": 2 * p - d1["high"], "R1": 2 * p - d1["low"]}, index=d1.index).shift(1)


def criminal(o, h, l, c, side: int) -> bool:
    body = abs(c - o)
    rng = h - l
    if rng <= 0 or body / rng < 0.55:
        return False
    return (side > 0 and c > o and (h - c) / rng <= 0.25) or (side < 0 and c < o and (c - l) / rng <= 0.25)


def engulf(o, c, po, pc, side: int) -> bool:
    if side > 0:
        return c > o and pc < po and c > po and o < pc
    return c < o and pc > po and c < po and o > pc


def pin_bar(o, h, l, c, side: int) -> bool:
    body = abs(c - o)
    rng = h - l
    if rng <= 0 or body <= 0:
        return False
    lo, up = min(o, c) - l, h - max(o, c)
    return (side > 0 and lo >= 2 * body and up <= body) or (side < 0 and up >= 2 * body and lo <= body)


def backtest(h1: pd.DataFrame) -> pd.Series:
    h4 = h4_from_h1(h1)
    c = h1["close"].astype(float)
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    o = h1["open"].astype(float)
    ema21 = c.ewm(span=21, adjust=False).mean()
    h4c = h4["close"].reindex(c.index, method="ffill")
    h4e = h4["close"].ewm(span=50, adjust=False).mean().reindex(c.index, method="ffill")
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    delta = c.diff()
    rs = delta.clip(lower=0).rolling(9).mean() / (-delta.clip(upper=0)).rolling(9).mean().replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    pv = pivots(h1)
    s1 = pv["S1"].reindex(c.index, method="ffill")
    r1 = pv["R1"].reindex(c.index, method="ffill")
    swing = 20
    day = h1.index.normalize()
    counts: dict = {}
    rets, idx = [], []

    for i in range(swing + 5, len(h1) - 12):
        dk = day[i]
        counts.setdefault(dk, 0)
        if counts[dk] >= MAX_DAY:
            continue
        if pd.isna(h4c.iloc[i]) or pd.isna(h4e.iloc[i]):
            continue
        up = h4c.iloc[i] > h4e.iloc[i] and c.iloc[i] > ema21.iloc[i] and ema21.iloc[i] > ema21.iloc[i - 2]
        dn = h4c.iloc[i] < h4e.iloc[i] and c.iloc[i] < ema21.iloc[i] and ema21.iloc[i] < ema21.iloc[i - 2]
        if not up and not dn:
            continue
        side = 1 if up else -1
        sh = h.iloc[i - swing : i].max()
        slv = l.iloc[i - swing : i].min()
        fib50 = slv + (sh - slv) * 0.5
        fib618 = slv + (sh - slv) * 0.618
        a = atr.iloc[i]
        if side > 0:
            zone = (l.iloc[i] >= fib618 and l.iloc[i] <= sh * 1.001) or abs(l.iloc[i] - s1.iloc[i]) < a * 0.35
        else:
            zone = (h.iloc[i] <= fib50 and h.iloc[i] >= slv * 0.999) or abs(h.iloc[i] - r1.iloc[i]) < a * 0.35
        if not zone:
            continue
        pat = criminal(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], side) or engulf(
            o.iloc[i], c.iloc[i], o.iloc[i - 1], c.iloc[i - 1], side
        ) or pin_bar(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], side)
        if not pat:
            continue
        r = rsi.iloc[i]
        if side > 0 and not (35 <= r <= 58):
            continue
        if side < 0 and not (42 <= r <= 65):
            continue
        if pd.isna(a) or a <= 0:
            continue
        entry = c.iloc[i + 1]
        sl_px = entry - side * a * 1.0
        tp_px = entry + side * a * 2.0
        exit_px = c.iloc[i + 1 + 8]
        for j in range(1, 9):
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


def metrics(r: pd.Series) -> dict:
    if len(r) < 15:
        return {"n": len(r), "winrate": 0, "exp_bps": 0, "total_pct": 0, "pf": 0}
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    return {
        "n": len(r),
        "winrate": round(100 * (r > 0).mean(), 1),
        "exp_bps": round(r.mean() * 10000, 2),
        "total_pct": round(((1 + r).prod() - 1) * 100, 2),
        "pf": round(pf, 2),
    }


def main() -> int:
    rows = []
    for sym in SYMS:
        r = backtest(load_h1(sym))
        for split, (a, b) in SPLITS.items():
            sub = r[(r.index >= a) & (r.index < b)]
            rows.append({"symbol": sym, "split": split, **metrics(sub)})
    pd.DataFrame(rows).to_csv(OUT, index=False)
    from period_returns import print_split_monthly, publish_results

    for sym in SYMS:
        r = backtest(load_h1(sym))
        publish_results(f"fxmopol_{sym}", r)
        print_split_monthly(f"fxmopol_{sym}", r, SPLITS)
    print(f"Split metrics: {OUT}")
    print("Automated: SovereignFxmopolEA on H1; proof sleeve: carry_mom_filter (GodsEye)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
