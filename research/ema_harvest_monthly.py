#!/usr/bin/env python3
"""EMA harvest monthly results — BTCUSD H1, EMA=2 (best from ema_period_sweep).

Best config from research/results/ema_period_sweep/ema_period_sweep.csv:
  ema_period=2, min_consec_long=1, min_consec_short=3, exit_on_ema=false,
  rsi_exit_long=80, rsi_exit_short=20, max_hold=48, trail_atr=0.0
Result: +34,728% total, PF 1.27, 55.2% win rate, 3,086 trades.

Run:
    .\.venv\Scripts\python.exe research\ema_harvest_monthly.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "results" / "ema_harvest_monthly"
OUT.mkdir(parents=True, exist_ok=True)

from period_returns import publish_results  # noqa: E402


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


def simulate_harvest(df: pd.DataFrame, params: dict, ema_period: int) -> pd.Series:
    """Return trade returns (fraction) indexed by exit bar time."""
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

    trade_rets = []
    trade_times = []

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
                trade_rets.append(gross)
                trade_times.append(df.index[i])
                side = 0

    if side != 0 and entry_i >= 0:
        exit_px = float(c.iloc[n - 1])
        if side > 0:
            gross = (exit_px - entry_px) / entry_px
        else:
            gross = (entry_px - exit_px) / entry_px
        trade_rets.append(gross)
        trade_times.append(df.index[n - 1])

    return pd.Series(trade_rets, index=pd.DatetimeIndex(trade_times))


def main() -> int:
    sym, tf = "BTCUSD", "H1"
    df = load_csv(sym, tf)
    print(f"=== {sym} {tf} ({len(df)} bars, {df.index[0]} to {df.index[-1]}) ===")

    params = {
        "min_consec_long": 1,
        "min_consec_short": 3,
        "exit_on_ema": False,
        "rsi_exit_long": 80,
        "rsi_exit_short": 20,
        "max_hold": 48,
        "trail_atr": 0.0,
    }

    rets = simulate_harvest(df, params, ema_period=2)
    rets = rets.sort_index()

    total = float(np.prod(1 + rets) - 1) * 100
    wins = rets[rets > 0]
    losses = rets[rets < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0
    winrate = 100 * (rets > 0).mean()
    n_trades = len(rets)
    print(f"n_trades={n_trades} winrate={winrate:.1f}% total={total:.2f}% pf={pf:.2f}")

    # Monthly calendar output (per AGENTS.md reporting rule)
    publish_results(f"ema_harvest_{sym}_{tf}_ema2", rets, last_months=24)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
