#!/usr/bin/env python3
"""Test: profit from sustained closes above/below EMA (trend ride).

User hypothesis: many consecutive candles on one side of EMA should be tradable.

Rules scanned (design pick threshold, validation rank, OOS report only):
  - streak: min consecutive closes above/below EMA21 or full stack (21>100>SMMA100)
  - entry: first bar when streak reaches N (one trade per wave)
  - exit: close crosses back through EMA21 OR max hold bars OR ATR SL/TP

Run:
    .\\.venv\\Scripts\\python.exe research\\ema_streak_ride.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT_GRID = HERE / "results" / "ema_streak_ride_grid.csv"
OUT_MONTHLY = HERE / "results" / "ema_streak_ride_monthly.csv"
OUT_MANIFEST = HERE / "results" / "ema_streak_ride_manifest.json"

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}
SYMS = ("USDJPY", "EURUSD", "GBPUSD")
COST_BPS = 3.0
MAX_DAY = 5


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


def streak_mask(mask: pd.Series) -> np.ndarray:
    n = len(mask)
    s = np.zeros(n, dtype=int)
    for i in range(n):
        if bool(mask.iloc[i]):
            s[i] = (s[i - 1] + 1) if i > 0 else 1
    return s


def streak_ema(close: pd.Series, ema: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    n = len(close)
    up = np.zeros(n, dtype=int)
    dn = np.zeros(n, dtype=int)
    for i in range(n):
        if close.iloc[i] > ema.iloc[i]:
            up[i] = up[i - 1] + 1 if i > 0 else 1
        if close.iloc[i] < ema.iloc[i]:
            dn[i] = dn[i - 1] + 1 if i > 0 else 1
    return up, dn


@dataclass(frozen=True)
class Cfg:
    sym: str
    filter_mode: str  # ema21 | stack
    min_streak: int
    exit_mode: str  # ema_break | hold12 | atr12
    sl_atr: float = 1.0
    tp_atr: float = 2.0

    def key(self) -> str:
        return f"{self.sym}_{self.filter_mode}_s{self.min_streak}_{self.exit_mode}"


def backtest(h1: pd.DataFrame, cfg: Cfg) -> pd.Series:
    c = h1["close"].astype(float)
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema100 = c.ewm(span=100, adjust=False).mean()
    sm = smma(c, 100)
    stack_long = (c > ema21) & (ema21 > ema100) & (c > sm)
    stack_short = (c < ema21) & (ema21 < ema100) & (c < sm)
    if cfg.filter_mode == "stack":
        up = streak_mask(stack_long)
        dn = streak_mask(stack_short)
    else:
        up, dn = streak_ema(c, ema21)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    day = h1.index.normalize()
    counts: dict = {}
    rets, idx = [], []
    for i in range(120, len(h1) - 24):
        dk = day[i]
        counts.setdefault(dk, 0)
        if counts[dk] >= MAX_DAY:
            continue
        side = 0
        if up[i] == cfg.min_streak and up[i - 1] == cfg.min_streak - 1:
            side = 1
        elif dn[i] == cfg.min_streak and dn[i - 1] == cfg.min_streak - 1:
            side = -1
        if side == 0:
            continue

        entry = c.iloc[i + 1]
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            continue
        hold = 24
        sl_px = entry - side * a * cfg.sl_atr
        tp_px = entry + side * a * cfg.tp_atr
        last_k = min(i + 1 + hold, len(c) - 1)
        exit_px = c.iloc[last_k]
        for j in range(1, hold + 1):
            k = i + 1 + j
            if k >= len(c):
                break
            if cfg.exit_mode == "ema_break":
                if side > 0 and c.iloc[k] < ema21.iloc[k]:
                    exit_px = c.iloc[k]
                    break
                if side < 0 and c.iloc[k] > ema21.iloc[k]:
                    exit_px = c.iloc[k]
                    break
            elif cfg.exit_mode == "atr12":
                hi, lo = h.iloc[k], l.iloc[k]
                if side > 0 and (lo <= sl_px or hi >= tp_px):
                    exit_px = tp_px if hi >= tp_px else sl_px
                    break
                if side < 0 and (hi >= sl_px or lo <= tp_px):
                    exit_px = tp_px if lo <= tp_px else sl_px
                    break
            elif cfg.exit_mode == "hold12" and j >= 12:
                exit_px = c.iloc[k]
                break
            exit_px = c.iloc[k]
        gross = side * (exit_px - entry) / entry - COST_BPS / 10000.0
        rets.append(gross)
        idx.append(h1.index[i + 1])
        counts[dk] += 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def stats(r: pd.Series) -> dict:
    if len(r) < 30:
        return {"n": len(r), "exp_bps": -999, "pf": 0, "winrate": 0, "total_pct": 0}
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    return {
        "n": len(r),
        "exp_bps": round(r.mean() * 10000, 2),
        "pf": round(pf, 2),
        "winrate": round(100 * (r > 0).mean(), 1),
        "total_pct": round(((1 + r).prod() - 1) * 100, 2),
    }


def unconditional_edge(h1: pd.DataFrame, min_streak: int = 5) -> dict:
    """Avg forward return after N closes above EMA21 (no exit logic, 12h hold)."""
    c = h1["close"].astype(float)
    ema21 = c.ewm(span=21, adjust=False).mean()
    up, _ = streak_ema(c, ema21)
    fwd = []
    for i in range(120, len(c) - 13):
        if up[i] >= min_streak and up[i - 1] == min_streak - 1:
            entry = c.iloc[i + 1]
            exit_px = c.iloc[i + 1 + 12]
            fwd.append((exit_px - entry) / entry)
    if len(fwd) < 50:
        return {"n": len(fwd), "mean_bps": 0, "winrate": 0}
    r = np.array(fwd)
    return {
        "n": len(r),
        "mean_bps": round(r.mean() * 10000, 2),
        "winrate": round(100 * (r > 0).mean(), 1),
    }


def regime_hold(h1: pd.DataFrame, use_stack: bool = True) -> pd.Series:
    """Stay long while above stack (or EMA21); one return per completed regime."""
    c = h1["close"].astype(float)
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema100 = c.ewm(span=100, adjust=False).mean()
    sm = smma(c, 100)
    cost = COST_BPS / 10000.0
    rets, idx = [], []
    in_long = in_short = False
    entry = 0.0
    entry_t = None
    for i in range(101, len(c)):
        if use_stack:
            long_on = c.iloc[i] > ema21.iloc[i] and ema21.iloc[i] > ema100.iloc[i] and c.iloc[i] > sm.iloc[i]
            short_on = c.iloc[i] < ema21.iloc[i] and ema21.iloc[i] < ema100.iloc[i] and c.iloc[i] < sm.iloc[i]
        else:
            long_on = c.iloc[i] > ema21.iloc[i]
            short_on = c.iloc[i] < ema21.iloc[i]
        if not in_long and not in_short and long_on and not short_on:
            in_long = True
            entry = c.iloc[i]
            entry_t = h1.index[i]
        elif not in_long and not in_short and short_on and not long_on:
            in_short = True
            entry = c.iloc[i]
            entry_t = h1.index[i]
        elif in_long and not long_on:
            rets.append((c.iloc[i] - entry) / entry - cost)
            idx.append(entry_t)
            in_long = False
        elif in_short and not short_on:
            rets.append((entry - c.iloc[i]) / entry - cost)
            idx.append(entry_t)
            in_short = False
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def main() -> int:
    grid: list[Cfg] = []
    for sym in SYMS:
        for fm in ("ema21", "stack"):
            for ms in (3, 5, 8, 12, 20, 30):
                for ex in ("ema_break", "hold12", "atr12"):
                    grid.append(Cfg(sym, fm, ms, ex))

    rows = []
    for cfg in grid:
        try:
            h1 = load_h1(cfg.sym)
        except FileNotFoundError:
            continue
        r = backtest(h1, cfg)
        row = {"config": cfg.key(), "filter": cfg.filter_mode, "min_streak": cfg.min_streak, "exit": cfg.exit_mode}
        for name, (a, b) in SPLITS.items():
            part = r[(r.index >= a) & (r.index < b)]
            for k, v in stats(part).items():
                row[f"{k}_{name}"] = v
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OUT_GRID, index=False)

    # pick per symbol: best validation exp_bps with n>=50
    picks = []
    for sym in SYMS:
        sub = df[df["config"].str.startswith(sym + "_")]
        sub = sub[sub.n_validation >= 50].sort_values("exp_bps_validation", ascending=False)
        if len(sub):
            picks.append(sub.iloc[0])

    print("=== Regime hold (stay in while stack intact, exit when broken) ===")
    for sym in SYMS:
        try:
            r = regime_hold(load_h1(sym), True)
            from period_returns import frame_to_monthly_rows

            m = frame_to_monthly_rows(f"{sym}_regime_stack", r)
            oos = [x for x in m if x["date"] >= "2020-01-01"]
            if oos:
                avg = sum(x["return_pct"] for x in oos) / len(oos)
                print(sym, f"regimes={len(r)} OOS months={len(oos)} avg_month_pct={avg:.2f}")
        except FileNotFoundError:
            pass

    print("\n=== EMA streak ride (unconditional 12h after streak hits 5) ===")
    for sym in SYMS:
        try:
            h1 = load_h1(sym)
            print(sym, unconditional_edge(h1, 5), unconditional_edge(h1, 10))
        except FileNotFoundError:
            pass

    from period_returns import print_split_monthly, publish_results

    best = picks[0] if picks else None
    if best is not None:
        sym = best["config"].split("_")[0]
        cfg = next(c for c in grid if c.key() == best["config"])
        r = backtest(load_h1(sym), cfg)
        publish_results(best["config"], r)
        print_split_monthly(best["config"], r, SPLITS)

    manifest = {
        "hypothesis": "Sustained closes above/below EMA are tradable with streak entry",
        "picks_validation": [p["config"] for p in picks],
        "best_oos_exp_bps": float(best["exp_bps_OOS"]) if best is not None else None,
        "note": "Costs 3bps/side; streak entry on Nth consecutive close",
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT_GRID}, {OUT_MONTHLY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
