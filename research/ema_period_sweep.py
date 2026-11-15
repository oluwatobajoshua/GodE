#!/usr/bin/env python3
"""EMA period sweep for the candle-harvest strategy.

Sweeps EMA period from 1..N across symbols to find which EMA period
is most profitable for the harvest rule:

  Entry:  N consecutive closes on cross side (configurable min_consec)
  Exit:   EMA break / RSI extreme / max hold / ATR trail

Run:
    .\.venv\Scripts\python.exe research\ema_period_sweep.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_period_sweep"
OUT.mkdir(parents=True, exist_ok=True)

SYMS_TFS = [("EURUSD", "H1"), ("GBPUSD", "H1"), ("USDJPY", "H1"), ("BTCUSD", "H1")]

EMA_PERIODS = list(range(1, 101))  # sweep EMA 1..100

# Best config from ema21_harvest research (BTCUSD H1 winner)
DEFAULT_PARAMS = {
    "min_consec_long": 1,
    "min_consec_short": 3,
    "exit_on_ema": False,
    "rsi_exit_long": 80,
    "rsi_exit_short": 20,
    "max_hold": 48,
    "trail_atr": 0.0,
}


def load_csv(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    if not p.exists():
        p = DATA / "mt5" / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.set_index("time").sort_index()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def simulate_harvest(df: pd.DataFrame, params: dict, ema_period: int) -> list:
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    e = ema(c, ema_period)
    r = rsi(c)
    n = len(df)

    min_consec_long = params["min_consec_long"]
    min_consec_short = params["min_consec_short"]
    exit_on_ema = params["exit_on_ema"]
    rsi_exit_long = params["rsi_exit_long"]
    rsi_exit_short = params["rsi_exit_short"]
    max_hold = params["max_hold"]
    trail_atr = params["trail_atr"]

    atr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = atr.rolling(14).mean()

    side = 0
    entry_i = -1
    entry_px = 0.0
    high_px = 0.0
    low_px = 0.0
    consec_long = 0
    consec_short = 0

    trades = []

    for i in range(ema_period + 10, n - 1):
        if side == 0:
            if c.iloc[i] > e.iloc[i]:
                consec_long += 1
                consec_short = 0
            elif c.iloc[i] < e.iloc[i]:
                consec_short += 1
                consec_long = 0
            else:
                consec_long = 0
                consec_short = 0

            if consec_long >= min_consec_long and i >= 1 and c.iloc[i - 1] <= e.iloc[i - 1]:
                side = 1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                high_px = entry_px
                low_px = entry_px
                consec_long = 0
                consec_short = 0
                continue

            if consec_short >= min_consec_short and i >= 1 and c.iloc[i - 1] >= e.iloc[i - 1]:
                side = -1
                entry_i = i
                entry_px = float(c.iloc[i + 1])
                high_px = entry_px
                low_px = entry_px
                consec_long = 0
                consec_short = 0
                continue

        else:
            if side > 0:
                high_px = max(high_px, float(h.iloc[i]))
                low_px = min(low_px, float(l.iloc[i]))
            else:
                high_px = max(high_px, float(h.iloc[i]))
                low_px = min(low_px, float(l.iloc[i]))

            exit_now = False
            exit_reason = ""

            c_i = float(c.iloc[i])
            bars_held = i - entry_i

            if exit_on_ema:
                if side > 0 and c_i < float(e.iloc[i]):
                    exit_now = True
                    exit_reason = "ema_break"
                if side < 0 and c_i > float(e.iloc[i]):
                    exit_now = True
                    exit_reason = "ema_break"

            rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
            if side > 0 and rv >= rsi_exit_long:
                exit_now = True
                exit_reason = "rsi_extreme"
            if side < 0 and rv <= rsi_exit_short:
                exit_now = True
                exit_reason = "rsi_extreme"

            if trail_atr > 0 and bars_held > 0:
                a = float(atr.iloc[entry_i]) if pd.notna(atr.iloc[entry_i]) else 0.001
                if side > 0:
                    trail_px = high_px - trail_atr * a
                    if c_i <= trail_px:
                        exit_now = True
                        exit_reason = "trail_stop"
                else:
                    trail_px = low_px + trail_atr * a
                    if c_i >= trail_px:
                        exit_now = True
                        exit_reason = "trail_stop"

            if bars_held >= max_hold:
                exit_now = True
                exit_reason = "time_stop"

            if exit_now:
                exit_px = c_i
                if side > 0:
                    gross = (exit_px - entry_px) / entry_px
                else:
                    gross = (entry_px - exit_px) / entry_px
                trades.append({
                    "entry_i": entry_i,
                    "exit_i": i,
                    "side": side,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "bars_held": bars_held,
                    "exit_reason": exit_reason,
                    "gross_pct": gross * 100,
                })
                side = 0

    if side != 0 and entry_i >= 0:
        exit_px = float(c.iloc[n - 1])
        if side > 0:
            gross = (exit_px - entry_px) / entry_px
        else:
            gross = (entry_px - exit_px) / entry_px
        trades.append({
            "entry_i": entry_i,
            "exit_i": n - 1,
            "side": side,
            "entry_px": entry_px,
            "exit_px": exit_px,
            "bars_held": n - 1 - entry_i,
            "exit_reason": "end",
            "gross_pct": gross * 100,
        })

    return trades


def main() -> int:
    all_rows = []

    for sym, tf in SYMS_TFS:
        try:
            df = load_csv(sym, tf)
        except FileNotFoundError:
            print(f"SKIP {sym}_{tf}: file not found")
            continue
        print(f"\n=== {sym} {tf} ({len(df)} bars) ===")

        for period in EMA_PERIODS:
            trades = simulate_harvest(df, DEFAULT_PARAMS, period)
            if not trades:
                continue
            rets = np.asarray([t["gross_pct"] for t in trades]) / 100.0
            total = float(np.prod(1 + rets) - 1) * 100
            wins = rets[rets > 0]
            losses = rets[rets < 0]
            pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
            winrate = 100 * (rets > 0).mean()
            n_trades = len(trades)
            avg_bars = float(np.mean([t["bars_held"] for t in trades]))

            all_rows.append({
                "symbol": sym, "tf": tf, "ema_period": period,
                "n_trades": n_trades,
                "winrate": round(winrate, 1),
                "total_pct": round(total, 2),
                "pf": round(pf, 2),
                "avg_bars_held": round(avg_bars, 1),
            })

    df_out = pd.DataFrame(all_rows)
    out_csv = OUT / "ema_period_sweep.csv"
    df_out.to_csv(out_csv, index=False)

    # Show top configs by total return with pf >= 1
    good = df_out[(df_out["pf"] >= 1.0) & (df_out["n_trades"] >= 10)]
    if len(good):
        good = good.sort_values("total_pct", ascending=False)
        good.to_csv(OUT / "ema_period_profitable.csv", index=False)
        print(f"\n=== {len(good)} profitable configs (pf>=1, n>=10) ===")
        print(good.head(20).to_string(index=False))
    else:
        print("\n=== NO profitable configs found ===")

    print(f"\nSaved: {out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
