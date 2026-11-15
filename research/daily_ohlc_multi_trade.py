#!/usr/bin/env python3
"""Daily OHLC + many trades/day — breakeven math and bar backtests.

User hypothesis: with O/H/L/C every day, ~10 trades/day and ~50% wins can still
profit with good R:R and management. This script quantifies required win rate
and tests simple OHLC/M5 rules (not a guarantee of edge).

Run:
    .\\.venv\\Scripts\\python.exe research\\daily_ohlc_multi_trade.py
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "daily_ohlc_multi_trade_summary.csv"
SYMS = ("EURUSD",)
SPLITS = {
    "design": ("2019-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}
COST_BPS = 3.0
MAX_TRADES_PER_DAY = 10


def breakeven_winrate(tp_atr: float, sl_atr: float, cost_bps: float = 3.0) -> float:
    """Approx breakeven win % for fixed ATR tp/sl (ignores slip distribution)."""
    # win pays tp/sl in R units; loss pays -1R; cost ~ cost_bps as fraction of 1R
    r_win = tp_atr / sl_atr
    cost_r = cost_bps / 10000.0 / (sl_atr * 1e-4)  # rough; use small epsilon
    cost_r = cost_bps / 10000.0 * 10  # ~3bp on FX ~1R ≈ 0.0003 price
    # p * r_win - (1-p) * 1 - cost = 0  => p = (1+cost)/(1+r_win)
    return (1.0 + cost_bps / 10000.0) / (1.0 + r_win)


def load_m5(sym: str) -> pd.DataFrame:
    p = DATA / f"{sym}_M5.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def load_d1(sym: str) -> pd.DataFrame:
    p = DATA / f"{sym}_D1.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


@dataclass
class Rules:
    name: str
    sl_atr: float
    tp_atr: float
    max_hold_m5: int = 12


def trades_orb_m5(df: pd.DataFrame, rules: Rules) -> pd.Series:
    """London ORB proxy: range 07:00-08:00 UTC, trade breaks until max/day."""
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    o = df["open"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14 * 12).mean()

    day = df.index.normalize()
    rets = []
    idx = []
    unique_days = pd.Series(day).unique()

    for d in unique_days:
        mask = day == d
        sub = df.loc[mask]
        if len(sub) < 100:
            continue
        open_mask = (sub.index.hour == 7) & (sub.index.minute < 60)
        or_bars = sub.loc[open_mask]
        if or_bars.empty:
            or_bars = sub.iloc[:12]
        or_hi = or_bars["high"].max()
        or_lo = or_bars["low"].min()
        post = sub[sub.index.hour >= 8]
        n_trades = 0
        i = 0
        while i < len(post) - rules.max_hold_m5 - 1 and n_trades < MAX_TRADES_PER_DAY:
            row = post.iloc[i]
            side = 0
            if row["close"] > or_hi:
                side = 1
            elif row["close"] < or_lo:
                side = -1
            if side == 0:
                i += 1
                continue
            at = atr.loc[row.name]
            if pd.isna(at) or at <= 0:
                i += 1
                continue
            entry = c.shift(-1).loc[row.name] if row.name in c.index else np.nan
            loc = df.index.get_loc(row.name)
            if isinstance(loc, slice):
                loc = loc.start
            if loc + 1 >= len(c):
                break
            entry = c.iloc[loc + 1]
            sl = entry - side * at * rules.sl_atr
            tp = entry + side * at * rules.tp_atr
            exit_px = c.iloc[loc + 1 + rules.max_hold_m5]
            for j in range(1, rules.max_hold_m5 + 1):
                if loc + 1 + j >= len(c):
                    break
                hi, lo = h.iloc[loc + 1 + j], l.iloc[loc + 1 + j]
                if side > 0 and (lo <= sl or hi >= tp):
                    exit_px = tp if hi >= tp else sl
                    break
                if side < 0 and (hi >= sl or lo <= tp):
                    exit_px = tp if lo <= tp else sl
                    break
                exit_px = c.iloc[loc + 1 + j]
            gross = side * (exit_px - entry) / entry - COST_BPS / 10000.0
            rets.append(gross)
            idx.append(df.index[loc + 1])
            n_trades += 1
            i += rules.max_hold_m5 + 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def trades_d1_close_bias(d1: pd.DataFrame, rules: Rules) -> pd.Series:
    """One signal per day from prior D1 candle color; entry next D1 open proxy."""
    c = d1["close"].astype(float)
    o = d1["open"].astype(float)
    h = d1["high"].astype(float)
    l = d1["low"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    rets, idx = [], []
    for i in range(20, len(d1) - 2):
        bull = c.iloc[i] > o.iloc[i]
        bear = c.iloc[i] < o.iloc[i]
        side = 1 if bull else (-1 if bear else 0)
        if side == 0:
            continue
        a = atr.iloc[i]
        if pd.isna(a):
            continue
        entry = o.iloc[i + 1]
        sl = entry - side * a * rules.sl_atr
        tp = entry + side * a * rules.tp_atr
        hi, lo, cl = h.iloc[i + 1], l.iloc[i + 1], c.iloc[i + 1]
        if side > 0 and (lo <= sl or hi >= tp):
            exit_px = tp if hi >= tp else sl
        elif side < 0 and (hi >= sl or lo <= tp):
            exit_px = tp if lo <= tp else sl
        else:
            exit_px = cl
        gross = side * (exit_px - entry) / entry - COST_BPS / 10000.0
        rets.append(gross)
        idx.append(d1.index[i + 1])
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def trades_ema_pullback_m5(df: pd.DataFrame, rules: Rules, ema: int = 21) -> pd.Series:
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    o = df["open"].astype(float)
    ema_s = c.ewm(span=ema, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    up = (c > ema_s) & (ema_s > ema_s.shift(2))
    dn = (c < ema_s) & (ema_s < ema_s.shift(2))
    day = df.index.normalize()

    rets, idx = [], []
    counters: dict = {}

    i = ema + 5
    while i < len(c) - rules.max_hold_m5 - 1:
        d = day[i]
        counters.setdefault(d, 0)
        if counters[d] >= MAX_TRADES_PER_DAY:
            i += 1
            continue
        side = 0
        if up.iloc[i] and l.iloc[i] <= ema_s.iloc[i] and c.iloc[i] > o.iloc[i]:
            side = 1
        elif dn.iloc[i] and h.iloc[i] >= ema_s.iloc[i] and c.iloc[i] < o.iloc[i]:
            side = -1
        if side == 0:
            i += 1
            continue
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            i += 1
            continue
        entry = c.iloc[i + 1]
        sl = entry - side * a * rules.sl_atr
        tp = entry + side * a * rules.tp_atr
        exit_px = c.iloc[i + 1 + rules.max_hold_m5]
        for j in range(1, rules.max_hold_m5 + 1):
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
        idx.append(df.index[i + 1])
        counters[d] += 1
        i += rules.max_hold_m5 + 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def summarize(name: str, sym: str, r: pd.Series, rules: Rules) -> list[dict]:
    rows = []
    if r.empty:
        return rows
    trades_per_day = r.groupby(r.index.normalize()).size()
    for split, (a, b) in SPLITS.items():
        sub = r[(r.index >= a) & (r.index < b)]
        if len(sub) < 20:
            continue
        wins = sub[sub > 0]
        losses = sub[sub < 0]
        avg_w = wins.mean() * 100 if len(wins) else 0
        avg_l = losses.mean() * 100 if len(losses) else 0
        eq = (1 + sub).cumprod()
        tpd = trades_per_day.reindex(sub.index.normalize().unique()).mean()
        rows.append(
            {
                "strategy": name,
                "symbol": sym,
                "split": split,
                "trades": len(sub),
                "trades_per_day_avg": round(float(tpd), 2) if pd.notna(tpd) else 0,
                "winrate_pct": round(100 * (sub > 0).mean(), 1),
                "avg_win_pct": round(avg_w, 3),
                "avg_loss_pct": round(avg_l, 3),
                "expectancy_pct": round(sub.mean() * 100, 4),
                "total_pct": round((eq.iloc[-1] - 1) * 100, 2),
                "pf": round(wins.sum() / abs(losses.sum()), 2) if len(losses) and losses.sum() != 0 else None,
                "sl_atr": rules.sl_atr,
                "tp_atr": rules.tp_atr,
            }
        )
    return rows


def main() -> int:
    print("=== Breakeven win rate (need MORE than 50% at 1:1) ===")
    for sl, tp in [(1.0, 1.0), (1.0, 1.5), (1.0, 2.0), (1.25, 2.5)]:
        be = 100 * breakeven_winrate(tp, sl)
        print(f"  SL={sl} ATR, TP={tp} ATR -> breakeven win ~ {be:.1f}% (with ~3bp cost)")

    rows = []
    configs = [
        (Rules("orb_m5_10perday", 1.0, 2.0), "m5", "orb"),
        (Rules("ema21_pullback_m5", 1.25, 2.0), "m5", "ema"),
        (Rules("d1_candle_color", 1.5, 2.0), "d1", "d1"),
    ]
    for sym in SYMS:
        m5 = load_m5(sym)
        d1 = load_d1(sym)
        for rules, tf, kind in configs:
            if kind == "orb":
                r = trades_orb_m5(m5, rules)
            elif kind == "ema":
                r = trades_ema_pullback_m5(m5, rules)
            else:
                r = trades_d1_close_bias(d1, rules)
            rows.extend(summarize(rules.name, sym, r, rules))

    df = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print("\n=== OHLC / multi-trade research (EURUSD) ===")
    print(df.to_string(index=False))
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
