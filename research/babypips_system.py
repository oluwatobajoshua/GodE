#!/usr/bin/env python3
"""BabyPips School of Pipsology — rule-based stack (Elementary → High School + risk).

Maps public curriculum themes to testable rules:
- Grade 1–2: S/R + bullish/bearish engulfing at level
- Grade 3–4: 50–61.8% fib + EMA(21/50) trend
- Grade 6: RSI(14) not extreme
- Grade 8: Classic daily pivot S1/R1 confluence
- Grade 12: H4 bias, H1 entry
- Senior: 1:2 R:R, 3 trades/day max, 3bp cost

Proof rule: design / validation / OOS — cagr>0, sharpe>0, pf>=1 (monthly where applicable).

Also reports BabyPips carry chapter vs existing carry_mom_filter.

Run:
    .\\.venv\\Scripts\\python.exe research\\babypips_system.py

Course reference: https://www.babypips.com/learn/forex
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "babypips_system_summary.csv"
MANIFEST = HERE / "results" / "babypips_manifest.json"
COST_BPS = 3.0
MAX_DAY = 3

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}


def load_h1(sym: str) -> pd.DataFrame:
    for p in (DATA / "histdata" / f"{sym}_H1.csv", DATA / f"{sym}_H1.csv"):
        if p.exists():
            d = pd.read_csv(p, parse_dates=["time"])
            d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
            return d.set_index("time").sort_index()
    raise FileNotFoundError(sym)


def to_h4(h1: pd.DataFrame) -> pd.DataFrame:
    o = h1["open"].astype(float).resample("4h").first()
    h = h1["high"].astype(float).resample("4h").max()
    l = h1["low"].astype(float).resample("4h").min()
    c = h1["close"].astype(float).resample("4h").last()
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()


def daily_pivots(h1: pd.DataFrame) -> pd.DataFrame:
    d1 = h1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    p = (d1["high"] + d1["low"] + d1["close"]) / 3.0
    s1 = 2 * p - d1["high"]
    r1 = 2 * p - d1["low"]
    piv = pd.DataFrame({"P": p, "S1": s1, "R1": r1}, index=d1.index)
    return piv.shift(1)


def engulf(o, c, po, pc, side: int) -> bool:
    if side > 0:
        return c > o and po > pc and c > po and o < pc
    return c < o and po < pc and c < po and o > pc


def backtest_pipsology(h1: pd.DataFrame) -> pd.Series:
    h4 = to_h4(h1)
    c = h1["close"].astype(float)
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    o = h1["open"].astype(float)

    ema21 = c.ewm(span=21, adjust=False).mean()
    h4_c = h4["close"].reindex(c.index, method="ffill")
    h4_ema = h4["close"].ewm(span=50, adjust=False).mean().reindex(c.index, method="ffill")

    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    delta = c.diff()
    rs = delta.clip(lower=0).rolling(14).mean() / (-delta.clip(upper=0)).rolling(14).mean().replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)

    swing = 20
    sh = h.rolling(swing).max().shift(1)
    sl = l.rolling(swing).min().shift(1)
    fib50 = sl + (sh - sl) * 0.5
    fib618 = sl + (sh - sl) * 0.618

    piv = daily_pivots(h1)
    s1 = piv["S1"].reindex(c.index, method="ffill")
    r1 = piv["R1"].reindex(c.index, method="ffill")

    day = h1.index.normalize()
    counts: dict = {}
    rets, idx = [], []

    for i in range(60, len(h1) - 12):
        d = day[i]
        counts.setdefault(d, 0)
        if counts[d] >= MAX_DAY:
            continue

        if pd.isna(h4_c.iloc[i]) or pd.isna(h4_ema.iloc[i]):
            continue
        up = h4_c.iloc[i] > h4_ema.iloc[i] and c.iloc[i] > ema21.iloc[i]
        dn = h4_c.iloc[i] < h4_ema.iloc[i] and c.iloc[i] < ema21.iloc[i]
        if not up and not dn:
            continue
        side = 1 if up else -1

        at_level = False
        if side > 0:
            at_level = (l.iloc[i] <= sh.iloc[i] * 1.001 and l.iloc[i] >= fib618.iloc[i]) or (
                abs(l.iloc[i] - s1.iloc[i]) < atr.iloc[i] * 0.3
            )
        else:
            at_level = (h.iloc[i] >= sl.iloc[i] * 0.999 and h.iloc[i] <= fib50.iloc[i]) or (
                abs(h.iloc[i] - r1.iloc[i]) < atr.iloc[i] * 0.3
            )
        if not at_level:
            continue

        if not engulf(o.iloc[i], c.iloc[i], o.iloc[i - 1], c.iloc[i - 1], side):
            continue

        r = rsi.iloc[i]
        if side > 0 and not (35 <= r <= 58):
            continue
        if side < 0 and not (42 <= r <= 65):
            continue

        a = atr.iloc[i]
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
        counts[d] += 1

    return pd.Series(rets, index=pd.DatetimeIndex(idx))


def metrics(r: pd.Series) -> dict:
    if len(r) < 20:
        return {"n": len(r), "cagr": 0, "sharpe": 0, "pf": 0, "winrate": 0, "total_pct": 0}
    eq = (1 + r).cumprod()
    years = max((r.index[-1] - r.index[0]).days / 365.25, 1e-6)
    cagr = (eq.iloc[-1] ** (1 / years) - 1) * 100
    bpy = 252 * 24
    sharpe = r.mean() / r.std() * np.sqrt(bpy) if r.std() > 0 else 0
    wins, losses = r[r > 0], r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    return {
        "n": len(r),
        "cagr": round(cagr, 2),
        "sharpe": round(sharpe, 2),
        "pf": round(pf, 2),
        "winrate": round(100 * (r > 0).mean(), 1),
        "total_pct": round((eq.iloc[-1] - 1) * 100, 2),
    }


def carry_row() -> dict:
    p = HERE / "results" / "fx_evidence_summary.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    sub = df[(df.strategy == "carry_mom_filter") & (df.split.str.contains("OOS|validation|design", regex=True))]
    return {"carry_babypips_chapter": sub.to_dict(orient="records")}


def main() -> int:
    rows = []
    promoted = False
    for sym in ("EURUSD", "GBPUSD"):
        h1 = load_h1(sym)
        r = backtest_pipsology(h1)
        for split, (a, b) in SPLITS.items():
            sub = r[(r.index >= a) & (r.index < b)]
            m = metrics(sub)
            rows.append({"strategy": "pipsology_mtf_pa", "symbol": sym, "split": split, **m})
            if split == "OOS" and m["pf"] >= 1 and m["cagr"] > 0 and m["sharpe"] > 0:
                promoted = True

    summary = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT, index=False)

    manifest = {
        "source": "https://www.babypips.com/learn/forex",
        "rules": [
            "H4 EMA50 trend + H1 EMA21",
            "S/R swing + fib 50-61.8 + pivot S1/R1",
            "Engulfing candle only",
            "RSI band filter",
            "1:2 R:R, 3 trades/day, 3bp cost",
        ],
        "pipsology_promoted": promoted,
        "automated_live_candidate": "carry_mom_filter",
        "pipsology_note": "BabyPips TA stack alone did not pass OOS on HistData if promoted=false",
        "carry_chapter_proof": carry_row(),
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== BabyPips Pipsology price-action (H1/H4) ===")
    print(summary.to_string(index=False))
    print("\n=== BabyPips Carry Trade chapter (already in repo) ===")
    print("Use SovereignGodsEyeEA SOV_CARRY_MOM + evidence_directives.json")
    print(f"\nPromoted pipsology TA: {promoted}")
    print(f"Wrote {OUT}, {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
