#!/usr/bin/env python3
"""Scalp lane — London ORB + EMA21 M5 pullback on broker-native M5.

For traders who want frequency and short holds (not D1 carry). Honest 3-split proof.

Run:
    .\\scripts\\run_scalp_lane.ps1 -Export
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bar_store import _scalp_m5_policy, load_m5_scalp
from m5_execution_cost import round_trip_cost_price
from period_returns import print_split_monthly, publish_results

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CFG = REPO / "configs" / "scalp_lane.json"
OUT_MAN = HERE / "results" / "scalp_lane_manifest.json"
OUT_SUM = HERE / "results" / "scalp_lane_summary.csv"

SPLITS_HIST = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}


def _splits(cfg: dict) -> dict[str, tuple[str, str]]:
    bars = cfg.get("bars") or {}
    if _scalp_m5_policy() == "mt5_only":
        w = bars.get("tester_oos") or {}
        a, b = w.get("from", "2024-07-01"), w.get("to", "2026-01-01")
        span = bars.get("mt5_explore_splits")
        if span:
            return {"tester_oos": (a, b), **span}
        return {"tester_oos": (a, b)}
    return dict(SPLITS_HIST)


def _metrics(r: pd.Series) -> dict:
    if r.empty:
        return {"n": 0, "total_pct": 0.0, "pf": 0.0, "winrate": 0.0}
    wins = r[r > 0]
    losses = r[r <= 0]
    gp = wins.sum()
    gl = -losses.sum()
    return {
        "n": int(len(r)),
        "total_pct": round(float(r.sum() * 100), 2),
        "pf": round(float(gp / gl), 2) if gl > 0 else 0.0,
        "winrate": round(float((r > 0).mean() * 100), 1),
    }


def _rt_cost(sym: str, row: pd.Series, orb: dict) -> float:
    slip = float(orb.get("slippage_points", 1))
    return round_trip_cost_price(sym, row, slippage_pts=slip)


def london_orb(df: pd.DataFrame, sym: str, cfg: dict) -> pd.DataFrame:
    orb = cfg["london_orb"]
    box_hours = set(int(x) for x in orb.get("box_hours_utc", [6]))
    hours = set(int(x) for x in orb["trade_hours_utc"])
    max_hold = int(orb["max_hold_bars"])
    buf = float(orb["sl_box_buffer_atr"])
    tp_r = float(orb["tp_r_multiple"])
    max_day = int(orb["max_trades_per_day"])

    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    df = df.copy()
    df["date"] = df.index.normalize()
    trades = []

    for day, g in df.groupby("date"):
        box = g[g.index.hour.isin(box_hours)]
        if len(box) < 4:
            continue
        box_hi = float(box["high"].max())
        box_lo = float(box["low"].min())
        if box_hi <= box_lo:
            continue
        a_ref = float(atr.loc[box.index].median())
        if not np.isfinite(a_ref) or a_ref <= 0:
            continue
        day_trades = 0
        for i in range(len(g) - 2):
            t = g.index[i]
            if t.hour not in hours:
                continue
            if day_trades >= max_day:
                break
            row = g.iloc[i]
            side = 0
            if float(row["high"]) > box_hi and float(row["close"]) > box_hi:
                side = 1
            elif float(row["low"]) < box_lo and float(row["close"]) < box_lo:
                side = -1
            if side == 0:
                continue
            ci = df.index.get_loc(t)
            if ci + 1 >= len(df):
                break
            entry_row = df.iloc[ci + 1]
            entry = float(entry_row["open"])
            cost = _rt_cost(sym, entry_row, orb)
            sl = (box_lo - buf * a_ref) if side > 0 else (box_hi + buf * a_ref)
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            tp = entry + side * risk * tp_r
            exit_px = entry
            exit_t = df.index[ci + 1]
            for j in range(2, max_hold + 2):
                if ci + j >= len(df):
                    break
                bar = df.iloc[ci + j]
                hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])
                if side > 0 and lo <= sl:
                    exit_px = sl
                    exit_t = df.index[ci + j]
                    break
                if side < 0 and hi >= sl:
                    exit_px = sl
                    exit_t = df.index[ci + j]
                    break
                if side > 0 and hi >= tp:
                    exit_px = tp
                    exit_t = df.index[ci + j]
                    break
                if side < 0 and lo <= tp:
                    exit_px = tp
                    exit_t = df.index[ci + j]
                    break
                exit_px = cl
                exit_t = df.index[ci + j]
            pnl = side * (exit_px - entry) - cost
            trades.append(
                {
                    "entry_time": df.index[ci + 1],
                    "exit_time": exit_t,
                    "side": "LONG" if side > 0 else "SHORT",
                    "return_pct": pnl / entry * 100.0,
                    "strategy": "london_orb",
                }
            )
            day_trades += 1
    return pd.DataFrame(trades)


def ema21_pullback(df: pd.DataFrame, sym: str, cfg: dict) -> pd.DataFrame:
    ecfg = cfg["ema21_pullback"]
    hours = set(int(x) for x in ecfg["trade_hours_utc"])
    min_adx = float(ecfg["min_adx"])
    sl_atr = float(ecfg["sl_atr"])
    tp_atr = float(ecfg["tp_atr"])
    max_hold = int(ecfg["max_hold_bars"])
    slip = float(ecfg.get("slippage_points", 1))

    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    o = df["open"].astype(float)
    ema = c.ewm(span=21, adjust=False).mean()
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    plus_dm = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    pdi = 100 * (plus_dm.rolling(14).mean() / atr)
    mdi = 100 * (minus_dm.rolling(14).mean() / atr)
    adx = ((pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan) * 100).rolling(14).mean()

    up = (c > ema) & (ema > ema.shift(2))
    dn = (c < ema) & (ema < ema.shift(2))
    pull_long = up & (l <= ema + atr * 0.25) & (c > o) & (c > ema)
    pull_short = dn & (h >= ema - atr * 0.25) & (c < o) & (c < ema)
    trades = []
    i = 50
    while i < len(df) - max_hold - 2:
        t = df.index[i]
        if t.hour not in hours or pd.isna(adx.iloc[i]) or adx.iloc[i] < min_adx:
            i += 1
            continue
        side = 0
        if bool(pull_long.iloc[i]):
            side = 1
        elif bool(pull_short.iloc[i]):
            side = -1
        if side == 0:
            i += 1
            continue
        entry_row = df.iloc[i + 1]
        entry = float(entry_row["open"])
        cost = round_trip_cost_price(sym, entry_row, slippage_pts=slip)
        a = float(atr.iloc[i])
        if not np.isfinite(a) or a <= 0:
            i += 1
            continue
        sl = entry - side * sl_atr * a
        tp = entry + side * tp_atr * a
        exit_px = entry
        exit_t = df.index[i + 1]
        for j in range(1, max_hold + 1):
            if i + 1 + j >= len(df):
                break
            bar = df.iloc[i + 1 + j]
            hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])
            if side > 0 and lo <= sl:
                exit_px, exit_t = sl, df.index[i + 1 + j]
                break
            if side < 0 and hi >= sl:
                exit_px, exit_t = sl, df.index[i + 1 + j]
                break
            if side > 0 and hi >= tp:
                exit_px, exit_t = tp, df.index[i + 1 + j]
                break
            if side < 0 and lo <= tp:
                exit_px, exit_t = tp, df.index[i + 1 + j]
                break
            exit_px, exit_t = cl, df.index[i + 1 + j]
        pnl = side * (exit_px - entry) - cost
        trades.append(
            {
                "entry_time": df.index[i + 1],
                "exit_time": exit_t,
                "side": "LONG" if side > 0 else "SHORT",
                "return_pct": pnl / entry * 100.0,
                "strategy": "ema21_pullback",
            }
        )
        i += max_hold // 2
    return pd.DataFrame(trades)


def run_symbol(sym: str, cfg: dict) -> dict:
    splits = _splits(cfg)
    m5, meta = load_m5_scalp(sym)
    meta["bars"] = len(m5)
    meta["from"] = str(m5.index.min())
    meta["to"] = str(m5.index.max())
    meta["splits"] = list(splits.keys())
    out: dict = {"symbol": sym, "bar_meta": meta, "strategies": {}}
    runners = {
        "london_orb": lambda: london_orb(m5, sym, cfg),
        "ema21_pullback": lambda: ema21_pullback(m5, sym, cfg),
    }
    for name in cfg.get("strategies", runners.keys()):
        tr = runners[name]()
        if tr.empty:
            out["strategies"][name] = {"trades": 0, "error": "no_trades"}
            continue
        r = pd.Series(tr["return_pct"].values / 100.0, index=pd.to_datetime(tr["exit_time"]))
        tag = f"scalp_{sym}_{name}"
        publish_results(tag, r)
        print_split_monthly(tag, r, splits)
        row = {"trades": len(tr), "splits": {}, "full": _metrics(r)}
        for split, (a, b) in splits.items():
            part = r[(r.index >= pd.Timestamp(a)) & (r.index < pd.Timestamp(b))]
            row["splits"][split] = _metrics(part)
        out["strategies"][name] = row
        print(f"\n{sym} {name}: n={len(tr)} full%={row['full']['total_pct']} pf={row['full']['pf']}")
    return out


def main() -> int:
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    rows = []
    manifest = {
        "config": str(CFG),
        "m5_policy": _scalp_m5_policy(),
        "symbols": {},
    }
    for sym in cfg.get("symbols", ["GBPUSD", "EURUSD"]):
        block = run_symbol(sym.upper(), cfg)
        manifest["symbols"][sym.upper()] = block
        for strat, data in block.get("strategies", {}).items():
            if "splits" not in data:
                continue
            oos_key = (cfg.get("promotion") or {}).get("oos_split_key", "OOS")
            oos = data["splits"].get(oos_key) or data["splits"].get("OOS", {})
            rows.append(
                {
                    "symbol": sym,
                    "strategy": strat,
                    "trades": data.get("trades"),
                    "full_pct": data["full"].get("total_pct"),
                    "oos_n": oos.get("n"),
                    "oos_pct": oos.get("total_pct"),
                    "oos_pf": oos.get("pf"),
                }
            )
    OUT_MAN.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    if rows:
        pd.DataFrame(rows).to_csv(OUT_SUM, index=False)
        print(f"\nWrote {OUT_SUM} and {OUT_MAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
