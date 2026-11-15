#!/usr/bin/env python3
"""Higher-Timeframe Macro Structure (D1) — Impulse + Order-Block Retracement.

Detects impulsive D1 candles (body > 1.5x ATR, close in extreme quartile),
marks the prior candle as the order block, then enters mean-reversion
when price retraces into the OB zone. Target: 3R. Stop: beyond OB extreme.
Max 1 position. Backtest contract: per-bar returns, trade-level PF.

Run:
    .\.venv\Scripts\python.exe research\d1_macro_structure.py
"""

from __future__ import annotations

import sys
import re

import numpy as np
import pandas as pd

sys.path.insert(0, "C:\\xampp\\htdocs\\GodE\\research")
from backtest_core import wilder_atr, cost_per_side, point_size, trade_metrics, validate_returns
from period_returns import publish_results, print_split_monthly


def slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:96]


def backtest(df: pd.DataFrame, symbol: str,
             atr_period: int = 14,
             impulse_body_mult: float = 1.5,
             close_quartile: float = 0.25,
             rr: float = 3.0,
             comm_pts: float = 7.0,
             slip_pts: float = 1.0,
             floor_pts: float = 1.0):
    df = df.copy().reset_index(drop=True)
    n = len(df)
    point = point_size(symbol)

    time = pd.to_datetime(df["time"])
    open_px = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)

    atr = wilder_atr(df, atr_period).to_numpy(float)
    cost_side = cost_per_side(df, symbol, comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_times: list = []

    position = None  # dict: side, entry, sl, tp, entry_bar, ob_high, ob_low

    # Order block state: pending OB waiting for retracement
    ob_pending = False
    ob_side = 0        # 1 = bullish impulse (waiting to long), -1 = bearish (waiting to short)
    ob_high = 0.0
    ob_low = 0.0
    ob_invalidated = False

    for i in range(n):
        c = close[i]
        o = open_px[i]
        hi = high[i]
        lo = low[i]
        a = atr[i] if not np.isnan(atr[i]) else 0.0

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

        # --- if no position, check for new impulse or retracement ----
        if position is None and i > 0 and a > 0:
            prev_c = close[i - 1]
            prev_o = open_px[i - 1]
            prev_hi = high[i - 1]
            prev_lo = low[i - 1]
            prev_body = abs(prev_c - prev_o)
            prev_range = prev_hi - prev_lo

            # Detect impulse on the *previous* candle (candle i-1)
            # Body > 1.5 x ATR, close in top/bottom quartile of range
            is_impulse = False
            impulse_dir = 0
            if prev_range > 0 and prev_body > impulse_body_mult * a:
                close_pos = (prev_c - prev_lo) / prev_range  # 0 = low, 1 = high
                if close_pos >= 1.0 - close_quartile:
                    is_impulse = True
                    impulse_dir = 1   # bullish impulse -> wait for retracement to long
                elif close_pos <= close_quartile:
                    is_impulse = True
                    impulse_dir = -1  # bearish impulse -> wait for retracement to short

            if is_impulse and not ob_pending:
                # Mark previous candle as order block
                ob_pending = True
                ob_side = impulse_dir
                ob_high = prev_hi
                ob_low = prev_lo
                ob_invalidated = False

            elif ob_pending and not ob_invalidated:
                # Wait for retracement into OB zone
                if ob_side == 1:
                    # Bullish impulse: wait for price to come back into [ob_low, ob_high]
                    # Invalidated if price breaks above ob_high without entering
                    if hi > ob_high and lo > ob_high:
                        ob_invalidated = True
                        ob_pending = False
                    elif lo <= ob_high and hi >= ob_low:
                        # Price entered OB zone -> enter long at next open
                        if i + 1 < n:
                            entry_px = float(open_px[i + 1])
                            sl = ob_low
                            risk = entry_px - sl
                            tp = entry_px + rr * risk
                            if risk > 0 and tp > entry_px:
                                position = {"side": 1, "entry": entry_px, "sl": sl,
                                            "tp": tp, "entry_bar": i + 1}
                        ob_pending = False
                        ob_invalidated = True
                else:
                    # Bearish impulse: wait for price to come back into [ob_low, ob_high]
                    if lo < ob_low and hi < ob_low:
                        ob_invalidated = True
                        ob_pending = False
                    elif hi >= ob_low and lo <= ob_high:
                        # Price entered OB zone -> enter short at next open
                        if i + 1 < n:
                            entry_px = float(open_px[i + 1])
                            sl = ob_high
                            risk = sl - entry_px
                            tp = entry_px - rr * risk
                            if risk > 0 and tp < entry_px:
                                position = {"side": -1, "entry": entry_px, "sl": sl,
                                            "tp": tp, "entry_bar": i + 1}
                        ob_pending = False
                        ob_invalidated = True

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

    validate_returns(rets_series, close, label="d1_macro_structure")

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

    print(f"\n=== D1 Macro Structure | {symbol} D1 | {len(df)} bars ===")
    print(f"Start: $10000.00  End: ${10000*(1+total):,.2f}  PnL: ${10000*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  Wins: {int((trades>0).sum())}  Losses: {int((trades<0).sum())}  "
          f"WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    print(f"Avg win: ${10000*avg_win:+.2f}  Avg loss: ${10000*avg_loss:+.2f}")

    print(f"\n=== Last 12 months ===")
    publish_results(f"d1_macro_structure_{symbol}", rets, last_months=12, last_days=21)

    print(f"\n=== Three-way split ===")
    splits = {
        "design":   ("2022-11-01", "2024-01-01"),
        "validation": ("2024-01-01", "2025-01-01"),
        "oos":      ("2025-01-01", "2026-12-31"),
    }
    print_split_monthly(f"d1_macro_structure_{symbol}", rets, splits)

    verdict = "PASS" if (cagr > 0 and m["pf"] and m["pf"] > 1.0 and maxdd > -0.20) else "FAIL"
    print(f"\n=== RESULT: {symbol} D1 Macro Structure ===")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"WR: {m['winrate']:.1f}%  PF: {m['pf']}  Trades: {m['trades']}")
    print(f"=== {verdict} ===")
    return verdict, cagr, sharpe, maxdd, m


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else r"C:\xampp\htdocs\GodE\research\data\mt5\EURUSD_D1.csv"
    sym = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    run(csv, sym)
