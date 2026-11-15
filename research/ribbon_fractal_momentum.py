#!/usr/bin/env python3
"""Ribbon Fractal Momentum backtest.

Entry (all 3 must align):
  1. EMA ribbon stacked: EMA21 > EMA50 > EMA100 + slope up (longs) / reverse (shorts)
  2. RSI(9) extreme reset: dipped below 20 / above 80, then crossed back out
  3. Fractal breakout: close above most recent fractal up (long) / below fractal down (short)

Exit:
  1. Structural stop at opposite fractal
  2. Breakeven at +1R
  3. TP at 3R
"""

from __future__ import annotations

import sys
import re

import numpy as np
import pandas as pd

sys.path.insert(0, "C:\\xampp\\htdocs\\GodE\\research")
from backtest_core import cost_per_side, point_size, trade_metrics
from period_returns import publish_results, print_split_monthly


def slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:96]


def wilder_rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def backtest(df: pd.DataFrame, symbol: str,
             ema_fast: int = 21, ema_mid: int = 50, ema_slow: int = 100,
             rsi_period: int = 9,
             rsi_long_extreme: float = 20.0,
             rsi_short_extreme: float = 80.0,
             rsi_reset_ceiling: float = 50.0,
             rsi_reset_floor: float = 50.0,
             rr_target: float = 3.0,
             be_at_r: float = 1.0,
             comm_pts: float = 7.0,
             slip_pts: float = 1.0,
             floor_pts: float = 1.0,
             capital_per_trade: float = 10000.0):
    df = df.copy().reset_index(drop=True)
    n = len(df)
    point = point_size(symbol)

    time = pd.to_datetime(df["time"])
    open_px = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)

    ema21 = pd.Series(close).ewm(span=ema_fast, adjust=False).mean().to_numpy(float)
    ema50 = pd.Series(close).ewm(span=ema_mid, adjust=False).mean().to_numpy(float)
    ema100 = pd.Series(close).ewm(span=ema_slow, adjust=False).mean().to_numpy(float)
    rsi = wilder_rsi(pd.Series(close), rsi_period).to_numpy(float)

    cost_side = cost_per_side(df, symbol, comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_times: list = []

    position = None  # {side, entry, stop, tp, risk, be_done, entry_bar}

    # Fractal tracking (causal: fractal at bar i-2 confirmed by bar i close)
    last_fractal_up = 0.0
    last_fractal_down = float("inf")

    # RSI reset state (armed when RSI crosses out of extreme after being in it)
    rsi_reset_long = False   # RSI dipped below 20 and crossed back above
    rsi_reset_short = False  # RSI dipped above 80 and crossed back below
    rsi_was_below_20 = False
    rsi_was_above_80 = False

    for i in range(n):
        c = close[i]
        o = open_px[i]
        h = high[i]
        l = low[i]
        e21 = ema21[i]
        e50 = ema50[i]
        e100 = ema100[i]
        r = rsi[i] if not np.isnan(rsi[i]) else 50.0
        prev_c = close[i - 1] if i > 0 else o
        prev_r = rsi[i - 1] if i > 0 else 50.0
        prev_e21 = ema21[i - 1] if i > 0 else e21

        # --- Confirm fractal at bar i-2 (needs bar i) ------------------
        if i >= 4:
            hi_im2 = high[i - 2]
            if (hi_im2 > high[i - 4] and hi_im2 > high[i - 3] and
                    hi_im2 >= high[i - 1] and hi_im2 > high[i]):
                last_fractal_up = hi_im2
            lo_im2 = low[i - 2]
            if (lo_im2 < low[i - 4] and lo_im2 < low[i - 3] and
                    lo_im2 <= low[i - 1] and lo_im2 < low[i]):
                last_fractal_down = lo_im2

        # --- RSI extreme detection -------------------------------------
        if r < rsi_long_extreme:
            rsi_was_below_20 = True
        if r > rsi_short_extreme:
            rsi_was_above_80 = True

        # RSI cross out of extreme (arms the reset)
        rsi_crossed_above_20 = (i > 0 and prev_r < rsi_long_extreme and r >= rsi_long_extreme)
        rsi_crossed_below_80 = (i > 0 and prev_r > rsi_short_extreme and r <= rsi_short_extreme)

        if rsi_crossed_above_20 and rsi_was_below_20:
            rsi_reset_long = True
        if rsi_crossed_below_80 and rsi_was_above_80:
            rsi_reset_short = True

        # RSI leaves reset window -> disarm both flags
        if r > rsi_reset_ceiling:
            rsi_reset_long = False
            rsi_was_below_20 = False
        if r < rsi_reset_floor:
            rsi_reset_short = False
            rsi_was_above_80 = False

        # --- Ribbon alignment ------------------------------------------
        ribbon_bullish = (e21 > e50 > e100 and e21 > prev_e21)
        ribbon_bearish = (e21 < e50 < e100 and e21 < prev_e21)

        # --- Manage open position --------------------------------------
        if position is not None:
            side = position["side"]
            entry_px = position["entry"]
            stop_px = position["stop"]
            tp_px = position["tp"]
            risk = position["risk"]
            be_done = position["be_done"]
            entry_bar = position["entry_bar"]

            exit_px = None
            exit_reason = ""

            if side == 1:  # long
                if l <= stop_px:
                    exit_px = stop_px
                    exit_reason = "SL"
                elif h >= tp_px:
                    exit_px = tp_px
                    exit_reason = "TP"

                if not be_done and c >= entry_px + be_at_r * risk:
                    position["stop"] = entry_px
                    position["be_done"] = True
                    stop_px = entry_px
                    be_done = True

            else:  # short
                if h >= stop_px:
                    exit_px = stop_px
                    exit_reason = "SL"
                elif l <= tp_px:
                    exit_px = tp_px
                    exit_reason = "TP"

                if not be_done and c <= entry_px - be_at_r * risk:
                    position["stop"] = entry_px
                    position["be_done"] = True
                    stop_px = entry_px
                    be_done = True

            # Mark to market at close of bar i
            if i == entry_bar:
                bar_ret = side * (c / o - 1.0) - cost_side[i] / o
            else:
                bar_ret = side * (c / prev_c - 1.0)
            rets[i] = bar_ret

            if exit_px is not None:
                if i == entry_bar:
                    exit_ret = side * (exit_px / o - 1.0) - cost_side[i] / o
                else:
                    exit_ret = side * (exit_px / prev_c - 1.0)
                exit_ret -= cost_side[i] / exit_px
                rets[i] = exit_ret

                gross = side * (exit_px / entry_px - 1.0)
                cost_rt = (cost_side[entry_bar] / entry_px + cost_side[i] / exit_px)
                trades.append(gross - cost_rt)
                trade_times.append(time[i])
                position = None

        # --- Evaluate entry (enter at open[i+1]) -----------------------
        if position is None and i + 1 < n:
            entry_px = open_px[i + 1]

            # Long: ribbon bullish + RSI armed + close above fractal up
            if (ribbon_bullish and rsi_reset_long and last_fractal_up > 0 and
                    last_fractal_down < float("inf") and c > last_fractal_up):
                stop_px = last_fractal_down
                risk_px = entry_px - stop_px
                if risk_px > 0:
                    tp_px = entry_px + rr_target * risk_px
                    position = {
                        "side": 1, "entry": entry_px, "stop": stop_px,
                        "tp": tp_px, "risk": risk_px, "be_done": False,
                        "entry_bar": i + 1,
                    }
                    rsi_reset_long = False  # disarm after entry

            # Short: ribbon bearish + RSI armed + close below fractal down
            elif (ribbon_bearish and rsi_reset_short and last_fractal_down < float("inf") and
                    last_fractal_up > 0 and c < last_fractal_down):
                stop_px = last_fractal_up
                risk_px = stop_px - entry_px
                if risk_px > 0:
                    tp_px = entry_px - rr_target * risk_px
                    position = {
                        "side": -1, "entry": entry_px, "stop": stop_px,
                        "tp": tp_px, "risk": risk_px, "be_done": False,
                        "entry_bar": i + 1,
                    }
                    rsi_reset_short = False  # disarm after entry

    # --- Close any open position at last close --------------------------
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
        cost_rt = (cost_side[position["entry_bar"]] / entry_px + cost_side[i] / exit_px)
        trades.append(gross - cost_rt)
        trade_times.append(time[i])

    rets_series = pd.Series(rets, index=time)
    trades_arr = np.asarray(trades, dtype=float)

    return rets_series, trades_arr, trade_times, df


def run(csv_path: str, symbol: str = "EURUSD",
        design: tuple = ("2022-11-01", "2024-01-01"),
        validation: tuple = ("2024-01-01", "2025-01-01"),
        oos: tuple = ("2025-01-01", "2026-12-31")):
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

    print(f"\n=== Ribbon Fractal Momentum | {symbol} | {len(df)} bars ===")
    print(f"Start: $10000.00  End: ${10000*(1+total):,.2f}  PnL: ${10000*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  Wins: {int((trades>0).sum())}  Losses: {int((trades<0).sum())}  "
          f"WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    print(f"Avg win: ${10000*avg_win:+.2f}  Avg loss: ${10000*avg_loss:+.2f}")

    print(f"\n=== Last 12 months ===")
    publish_results(f"ribbon_fractal_{symbol}", rets, last_months=12, last_days=21)

    print(f"\n=== Three-way split ===")
    splits = {"design": design, "validation": validation, "oos": oos}
    print_split_monthly(f"ribbon_fractal_{symbol}", rets, splits)

    # Gate check: all three splits positive, CAGR > 0, PF > 1, maxDD > -20%
    mdf = pd.read_csv(f"C:\\xampp\\htdocs\\GodE\\research\\results\\ribbon_fractal_{symbol}_monthly.csv")
    mdf["date"] = pd.to_datetime(mdf["date"])
    split_returns = {}
    for name, (a, b) in splits.items():
        part = mdf[(mdf["date"] >= a) & (mdf["date"] < b)]
        split_returns[name] = part["return_pct"].sum() if len(part) else 0.0

    all_splits_pos = all(v > 0 for v in split_returns.values())
    verdict = ("PASS" if (cagr > 0 and m["pf"] and m["pf"] > 1.0
                          and maxdd > -0.20 and all_splits_pos)
               else "FAIL")
    print(f"\n=== RESULT: {symbol} Ribbon Fractal Momentum ===")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"WR: {m['winrate']:.1f}%  PF: {m['pf']}  Trades: {m['trades']}")
    print(f"Split returns: { {k: round(v,2) for k,v in split_returns.items()} }")
    print(f"=== {verdict} ===")
    return verdict, cagr, sharpe, maxdd, m, split_returns


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else r"C:\xampp\htdocs\GodE\research\data\mt5\EURUSD_M15.csv"
    sym = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    run(csv, sym)
