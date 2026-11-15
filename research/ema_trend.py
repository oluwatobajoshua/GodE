#!/usr/bin/env python3
"""EMA trend + pullback/break (matches SovereignEmaTrendEA logic, bar backtest)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_trend_summary.csv"
COST_BPS = 3.0


def load(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    tcol = "time"
    d[tcol] = pd.to_datetime(d[tcol], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index(tcol).sort_index()


def backtest(df: pd.DataFrame, ema: int = 50, slope: int = 3, min_adx: float = 18.0) -> pd.Series:
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    o = df["open"].astype(float)
    ema_s = c.ewm(span=ema, adjust=False).mean()
    atr = (pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)).rolling(14).mean()
    up = (c > ema_s) & (ema_s > ema_s.shift(slope))
    dn = (c < ema_s) & (ema_s < ema_s.shift(slope))
    plus_dm = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    pdi = 100 * (plus_dm.rolling(14).mean() / atr14)
    mdi = 100 * (minus_dm.rolling(14).mean() / atr14)
    adx = (pdi - mdi).abs().rolling(14).mean()

    pull_long = up & (l <= ema_s + atr * 0.35) & (c > o) & (c > ema_s)
    pull_short = dn & (h >= ema_s - atr * 0.35) & (c < o) & (c < ema_s)
    brk_long = up & (c > h.shift(1)) & (c > ema_s)
    brk_short = dn & (c < l.shift(1)) & (c < ema_s)
    long_sig = (pull_long | brk_long) & (adx >= min_adx)
    short_sig = (pull_short | brk_short) & (adx >= min_adx)

    rets = []
    idx = []
    i = ema + slope + 20
    hold = 8
    sl_m = 1.25
    tp_m = 1.75
    while i < len(c) - hold - 1:
        side = 0
        if long_sig.iloc[i]:
            side = 1
        elif short_sig.iloc[i]:
            side = -1
        if side == 0:
            i += 1
            continue
        entry = c.iloc[i + 1]
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            i += 1
            continue
        sl = entry - side * a * sl_m
        tp = entry + side * a * tp_m
        exit_px = c.iloc[i + 1 + hold]
        for j in range(1, hold + 1):
            hi, lo = h.iloc[i + 1 + j], l.iloc[i + 1 + j]
            if side > 0 and (lo <= sl or hi >= tp):
                exit_px = tp if hi >= tp else sl
                break
            if side < 0 and (hi >= sl or lo <= tp):
                exit_px = tp if lo <= tp else sl
                break
            exit_px = c.iloc[i + 1 + j]
        gross = side * (exit_px - entry) / entry - COST_BPS / 10000.0
        rets.append(gross)
        idx.append(c.index[i + 1])
        i += hold + 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def main() -> int:
    rows = []
    for sym in ("EURUSD", "GBPUSD"):
        for tf in ("H1", "M15"):
            path = DATA / f"{sym}_{tf}.csv"
            if not path.exists():
                continue
            r = backtest(load(sym, tf))
            if r.empty:
                continue
            eq = (1 + r).cumprod()
            sh = r.mean() / r.std() * np.sqrt(252 * (24 if tf == "H1" else 96)) if r.std() > 0 else 0
            rows.append(
                {
                    "symbol": sym,
                    "tf": tf,
                    "n": len(r),
                    "winrate": round((r > 0).mean() * 100, 1),
                    "sharpe": round(sh, 2),
                    "total_pct": round((eq.iloc[-1] - 1) * 100, 2),
                }
            )
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(pd.DataFrame(rows).to_string(index=False))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
