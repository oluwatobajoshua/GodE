#!/usr/bin/env python3
"""Session Liquidity Sweep & Stop-Hunt Mean Reversion - EURUSD M15.

Asian range (00:00-07:00 UTC). Watch London (08:00+) / NY (13:00+) for a sweep
beyond Asian high/low, then enter mean-reversion on rejection close back inside.
Target: opposite side of Asian range. Stop: beyond sweep extreme.
Max 2 trades/day.

Backtest contract: per-bar returns, trade-level PF, validate_returns.
"""

from __future__ import annotations

import sys
import re

import numpy as np
import pandas as pd

sys.path.insert(0, "C:\\xampp\\htdocs\\GodE\\research")
from backtest_core import cost_per_side, point_size, trade_metrics, validate_returns
from period_returns import publish_results, print_split_monthly


def slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:96]


SESSION_ASIAN = (0, 7)   # 00:00-06:59 UTC
SESSION_LONDON = (8, 12) # 08:00-11:59 UTC
SESSION_NY = (13, 21)    # 13:00-20:59 UTC

MAX_TRADES_PER_DAY = 2


def backtest(df: pd.DataFrame, symbol: str,
             comm_pts: float = 7.0, slip_pts: float = 1.0,
             floor_pts: float = 1.0,
             sl_buffer_pts: float = 1.0):
    df = df.copy().reset_index(drop=True)
    n = len(df)
    point = point_size(symbol)

    time = pd.to_datetime(df["time"])
    open_px = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)

    hours = time.dt.hour.to_numpy()
    dates = time.dt.date.to_numpy()

    cost_side = cost_per_side(df, symbol, comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_times: list = []

    position = None  # dict: side, entry, sl, tp, entry_bar

    current_date = None
    asian_high = None
    asian_low = None
    trades_today = 0
    sweep_active = False
    sweep_side = 0      # 1 = swept above AH (waiting to short), -1 = swept below AL (waiting to long)
    sweep_extreme = 0.0

    for i in range(n):
        h = int(hours[i])
        d = dates[i]
        c = close[i]
        o = open_px[i]
        hi = high[i]
        lo = low[i]

        # --- new day reset -------------------------------------------
        if d != current_date:
            current_date = d
            asian_high = None
            asian_low = None
            trades_today = 0
            sweep_active = False
            sweep_side = 0
            sweep_extreme = 0.0

        # --- Asian session: build the range (causal, running) -------
        if SESSION_ASIAN[0] <= h < SESSION_ASIAN[1]:
            if asian_high is None or hi > asian_high:
                asian_high = float(hi)
            if asian_low is None or lo < asian_low:
                asian_low = float(lo)
            continue  # no trading during Asian session

        # --- outside Asian + London/NY: skip -------------------------
        in_london = SESSION_LONDON[0] <= h < SESSION_LONDON[1]
        in_ny = SESSION_NY[0] <= h < SESSION_NY[1]
        if not (in_london or in_ny):
            continue
        if asian_high is None or asian_low is None:
            continue

        # --- manage open position ------------------------------------
        if position is not None:
            side = position["side"]
            entry_px = position["entry"]
            sl = position["sl"]
            tp = position["tp"]
            prev_c = close[i - 1] if i > 0 else o

            exit_px = None
            if side == 1:
                if lo <= sl:
                    exit_px = sl
                elif hi >= tp:
                    exit_px = tp
            else:
                if hi >= sl:
                    exit_px = sl
                elif lo <= tp:
                    exit_px = tp

            if i == position["entry_bar"]:
                bar_ret = side * (c / o - 1.0) - cost_side[i] / o
            else:
                bar_ret = side * (c / prev_c - 1.0)
            rets[i] = bar_ret

            if exit_px is not None:
                if i == position["entry_bar"]:
                    exit_ret = side * (exit_px / o - 1.0) - cost_side[i] / o
                else:
                    exit_ret = side * (exit_px / prev_c - 1.0)
                exit_ret -= cost_side[i] / exit_px
                rets[i] = exit_ret

                gross = side * (exit_px / entry_px - 1.0)
                cost_rt = (cost_side[position["entry_bar"]] / entry_px +
                           cost_side[i] / exit_px)
                trades.append(gross - cost_rt)
                trade_times.append(time[i])
                position = None

        # --- evaluate entry (only if no open position) ---------------
        if position is None and trades_today < MAX_TRADES_PER_DAY:
            if not sweep_active:
                # Hunt for a sweep beyond Asian range
                if hi > asian_high:
                    sweep_active = True
                    sweep_side = 1
                    sweep_extreme = float(hi)
                elif lo < asian_low:
                    sweep_active = True
                    sweep_side = -1
                    sweep_extreme = float(lo)
            else:
                # Active sweep: waiting for rejection close back inside range
                if sweep_side == 1 and c < asian_high:
                    # Rejected above -> SHORT at next open, target AL
                    if i + 1 < n:
                        entry_px = float(open_px[i + 1])
                        sl = sweep_extreme + sl_buffer_pts * point
                        tp = asian_low
                        risk = sl - entry_px
                        if risk > 0 and tp < entry_px:
                            position = {"side": -1, "entry": entry_px, "sl": sl,
                                        "tp": tp, "entry_bar": i + 1}
                            trades_today += 1
                    sweep_active = False
                    sweep_side = 0
                elif sweep_side == -1 and c > asian_low:
                    # Rejected below -> LONG at next open, target AH
                    if i + 1 < n:
                        entry_px = float(open_px[i + 1])
                        sl = sweep_extreme - sl_buffer_pts * point
                        tp = asian_high
                        risk = entry_px - sl
                        if risk > 0 and tp > entry_px:
                            position = {"side": 1, "entry": entry_px, "sl": sl,
                                        "tp": tp, "entry_bar": i + 1}
                            trades_today += 1
                    sweep_active = False
                    sweep_side = 0
                else:
                    # Price still outside or extending: update sweep extreme
                    if sweep_side == 1 and hi > sweep_extreme:
                        sweep_extreme = float(hi)
                    elif sweep_side == -1 and lo < sweep_extreme:
                        sweep_extreme = float(lo)

    # --- close any open position at last close ------------------------
    if position is not None:
        i = n - 1
        side = position["side"]
        entry_px = position["entry"]
        exit_px = close[i]
        o = open_px[i]
        prev_c = close[i - 1]
        if i == position["entry_bar"]:
            exit_ret = side * (exit_px / o - 1.0) - cost_side[i] / o
        else:
            exit_ret = side * (exit_px / prev_c - 1.0)
        exit_ret -= cost_side[i] / exit_px
        rets[i] = exit_ret
        gross = side * (exit_px / entry_px - 1.0)
        cost_rt = (cost_side[position["entry_bar"]] / entry_px +
                   cost_side[i] / exit_px)
        trades.append(gross - cost_rt)
        trade_times.append(time[i])

    rets_series = pd.Series(rets, index=time)
    trades_arr = np.asarray(trades, dtype=float)

    validate_returns(rets_series, close, label="session_sweep")

    return rets_series, trades_arr, trade_times, df


def run(csv_path: str, symbol: str = "EURUSD"):
    df = pd.read_csv(csv_path, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)
    print(f"Loaded {len(df)} bars  {df['time'].min()} -> {df['time'].max()}")

    rets, trades, trade_times, df = backtest(df, symbol)

    equity = (1 + rets).cumprod()
    total = float(equity.iloc[-1] - 1.0)

    years = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 365.25
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else float("nan")

    daily = rets.resample("1D").sum().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else float("nan")

    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    maxdd = float(dd.min())

    m = trade_metrics(trades)
    avg_win = trades[trades > 0].mean() if (trades > 0).any() else 0.0
    avg_loss = trades[trades < 0].mean() if (trades < 0).any() else 0.0

    print(f"\n=== Session Liquidity Sweep | {symbol} M15 | {len(df)} bars ===")
    print(f"Start: $10000.00  End: ${10000*(1+total):,.2f}  PnL: ${10000*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  Wins: {int((trades>0).sum())}  Losses: {int((trades<0).sum())}  "
          f"WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    print(f"Avg win: ${10000*avg_win:+.2f}  Avg loss: ${10000*avg_loss:+.2f}")

    print(f"\n=== Last 12 months ===")
    publish_results(f"session_sweep_{symbol}", rets, last_months=12, last_days=21)

    print(f"\n=== Three-way split ===")
    splits = {
        "design":   ("2022-11-01", "2024-01-01"),
        "validation": ("2024-01-01", "2025-01-01"),
        "oos":      ("2025-01-01", "2026-12-31"),
    }
    print_split_monthly(f"session_sweep_{symbol}", rets, splits)

    verdict = "PASS" if (cagr > 0 and m["pf"] and m["pf"] > 1.0 and maxdd > -0.20) else "FAIL"
    print(f"\n=== RESULT: {symbol} M15 Session Liquidity Sweep ===")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"WR: {m['winrate']:.1f}%  PF: {m['pf']}  Trades: {m['trades']}")
    print(f"=== {verdict} ===")
    return verdict, cagr, sharpe, maxdd, m


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else r"C:\xampp\htdocs\GodE\research\data\mt5\EURUSD_M15.csv"
    sym = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    run(csv, sym)
