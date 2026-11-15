#!/usr/bin/env python3
"""Volatility Expansion Breakout (Donchian + ATR compression) - EURUSD M15.

Backtest contract: per-bar returns where (1 + ret).cumprod() - 1 == account return;
trade_metrics() is trade-level PF; validate_returns() rejects cumulative series.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "C:\\xampp\\htdocs\\GodE\\research")
from backtest_core import wilder_atr, cost_per_side, point_size, trade_metrics, validate_returns
from period_returns import publish_results, print_split_monthly


def slug(name: str) -> str:
    import re
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:96]


def backtest(df: pd.DataFrame, symbol: str,
             atr_period: int = 14,
             atr_ma_period: int = 50,
             compression_mult: float = 0.8,
             donchian_period: int = 20,
             sl_atr_cap: float = 1.2,
             rr: float = 2.0,
             be_at_r: float = 1.0,
             trail_atr_mult: float = 1.0,
             comm_pts: float = 7.0,
             slip_pts: float = 1.0,
             floor_pts: float = 1.0,
             risk_pct: float = 0.5):
    df = df.copy().reset_index(drop=True)
    n = len(df)
    point = point_size(symbol)

    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    opn = df["open"].to_numpy(float)
    time = df["time"].to_numpy()

    atr = wilder_atr(df, atr_period).to_numpy(float)
    sma_atr = pd.Series(atr).rolling(atr_ma_period).mean().to_numpy(float)

    # Donchian high/low over prior donchian_period bars (causal, shift(1))
    dch_high = pd.Series(high).rolling(donchian_period).max().shift(1).to_numpy(float)
    dch_low = pd.Series(low).rolling(donchian_period).min().shift(1).to_numpy(float)

    cost_side = cost_per_side(df, symbol, comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_times: list = []
    position = None  # dict

    def entry_cost_pct(i):
        return cost_side[i] / opn[i]

    def exit_cost_pct(i, px):
        return cost_side[i] / px

    for i in range(n):
        c = close[i]
        prev_c = close[i - 1] if i > 0 else opn[i]

        # --- manage open position -------------------------------------
        if position is not None:
            side = position["side"]
            entry_px = position["entry"]
            sl = position["sl"]
            tp = position["tp"]
            risk = position["risk"]

            # causal trailing / breakeven update using close[i-1] (known before bar i)
            pc = close[i - 1] if i > 0 else entry_px
            if not position["be_done"]:
                if side == 1 and pc >= entry_px + be_at_r * risk:
                    position["sl"] = entry_px
                    position["be_done"] = True
                    sl = entry_px
                elif side == -1 and pc <= entry_px - be_at_r * risk:
                    position["sl"] = entry_px
                    position["be_done"] = True
                    sl = entry_px
            else:
                a = atr[i - 1] if i > 0 else atr[i]
                if side == 1:
                    new_sl = pc - trail_atr_mult * a
                    if new_sl > position["sl"]:
                        position["sl"] = new_sl
                        sl = new_sl
                else:
                    new_sl = pc + trail_atr_mult * a
                    if new_sl < position["sl"]:
                        position["sl"] = new_sl
                        sl = new_sl

            # check SL/TP hit during bar i (high/low)
            exit_px = None
            if side == 1:
                if low[i] <= sl:
                    exit_px = sl
                elif high[i] >= tp:
                    exit_px = tp
            else:
                if high[i] >= sl:
                    exit_px = sl
                elif low[i] <= tp:
                    exit_px = tp

            # mark to market at close[i]
            if i == position["entry_bar"]:
                bar_ret = side * (c / opn[i] - 1.0) - entry_cost_pct(i)
            else:
                bar_ret = side * (c / prev_c - 1.0)
            rets[i] = bar_ret

            if exit_px is not None:
                # exit bar: replace move-to-close with move-to-exit_px, charge exit cost
                if i == position["entry_bar"]:
                    exit_ret = side * (exit_px / opn[i] - 1.0) - entry_cost_pct(i)
                else:
                    exit_ret = side * (exit_px / prev_c - 1.0)
                exit_ret -= exit_cost_pct(i, exit_px)
                rets[i] = exit_ret

                gross = side * (exit_px / entry_px - 1.0)
                cost_rt = (cost_side[position["entry_bar"]] / entry_px +
                           cost_side[i] / exit_px)
                trades.append(gross - cost_rt)
                trade_times.append(time[i])
                position = None

        # --- evaluate entry signal at close of bar i (enter at open[i+1]) ---
        if position is None and i + 1 < n:
            if not np.isnan(atr[i]) and not np.isnan(sma_atr[i]):
                compressed = atr[i] < compression_mult * sma_atr[i]
                if compressed:
                    dh = dch_high[i]
                    dl = dch_low[i]
                    if not np.isnan(dh) and not np.isnan(dl):
                        if close[i] > dh:  # long breakout
                            entry_px = opn[i + 1]
                            a = atr[i]
                            mid = (dh + dl) / 2.0
                            sl_cap = entry_px - sl_atr_cap * a
                            sl = max(mid, sl_cap)  # tighter (closer to entry) for long
                            risk = entry_px - sl
                            tp = entry_px + rr * risk
                            if risk > 0 and tp > entry_px:
                                position = {
                                    "side": 1, "entry": entry_px, "sl": sl,
                                    "tp": tp, "risk": risk, "be_done": False,
                                    "entry_bar": i + 1,
                                }
                        elif close[i] < dl:  # short breakout
                            entry_px = opn[i + 1]
                            a = atr[i]
                            mid = (dh + dl) / 2.0
                            sl_cap = entry_px + sl_atr_cap * a
                            sl = min(mid, sl_cap)  # tighter (closer to entry) for short
                            risk = sl - entry_px
                            tp = entry_px - rr * risk
                            if risk > 0 and tp < entry_px:
                                position = {
                                    "side": -1, "entry": entry_px, "sl": sl,
                                    "tp": tp, "risk": risk, "be_done": False,
                                    "entry_bar": i + 1,
                                }

    # close any open position at last close
    if position is not None:
        i = n - 1
        side = position["side"]
        entry_px = position["entry"]
        exit_px = close[i]
        prev_c = close[i - 1]
        if i == position["entry_bar"]:
            exit_ret = side * (exit_px / opn[i] - 1.0) - entry_cost_pct(i)
        else:
            exit_ret = side * (exit_px / prev_c - 1.0)
        exit_ret -= exit_cost_pct(i, exit_px)
        rets[i] = exit_ret
        gross = side * (exit_px / entry_px - 1.0)
        cost_rt = (cost_side[position["entry_bar"]] / entry_px +
                   cost_side[i] / exit_px)
        trades.append(gross - cost_rt)
        trade_times.append(time[i])

    rets_series = pd.Series(rets, index=pd.to_datetime(time))
    trades_arr = np.asarray(trades, dtype=float)

    validate_returns(rets_series, close, label="vol_exp_breakout")

    return rets_series, trades_arr, trade_times, df


def run(csv_path: str, symbol: str = "EURUSD"):
    df = pd.read_csv(csv_path, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)
    print(f"Loaded {len(df)} bars  {df['time'].min()} -> {df['time'].max()}")

    rets, trades, trade_times, df = backtest(df, symbol)

    # equity curve
    equity = (1 + rets).cumprod()
    total = float(equity.iloc[-1] - 1.0)

    # CAGR
    years = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 365.25
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else float("nan")

    # Sharpe (daily resample)
    daily = rets.resample("1D").sum().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else float("nan")

    # maxDD
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    maxdd = float(dd.min())

    m = trade_metrics(trades)

    print(f"\n=== Volatility Expansion Breakout | {symbol} M15 | {len(df)} bars ===")
    print(f"Start: $10000.00  End: ${10000*(1+total):,.2f}  PnL: ${10000*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  Wins: {int((trades>0).sum())}  Losses: {int((trades<0).sum())}  "
          f"WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    avg_win = trades[trades > 0].mean() if (trades > 0).any() else 0.0
    avg_loss = trades[trades < 0].mean() if (trades < 0).any() else 0.0
    print(f"Avg win: ${10000*avg_win:+.2f}  Avg loss: ${10000*avg_loss:+.2f}")

    print(f"\n=== Last 12 months ===")
    publish_results(f"vol_exp_breakout_{symbol}", rets, last_months=12, last_days=21)

    print(f"\n=== Three-way split ===")
    t = df["time"]
    splits = {
        "design":   ("2022-11-01", "2024-01-01"),
        "validation": ("2024-01-01", "2025-01-01"),
        "oos":      ("2025-01-01", "2026-12-31"),
    }
    print_split_monthly(f"vol_exp_breakout_{symbol}", rets, splits)

    verdict = "PASS" if (cagr > 0 and m["pf"] and m["pf"] > 1.0 and maxdd > -0.20) else "FAIL"
    print(f"\n=== RESULT: {symbol} M15 Volatility Expansion Breakout ===")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"WR: {m['winrate']:.1f}%  PF: {m['pf']}  Trades: {m['trades']}")
    print(f"=== {verdict} ===")
    return verdict, cagr, sharpe, maxdd, m


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else r"C:\xampp\htdocs\GodE\research\data\mt5\EURUSD_M15.csv"
    sym = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    run(csv, sym)
