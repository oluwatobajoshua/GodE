#!/usr/bin/env python3
"""FXMOPOL cross-and-ride research — user EMA21 trigger + structure exits.

Rules (long):
  - After several closes below EMA21, enter on green reclaim (close above EMA21).
  - SL = low of that green trigger; do NOT exit on red below EMA21 alone.
  - Exit all longs when red closes below the *open* of the trigger green.
  - Add on EMA21 retest while trend intact; pyramid allowed.
  - Bulk exit: RSI >= 75, extension from EMA21, or green engulf after far red(s).

Also reports follow-through after first EMA21 close cross.

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_cross_ride.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fxmopol_chart_core import (
    allow_pullback_add,
    allow_trend_entry,
    build_stack,
    correction_exit,
    ema21_cross_close_long,
    ema21_cross_close_short,
    extension_exit_trigger,
    forward_bars_after_cross,
    macro_trend_dir,
    structure_bulk_exit_long,
    structure_bulk_exit_short,
    exit_red_below_trigger_open,
    trend_phase_at,
)
from fxmopol_playbook import SPLITS, load_h1
from period_returns import publish_results, slug, to_daily, to_monthly

HERE = __file__
OUT = __import__("pathlib").Path(__file__).resolve().parent / "results"
COST_BPS = 3.0
RSI_EXIT_LONG = 75.0
RSI_EXIT_SHORT = 25.0
MIN_BARS_OPPOSITE = 2
MAX_POSITIONS = 4
HORIZONS = (4, 8, 24)


@dataclass
class Leg:
    side: int
    entry_i: int
    entry_px: float
    trigger_open: float
    stop_px: float
    reason: str


@dataclass
class Book:
    legs: list[Leg] = field(default_factory=list)

    def net_side(self) -> int:
        if not self.legs:
            return 0
        return 1 if sum(l.side for l in self.legs) > 0 else -1


def pip_size(sym: str) -> float:
    return 0.01 if "JPY" in sym else 0.0001


def simulate(sym: str, h1: pd.DataFrame, ema_mid: int = 50) -> pd.DataFrame:
    stk = build_stack(h1, ema_mid=ema_mid)
    o, h, l, c = stk["open"], stk["high"], stk["low"], stk["close"]
    e21, e50, s100, r, atr = stk["ema21"], stk["ema50"], stk["smma100"], stk["rsi"], stk["atr"]
    pip = pip_size(sym)
    book = Book()
    rows: list[dict] = []

    def close_all(exit_i: int, px: float, reason: str) -> None:
        nonlocal book
        for leg in book.legs:
            gross = (px - leg.entry_px) / leg.entry_px if leg.side > 0 else (leg.entry_px - px) / leg.entry_px
            gross -= COST_BPS / 10000.0
            rows.append(
                {
                    "entry_time": h1.index[leg.entry_i],
                    "exit_time": h1.index[exit_i],
                    "side": "LONG" if leg.side > 0 else "SHORT",
                    "entry_reason": leg.reason,
                    "exit_reason": reason,
                    "entry_price": round(leg.entry_px, 5),
                    "exit_price": round(px, 5),
                    "pips": round((px - leg.entry_px) / pip if leg.side > 0 else (leg.entry_px - px) / pip, 1),
                    "return_pct": round(gross * 100, 4),
                }
            )
        book.legs.clear()

    for i in range(120, len(h1) - 1):
        exit_px = float(c.iloc[i])

        # --- manage open legs (SL on trigger low/high) ---
        still: list[Leg] = []
        stopped = False
        for leg in book.legs:
            if leg.side > 0 and float(l.iloc[i]) <= leg.stop_px:
                close_all(i, leg.stop_px, "exit_trigger_low_sl")
                stopped = True
                break
            if leg.side < 0 and float(h.iloc[i]) >= leg.stop_px:
                close_all(i, leg.stop_px, "exit_trigger_high_sl")
                stopped = True
                break
            still.append(leg)
        if stopped:
            continue
        book.legs = still

        if book.legs:
            side = book.net_side()
            trig_open = book.legs[0].trigger_open
            if correction_exit(i, o, h, l, c, e21, e50, s100, atr, side, trig_open):
                close_all(i, exit_px, "exit_correction_phase")
                continue
            if side > 0:
                if exit_red_below_trigger_open(o, c, i, trig_open, 1):
                    close_all(i, exit_px, "exit_red_below_trigger_open")
                    continue
                if pd.notna(r.iloc[i]) and r.iloc[i] >= RSI_EXIT_LONG:
                    close_all(i, exit_px, "exit_rsi75")
                    continue
                if pd.notna(atr.iloc[i]) and any(
                    extension_exit_trigger(
                        1, i - leg.entry_i, leg.entry_px, exit_px, float(e21.iloc[i]), float(atr.iloc[i])
                    )
                    for leg in book.legs
                ):
                    close_all(i, exit_px, "exit_ema_extension")
                    continue
                if structure_bulk_exit_long(o, h, l, c, e21, atr, i):
                    close_all(i, exit_px, "exit_structure_engulf")
                    continue
            else:
                if exit_red_below_trigger_open(o, c, i, trig_open, -1):
                    close_all(i, exit_px, "exit_green_above_trigger_open")
                    continue
                if pd.notna(r.iloc[i]) and r.iloc[i] <= RSI_EXIT_SHORT:
                    close_all(i, exit_px, "exit_rsi25")
                    continue
                if pd.notna(atr.iloc[i]) and any(
                    extension_exit_trigger(
                        -1, i - leg.entry_i, leg.entry_px, exit_px, float(e21.iloc[i]), float(atr.iloc[i])
                    )
                    for leg in book.legs
                ):
                    close_all(i, exit_px, "exit_ema_extension")
                    continue
                if structure_bulk_exit_short(o, h, l, c, e21, atr, i):
                    close_all(i, exit_px, "exit_structure_engulf")
                    continue

        if len(book.legs) >= MAX_POSITIONS:
            continue

        if not book.legs:
            if allow_trend_entry(i, o, h, l, c, e21, e50, s100, atr, r, 1):
                _, ph = trend_phase_at(i, o, h, l, c, e21, e50, s100, atr)
                book.legs.append(
                    Leg(
                        1,
                        i + 1,
                        float(c.iloc[i + 1]),
                        float(o.iloc[i]),
                        float(l.iloc[i]),
                        f"entry_reclaim_{ph.lower()}",
                    )
                )
                continue
            if allow_trend_entry(i, o, h, l, c, e21, e50, s100, atr, r, -1):
                _, ph = trend_phase_at(i, o, h, l, c, e21, e50, s100, atr)
                book.legs.append(
                    Leg(
                        -1,
                        i + 1,
                        float(c.iloc[i + 1]),
                        float(o.iloc[i]),
                        float(h.iloc[i]),
                        f"entry_reclaim_{ph.lower()}",
                    )
                )
        else:
            side = book.net_side()
            if allow_pullback_add(i, o, h, l, c, e21, e50, s100, atr, r, side):
                book.legs.append(
                    Leg(
                        side,
                        i + 1,
                        float(c.iloc[i + 1]),
                        float(o.iloc[i]),
                        float(l.iloc[i]) if side > 0 else float(h.iloc[i]),
                        "entry_pullback_ema21",
                    )
                )

    if book.legs:
        close_all(len(h1) - 2, float(c.iloc[-2]), "exit_end_of_data")

    return pd.DataFrame(rows)


def cross_follow_through(h1: pd.DataFrame, ema_mid: int = 50) -> pd.DataFrame:
    stk = build_stack(h1, ema_mid=ema_mid)
    c, e21 = stk["close"], stk["ema21"]
    rows = []
    for i in range(50, len(h1) - max(HORIZONS) - 1):
        side = 0
        if ema21_cross_close_long(c, e21, i):
            side = 1
        elif ema21_cross_close_short(c, e21, i):
            side = -1
        else:
            continue
        md = macro_trend_dir(c, e21, stk["ema50"], stk["smma100"], i)
        if md != side:
            continue
        row = {"time": h1.index[i], "side": "LONG" if side > 0 else "SHORT", "macro_trend": md}
        for hz in HORIZONS:
            fr = forward_bars_after_cross(c, e21, i, hz, side)
            row[f"ret_{hz}b_pct"] = round(fr * 100, 4) if fr is not None else None
        rows.append(row)
    return pd.DataFrame(rows)


def metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0, "exp_bps": 0.0, "pf": 0.0, "wr": 0.0}
    r = trades["return_pct"] / 100.0
    wins = r[r > 0]
    losses = r[r <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0.0
    return {
        "n": len(trades),
        "exp_bps": round(r.mean() * 10000, 2),
        "pf": round(float(pf), 2),
        "wr": round(100 * (r > 0).mean(), 1),
        "sum_pct": round(r.sum() * 100, 2),
    }


def main() -> int:
    syms = ("GBPUSD", "EURUSD", "USDJPY")
    summaries = []
    manifest_rows = []

    for sym in syms:
        h1 = load_h1(sym)
        ema_mid = 100 if "JPY" in sym else 50
        for label, (t0, t1) in SPLITS.items():
            w = h1.loc[(h1.index >= t0) & (h1.index < t1)]
            trades = simulate(sym, w, ema_mid=ema_mid)
            m = metrics(trades)
            m.update({"symbol": sym, "split": label})
            summaries.append(m)
            if not trades.empty:
                strat = f"cross_ride_{sym}_{ema_mid}_{label}"
                rets = trades.set_index("exit_time")["return_pct"] / 100.0
                day = to_daily(rets)
                mon = to_monthly(rets)
                day.to_csv(OUT / f"{slug(strat)}_daily.csv", index=False)
                mon.to_csv(OUT / f"{slug(strat)}_monthly.csv", index=False)
                trades.to_csv(OUT / f"{slug(strat)}_trades.csv", index=False)
                manifest_rows.append(strat)

        ft = cross_follow_through(h1, ema_mid=ema_mid)
        if not ft.empty:
            ft.to_csv(OUT / f"cross_follow_through_{sym}.csv", index=False)
            for side in ("LONG", "SHORT"):
                sub = ft[ft["side"] == side]
                if len(sub):
                    print(f"\n=== First EMA21 cross follow-through {sym} {side} (n={len(sub)}) ===")
                    for col in [c for c in ft.columns if c.startswith("ret_")]:
                        v = sub[col].dropna()
                        if len(v):
                            print(f"  {col}: mean={v.mean():.3f}% win%={(v > 0).mean() * 100:.1f}")

        trades_full = simulate(sym, h1, ema_mid=ema_mid)
        if not trades_full.empty:
            rets = trades_full.set_index("exit_time")["return_pct"] / 100.0
            publish_results(f"cross_ride_{sym}", rets)

    sum_df = pd.DataFrame(summaries)
    sum_df.to_csv(OUT / "fxmopol_cross_ride_summary.csv", index=False)
  # manifest
    man = OUT / "fxmopol_cross_ride_manifest.json"
    man.write_text(json.dumps({"strategies": manifest_rows, "splits": summaries}, indent=2), encoding="utf-8")
    print("\n=== Cross-ride summary by split ===")
    print(sum_df.to_string(index=False))
    print(f"\nWrote {OUT / 'fxmopol_cross_ride_summary.csv'}, {man}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
