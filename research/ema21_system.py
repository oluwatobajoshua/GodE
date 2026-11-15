#!/usr/bin/env python3
"""EMA 21 system search — design / validation / OOS (no live promises).

Run:
    .\\.venv\\Scripts\\python.exe research\\ema21_system.py
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
OUT = HERE / "results" / "ema21_system_summary.csv"
MANIFEST = HERE / "results" / "ema21_manifest.json"
EMA = 21

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}

COST_BPS = 4.0


@dataclass(frozen=True)
class Cfg:
    tf: str
    pullback_only: bool
    min_adx: float
    sl_atr: float
    tp_atr: float
    max_hold: int
    session: bool
    h1_filter: bool

    def name(self) -> str:
        return (
            f"{self.tf}_pb{int(self.pullback_only)}_adx{self.min_adx}_"
            f"sl{self.sl_atr}_tp{self.tp_atr}_h{self.max_hold}_"
            f"sess{int(self.session)}_h1{int(self.h1_filter)}"
        )


def load(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def _adx(h: pd.Series, l: pd.Series, c: pd.Series, w: int = 14) -> pd.Series:
    plus_dm = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr14 = tr.rolling(w).mean()
    pdi = 100 * (plus_dm.rolling(w).mean() / atr14)
    mdi = 100 * (minus_dm.rolling(w).mean() / atr14)
    dx = (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan) * 100
    return dx.rolling(w).mean()


def regime(c: pd.Series, ema_s: pd.Series, slope: int = 2) -> pd.Series:
    up = (c > ema_s) & (ema_s > ema_s.shift(slope))
    dn = (c < ema_s) & (ema_s < ema_s.shift(slope))
    reg = pd.Series(0, index=c.index)
    reg[up] = 1
    reg[dn] = -1
    return reg


def backtest(df: pd.DataFrame, cfg: Cfg, h1_reg: pd.Series | None = None) -> pd.Series:
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    o = df["open"].astype(float)
    ema_s = c.ewm(span=EMA, adjust=False).mean()
    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1).rolling(14).mean()
    reg = regime(c, ema_s)
    adx = _adx(h, l, c)

    pull_long = (reg > 0) & (l <= ema_s + atr * 0.3) & (c > o) & (c > ema_s)
    pull_short = (reg < 0) & (h >= ema_s - atr * 0.3) & (c < o) & (c < ema_s)
    brk_long = (reg > 0) & (c > h.shift(1)) & (c > ema_s)
    brk_short = (reg < 0) & (c < l.shift(1)) & (c < ema_s)
    if cfg.pullback_only:
        long_sig = pull_long
        short_sig = pull_short
    else:
        long_sig = pull_long | brk_long
        short_sig = pull_short | brk_short

    long_sig &= adx >= cfg.min_adx
    short_sig &= adx >= cfg.min_adx

    if cfg.session:
        hours = df.index.hour
        in_sess = (hours >= 7) & (hours <= 16)
        long_sig &= in_sess
        short_sig &= in_sess

    if cfg.h1_filter and h1_reg is not None:
        aligned = h1_reg.reindex(df.index, method="ffill").fillna(0)
        long_sig &= aligned > 0
        short_sig &= aligned < 0

    rets = []
    idx = []
    i = EMA + 25
    while i < len(c) - cfg.max_hold - 2:
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
        sl = entry - side * a * cfg.sl_atr
        tp = entry + side * a * cfg.tp_atr
        exit_px = c.iloc[i + 1 + cfg.max_hold]
        for j in range(1, cfg.max_hold + 1):
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
        i += cfg.max_hold + 1
    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def metrics(r: pd.Series, tf: str = "M5") -> dict:
    r = r.dropna()
    if len(r) < 10:
        return {"n": len(r), "cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0, "pf": 0.0, "winrate": 0.0}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-6)
    cagr = (eq.iloc[-1] ** (1 / years) - 1) * 100
    bpy = 252 * 24 if tf == "H1" else 252 * 288
    sh = r.mean() / r.std() * np.sqrt(bpy) if r.std() > 0 else 0.0
    maxdd = (eq / eq.cummax() - 1).min() * 100
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0.0
    return {
        "n": len(r),
        "cagr": round(cagr, 2),
        "sharpe": round(sh, 2),
        "maxdd": round(maxdd, 2),
        "pf": round(pf, 2),
        "winrate": round(100 * (r > 0).mean(), 1),
    }


def split_part(r: pd.Series, start: str, end: str) -> pd.Series:
    return r[(r.index >= start) & (r.index < end)]


def main() -> int:
    sym = "EURUSD"
    m5 = load(sym, "M5")
    h1 = load(sym, "H1")
    h1_reg = regime(h1["close"].astype(float), h1["close"].astype(float).ewm(span=EMA, adjust=False).mean())

    grid: list[Cfg] = []
    for tf, h1f in (("M5", True), ("M5", False), ("H1", False)):
        for pb in (True, False):
            for adx in (18.0, 22.0, 26.0):
                for sl, tp in ((1.0, 2.0), (1.25, 2.5), (1.5, 2.0)):
                    for hold in (6, 12, 24):
                        for sess in (True, False):
                            grid.append(
                                Cfg(tf, pb, adx, sl, tp, hold, sess, h1f if tf == "M5" else False)
                            )

    rows = []
    best_pass = None
    for cfg in grid:
        df = m5 if cfg.tf == "M5" else h1
        h1f = h1_reg if cfg.h1_filter else None
        r = backtest(df, cfg, h1f)
        if r.empty:
            continue
        split_metrics = {}
        ok = True
        for split, (a, b) in SPLITS.items():
            part = split_part(r, a, b)
            m = metrics(part, cfg.tf)
            m["split"] = split
            split_metrics[split] = m
            rows.append({"symbol": sym, "config": cfg.name(), **m})
            if split in ("validation", "OOS"):
                if m["cagr"] <= 0 or m["sharpe"] <= 0 or m["pf"] < 1.0:
                    ok = False
        if ok and best_pass is None:
            best_pass = cfg

    summary = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT, index=False)

    manifest = {
        "ema": EMA,
        "symbol": sym,
        "cost_bps": COST_BPS,
        "promoted": best_pass is not None,
        "config": best_pass.name() if best_pass else None,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== EMA 21 system (EURUSD) ===")
    if best_pass:
        print(f"PROMOTED: {best_pass.name()}")
        for split in SPLITS:
            sub = summary[(summary.config == best_pass.name()) & (summary.split == split)]
            if not sub.empty:
                print(sub.to_string(index=False))
    else:
        print("NO config passed validation + OOS (cagr>0, sharpe>0, pf>=1).")
        full = (
            summary[summary.split == "OOS"]
            .sort_values("sharpe", ascending=False)
            .drop_duplicates("config")
            .head(8)
        )
        print("Least-bad OOS configs:")
        print(full[["config", "cagr", "sharpe", "pf", "n", "maxdd"]].to_string(index=False))
    print(f"\nWrote {OUT} and {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
