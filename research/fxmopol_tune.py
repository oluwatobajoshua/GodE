#!/usr/bin/env python3
"""FXMOPOL tuner — design select, validation pick, OOS report (no OOS fitting).

Online-informed variants:
- Pullback at S/R+fib (default)
- H1 breakout of swing (DailyForex / Formiq: break > bounce on H1)
- Good sessions only (07–11 & 13–17 GMT)
- Pin-only at pivots (PriceActionNinja confluence filter)
- Wider R:R 1:2.5 / 1:3

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_tune.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from fxmopol_chart_core import session_allows_new_entry

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "fxmopol_tune.csv"
MANIFEST = HERE / "results" / "fxmopol_manifest.json"
SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}
COST_BPS = 3.0


def load_h1(sym: str) -> pd.DataFrame:
    for p in (DATA / "histdata" / f"{sym}_H1.csv", DATA / f"{sym}_H1.csv"):
        if p.exists():
            d = pd.read_csv(p, parse_dates=["time"])
            d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
            return d.set_index("time").sort_index()
    raise FileNotFoundError(sym)


@dataclass(frozen=True)
class Cfg:
    mode: str  # pullback | breakout
    session: bool
    pin_only: bool
    h4_ema: int
    sl_atr: float
    tp_atr: float
    max_day: int = 3

    def key(self) -> str:
        return (
            f"{self.mode}_sess{int(self.session)}_pin{int(self.pin_only)}"
            f"_h4{self.h4_ema}_sl{self.sl_atr}_tp{self.tp_atr}"
        )


def pivots(h1: pd.DataFrame) -> pd.DataFrame:
    d1 = h1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    p = (d1["high"] + d1["low"] + d1["close"]) / 3.0
    return pd.DataFrame({"S1": 2 * p - d1["high"], "R1": 2 * p - d1["low"]}, index=d1.index).shift(1)


def criminal(o, h, l, c, side: int) -> bool:
    body, rng = abs(c - o), h - l
    if rng <= 0 or body / rng < 0.55:
        return False
    return (side > 0 and c > o and (h - c) / rng <= 0.25) or (side < 0 and c < o and (c - l) / rng <= 0.25)


def engulf(o, c, po, pc, side: int) -> bool:
    if side > 0:
        return c > o and pc < po and c > po and o < pc
    return c < o and pc > po and c < po and o > pc


def pin_bar(o, h, l, c, side: int) -> bool:
    body, rng = abs(c - o), h - l
    if rng <= 0 or body <= 0:
        return False
    lo, up = min(o, c) - l, h - max(o, c)
    return (side > 0 and lo >= 2 * body and up <= body) or (side < 0 and up >= 2 * body and lo <= body)


def backtest(h1: pd.DataFrame, cfg: Cfg) -> pd.Series:
    h4 = h1.resample("4h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    c = h1["close"].astype(float)
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    o = h1["open"].astype(float)
    ema21 = c.ewm(span=21, adjust=False).mean()
    h4c = h4["close"].reindex(c.index, method="ffill")
    h4e = h4["close"].ewm(span=cfg.h4_ema, adjust=False).mean().reindex(c.index, method="ffill")
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
        if cfg.session and not session_allows_new_entry(h1.index[i].hour):
            continue
        dk = day[i]
        counts.setdefault(dk, 0)
        if counts[dk] >= cfg.max_day:
            continue
        if pd.isna(h4c.iloc[i]) or pd.isna(h4e.iloc[i]):
            continue
        up = h4c.iloc[i] > h4e.iloc[i] and c.iloc[i] > ema21.iloc[i]
        dn = h4c.iloc[i] < h4e.iloc[i] and c.iloc[i] < ema21.iloc[i]
        if not up and not dn:
            continue
        side = 1 if up else -1
        sh = h.iloc[i - swing : i].max()
        slv = l.iloc[i - swing : i].min()
        a = atr.iloc[i]
        if pd.isna(a) or a <= 0:
            continue

        if cfg.mode == "breakout":
            if side > 0 and c.iloc[i] <= sh:
                continue
            if side < 0 and c.iloc[i] >= slv:
                continue
        else:
            fib618 = slv + (sh - slv) * 0.618
            fib50 = slv + (sh - slv) * 0.5
            if side > 0:
                zone = (l.iloc[i] >= fib618) or abs(l.iloc[i] - s1.iloc[i]) < a * 0.35
            else:
                zone = (h.iloc[i] <= fib50) or abs(h.iloc[i] - r1.iloc[i]) < a * 0.35
            if not zone:
                continue

        if cfg.pin_only:
            pat = pin_bar(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], side)
        else:
            pat = criminal(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], side) or engulf(
                o.iloc[i], c.iloc[i], o.iloc[i - 1], c.iloc[i - 1], side
            ) or pin_bar(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], side)
        if not pat:
            continue
        r = rsi.iloc[i]
        if side > 0 and not (35 <= r <= 62):
            continue
        if side < 0 and not (38 <= r <= 65):
            continue

        entry = c.iloc[i + 1]
        sl_px = entry - side * a * cfg.sl_atr
        tp_px = entry + side * a * cfg.tp_atr
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
    if len(r) < 20:
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


def main() -> int:
    h1 = load_h1("EURUSD")
    grid = []
    for mode in ("pullback", "breakout"):
        for session in (False, True):
            for pin_only in (False, True):
                for h4_ema in (50, 200):
                    for sl, tp in ((1.0, 2.0), (1.0, 2.5), (1.2, 3.0)):
                        grid.append(Cfg(mode, session, pin_only, h4_ema, sl, tp))

    rows = []
    for cfg in grid:
        r = backtest(h1, cfg)
        row = {"config": cfg.key()}
        for prefix, ab in [("design", SPLITS["design"]), ("val", SPLITS["validation"]), ("oos", SPLITS["OOS"])]:
            part = r[(r.index >= ab[0]) & (r.index < ab[1])]
            for k, v in stats(part).items():
                row[f"{k}_{prefix}"] = v
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values("exp_bps_val", ascending=False)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)

    # pick best validation with pf>=1 and exp>0
    candidates = df[(df.pf_val >= 1.0) & (df.exp_bps_val > 0)]
    best = candidates.iloc[0] if len(candidates) else df.iloc[0]
    promoted = bool(len(candidates)) and best.get("pf_oos", 0) >= 1 and best.get("exp_bps_oos", -1) > 0

    manifest = {
        "best_validation_config": best["config"],
        "promoted_oos": promoted,
        "best_oos_pf": float(best.get("pf_oos", 0)),
        "best_oos_exp_bps": float(best.get("exp_bps_oos", 0)),
        "sources": [
            "Formiq S/R bounce vs break H1",
            "DailyForex pullback vs breakout",
            "PriceActionNinja pin at major levels + HTF EMA",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== FXMOPOL tune (top 8 by validation expectancy) ===")
    print(df.head(8).to_string(index=False))
    print(f"\nBest val candidate: {best['config']}")
    print(f"OOS promoted: {promoted} (pf_oos={best.get('pf_oos')}, exp_oos={best.get('exp_bps_oos')})")
    print(f"Wrote {OUT}, {MANIFEST}")
    cfg_best = next(c for c in grid if c.key() == best["config"])
    trade_r = backtest(h1, cfg_best)
    from period_returns import publish_results, print_split_monthly

    publish_results(f"fxmopol_tune_{best['config']}", trade_r)
    print_split_monthly(f"fxmopol_tune", trade_r, SPLITS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
