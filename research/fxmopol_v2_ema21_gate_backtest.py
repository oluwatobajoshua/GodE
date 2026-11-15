#!/usr/bin/env python3
"""fxmopol_v2 EMA21 profit-gated exit backtest — EURUSD M15.

Replicates EA entry rules (Short 1-5 / Long 1-5 mirrors) + new exit logic:
  * Unconditional: close above EMA21 (shorts) / below EMA21 (longs) -> close all
  * Profit-gated below EMA21: allow Exit 2 (weak structure) only if in profit
  * Hold: below EMA21 but not in profit -> do not exit
  * RSI level exit: shorts close RSI<25, longs close RSI>75 (after min hold)

Backtest contract: per-bar returns, trade-level PF, validate_returns.
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
             atr_period: int = 14,
             near_ema_atr_mult: float = 0.35,
             rsi_period: int = 9,
             min_consec: int = 2,
             rsi_short_lo: float = 22.0,
             rsi_short_hi: float = 55.0,
             rsi_long_lo: float = 45.0,
             rsi_long_hi: float = 78.0,
             rsi_exit_lo: float = 25.0,
             rsi_exit_hi: float = 75.0,
             rsi_min_hold: int = 12,
             comm_pts: float = 7.0,
             slip_pts: float = 1.0,
             floor_pts: float = 1.0,
             risk_pct: float = 0.5,
             max_positions: int = 5,
             enable_short1: bool = True,
             enable_short2: bool = True,
             enable_short5: bool = True,
             enable_long1: bool = True,
             enable_long2: bool = True,
             enable_long5: bool = True,
             use_profit_gate: bool = True,
             rsi_exit_enabled: bool = True):
    df = df.copy().reset_index(drop=True)
    n = len(df)
    point = point_size(symbol)

    time = pd.to_datetime(df["time"])
    open_px = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)

    atr = wilder_atr(df, atr_period).to_numpy(float)
    rsi = wilder_rsi(pd.Series(close), rsi_period).to_numpy(float)
    ema21 = pd.Series(close).ewm(span=21, adjust=False).mean().to_numpy(float)

    cost_side = cost_per_side(df, symbol, comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_times: list = []

    # Positions: list of dicts {side, entry, entry_bar}
    positions: list[dict] = []

    # Consecutive bars above/below EMA21 (causal, shift(1))
    consec_above = 0  # consecutive bars with close > EMA21
    consec_below = 0  # consecutive bars with close < EMA21

    # Swing high/low (fractal-based, causal)
    swing_high = 0.0
    swing_low = float("inf")
    swing_lookback = 5

    # RSI band state
    rsi_bars_held_short = 0
    rsi_bars_held_long = 0

    # Prev close for EMA21 crossover detection
    prev_close = 0.0

    for i in range(n):
        c = close[i]
        o = open_px[i]
        h = high[i]
        l = low[i]
        e = ema21[i]
        r = rsi[i] if not np.isnan(rsi[i]) else 50.0
        a = atr[i] if not np.isnan(atr[i]) else 0.0
        prev_c = close[i - 1] if i > 0 else o
        prev_e = ema21[i - 1] if i > 0 else e

        # --- update consecutive counts (causal: based on bar i-1) -----
        if i > 0:
            if close[i - 1] > ema21[i - 1]:
                consec_above += 1
                consec_below = 0
            elif close[i - 1] < ema21[i - 1]:
                consec_below += 1
                consec_above = 0
            else:
                consec_above = 0
                consec_below = 0

        # --- near-EMA tolerance (ATR-scaled) --------------------------
        near_ema_tol = near_ema_atr_mult * a / point * point if a > 0 else 20 * point

        # --- update swing high/low (causal: based on bars up to i-1) --
        if i >= swing_lookback:
            lookback_high = max(high[i - swing_lookback:i])
            lookback_low = min(low[i - swing_lookback:i])
            swing_high = lookback_high
            swing_low = lookback_low

        # --- manage open positions ------------------------------------
        still_open: list[dict] = []
        for pos in positions:
            side = pos["side"]
            entry_px = pos["entry"]
            entry_bar = pos["entry_bar"]

            # in-profit check
            if side == -1:  # short
                in_profit = c < entry_px
            else:  # long
                in_profit = c > entry_px

            # EMA21 crossover detection (close crossed EMA21)
            close_above_ema = (prev_close > 0.0 and prev_close <= e and c > e)
            close_below_ema = (prev_close > 0.0 and prev_close >= e and c < e)

            exit_px = None
            exit_reason = ""

            # --- Unconditional: price closes back over EMA21 ------------
            if side == -1 and close_above_ema:
                exit_px = c
                exit_reason = "EMA21 flip up"
            elif side == 1 and close_below_ema:
                exit_px = c
                exit_reason = "EMA21 flip down"

            # --- If no EMA21 flip, check profit-gated exits ------------
            if exit_px is None:
                if side == -1 and c < e:  # short below EMA21
                    if use_profit_gate and not in_profit:
                        # hold: below EMA21 but not in profit -> don't exit
                        pass
                    else:
                        # in profit (or gate off): check weak structure
                        if l > swing_low:
                            exit_px = c
                            exit_reason = "weak structure below EMA21"
                elif side == 1 and c > e:  # long above EMA21
                    if use_profit_gate and not in_profit:
                        # hold: above EMA21 but not in profit -> don't exit
                        pass
                    else:
                        # in profit (or gate off): check weak structure
                        if h < swing_high:
                            exit_px = c
                            exit_reason = "weak structure above EMA21"

            # --- RSI level exit ----------------------------------------
            if exit_px is None and rsi_exit_enabled:
                if side == -1:
                    rsi_bars_held_short += 1
                    if rsi_bars_held_short >= rsi_min_hold and r < rsi_exit_lo:
                        exit_px = c
                        exit_reason = f"RSI below {rsi_exit_lo}"
                else:
                    rsi_bars_held_long += 1
                    if rsi_bars_held_long >= rsi_min_hold and r > rsi_exit_hi:
                        exit_px = c
                        exit_reason = f"RSI above {rsi_exit_hi}"

            # --- mark to market at close -------------------------------
            if i == entry_bar:
                bar_ret = side * (c / o - 1.0) - cost_side[i] / o
            else:
                bar_ret = side * (c / prev_c - 1.0)
            rets[i] = bar_ret

            if exit_px is not None:
                # exit: replace move-to-close with move-to-exit, charge exit cost
                if i == entry_bar:
                    exit_ret = side * (exit_px / o - 1.0) - cost_side[i] / o
                else:
                    exit_ret = side * (exit_px / prev_c - 1.0)
                exit_ret -= cost_side[i] / exit_px
                rets[i] = exit_ret

                gross = side * (exit_px / entry_px - 1.0)
                cost_rt = (cost_side[entry_bar] / entry_px +
                           cost_side[i] / exit_px)
                trades.append(gross - cost_rt)
                trade_times.append(time[i])
                if side == -1:
                    rsi_bars_held_short = 0
                else:
                    rsi_bars_held_long = 0
            else:
                still_open.append(pos)
                # reset RSI hold counter if position still open
                if side == -1 and c >= e:
                    rsi_bars_held_short = 0
                if side == 1 and c <= e:
                    rsi_bars_held_long = 0

        positions = still_open

        # --- evaluate entries (at close of bar i, enter at close[i]) ---
        # In the EA, entries happen at close of signal bar.
        green = c > o
        red = c < o
        prev_green = close[i - 1] > open_px[i - 1] if i > 0 else False
        prev_red = close[i - 1] < open_px[i - 1] if i > 0 else False
        prev_o = open_px[i - 1] if i > 0 else o

        num_shorts = sum(1 for p in positions if p["side"] == -1)
        num_longs = sum(1 for p in positions if p["side"] == 1)
        has_short = num_shorts > 0
        has_long = num_longs > 0

        struct_ok_short = True  # structure filter disabled by default
        struct_ok_long = True

        # --- SHORT 1: consecAbove>=2 && red && c<ema && 22<rsi<55 -----
        if (enable_short1 and not has_short and num_shorts < max_positions and
                struct_ok_short and consec_above >= min_consec and red and c < e and
                rsi_short_lo < r < rsi_short_hi):
            sl = h
            sl_dist = abs(c - sl)
            if sl_dist <= 0:
                sl_dist = 20 * point
            positions.append({"side": -1, "entry": c, "entry_bar": i})
            rsi_bars_held_short = 0

        # --- SHORT 2: consecBelow>=2 && green && near/at EMA21 && 22<rsi<55
        elif (enable_short2 and num_shorts < max_positions and
              struct_ok_short and consec_below >= min_consec and green and
              c >= e - near_ema_tol and c <= e and rsi_short_lo < r < rsi_short_hi):
            sl = swing_high if swing_high > 0 else h
            sl_dist = abs(c - sl)
            if sl_dist <= 0:
                sl_dist = 20 * point
            positions.append({"side": -1, "entry": c, "entry_bar": i})
            rsi_bars_held_short = 0

        # --- SHORT 5: !hasShort && consecBelow>=2 && c<ema && rsi>22 && nearEMA
        elif (enable_short5 and not has_short and num_shorts < max_positions and
              struct_ok_short and consec_below >= min_consec and c < e and r > rsi_short_lo and
              abs(c - e) <= near_ema_tol):
            sl = swing_high if swing_high > 0 else h + 20 * point
            sl_dist = abs(c - sl)
            if sl_dist <= 0:
                sl_dist = 20 * point
            positions.append({"side": -1, "entry": c, "entry_bar": i})
            rsi_bars_held_short = 0

        # --- LONG 1: consecBelow>=2 && green && c>ema && 45<rsi<78 ----
        elif (enable_long1 and not has_long and num_longs < max_positions and
              struct_ok_long and consec_below >= min_consec and green and c > e and
              rsi_long_lo < r < rsi_long_hi):
            sl = l
            sl_dist = abs(c - sl)
            if sl_dist <= 0:
                sl_dist = 20 * point
            positions.append({"side": 1, "entry": c, "entry_bar": i})
            rsi_bars_held_long = 0

        # --- LONG 2: consecAbove>=2 && red && near/at EMA21 && 45<rsi<78
        elif (enable_long2 and num_longs < max_positions and
              struct_ok_long and consec_above >= min_consec and red and
              c >= e and c <= e + near_ema_tol and rsi_long_lo < r < rsi_long_hi):
            sl = swing_low if swing_low < float("inf") else l
            sl_dist = abs(c - sl)
            if sl_dist <= 0:
                sl_dist = 20 * point
            positions.append({"side": 1, "entry": c, "entry_bar": i})
            rsi_bars_held_long = 0

        # --- LONG 5: !hasLong && consecAbove>=2 && c>ema && rsi<78 && nearEMA
        elif (enable_long5 and not has_long and num_longs < max_positions and
              struct_ok_long and consec_above >= min_consec and c > e and r < rsi_long_hi and
              abs(c - e) <= near_ema_tol):
            sl = swing_low if swing_low < float("inf") else l - 20 * point
            sl_dist = abs(c - sl)
            if sl_dist <= 0:
                sl_dist = 20 * point
            positions.append({"side": 1, "entry": c, "entry_bar": i})
            rsi_bars_held_long = 0

        # --- save prev close for next bar crossover detection ---------
        prev_close = c

    # --- close any open positions at last close ------------------------
    if positions:
        i = n - 1
        c = close[i]
        o = open_px[i]
        prev_c = close[i - 1]
        for pos in positions:
            side = pos["side"]
            entry_px = pos["entry"]
            entry_bar = pos["entry_bar"]
            if i == entry_bar:
                exit_ret = side * (c / o - 1.0) - cost_side[i] / o
            else:
                exit_ret = side * (c / prev_c - 1.0)
            exit_ret -= cost_side[i] / c
            rets[i] = exit_ret
            gross = side * (c / entry_px - 1.0)
            cost_rt = (cost_side[entry_bar] / entry_px + cost_side[i] / c)
            trades.append(gross - cost_rt)
            trade_times.append(time[i])

    rets_series = pd.Series(rets, index=time)
    trades_arr = np.asarray(trades, dtype=float)

    validate_returns(rets_series, close, label="fxmopol_v2_ema21_profit_gate")

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

    print(f"\n=== fxmopol_v2 EMA21 Profit-Gated Exit | {symbol} M15 | {len(df)} bars ===")
    print(f"Start: $10000.00  End: ${10000*(1+total):,.2f}  PnL: ${10000*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  Wins: {int((trades>0).sum())}  Losses: {int((trades<0).sum())}  "
          f"WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    print(f"Avg win: ${10000*avg_win:+.2f}  Avg loss: ${10000*avg_loss:+.2f}")

    print(f"\n=== Last 12 months ===")
    publish_results(f"fxmopol_v2_ema21_gate_{symbol}", rets, last_months=12, last_days=21)

    print(f"\n=== Three-way split ===")
    splits = {
        "design":   ("2022-11-01", "2024-01-01"),
        "validation": ("2024-01-01", "2025-01-01"),
        "oos":      ("2025-01-01", "2026-12-31"),
    }
    print_split_monthly(f"fxmopol_v2_ema21_gate_{symbol}", rets, splits)

    verdict = "PASS" if (cagr > 0 and m["pf"] and m["pf"] > 1.0 and maxdd > -0.20) else "FAIL"
    print(f"\n=== RESULT: {symbol} M15 fxmopol_v2 EMA21 Profit-Gated Exit ===")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"WR: {m['winrate']:.1f}%  PF: {m['pf']}  Trades: {m['trades']}")
    print(f"=== {verdict} ===")
    return verdict, cagr, sharpe, maxdd, m


def run_gate_off(csv_path: str, symbol: str = "EURUSD"):
    df = pd.read_csv(csv_path, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)
    print(f"Loaded {len(df)} bars  {df['time'].min()} -> {df['time'].max()}")

    rets, trades, trade_times, df = backtest(df, symbol, use_profit_gate=False)

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

    print(f"\n=== fxmopol_v2 NO Profit Gate | {symbol} M15 | {len(df)} bars ===")
    print(f"Start: $10000.00  End: ${10000*(1+total):,.2f}  PnL: ${10000*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    print(f"Three-way split (gate OFF):")
    splits = {
        "design":   ("2022-11-01", "2024-01-01"),
        "validation": ("2024-01-01", "2025-01-01"),
        "oos":      ("2025-01-01", "2026-12-31"),
    }
    print_split_monthly(f"fxmopol_v2_no_gate_{symbol}", rets, splits)


if __name__ == "__main__":
    csv = sys.argv[1] if len(sys.argv) > 1 else r"C:\xampp\htdocs\GodE\research\data\mt5\EURUSD_M15.csv"
    sym = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    print("=== WITH profit gate (InpEMA21ProfitGate=true) ===")
    run(csv, sym)
    print("\n" + "="*70 + "\n")
    print("=== WITHOUT profit gate (InpEMA21ProfitGate=false) ===")
    run_gate_off(csv, sym)
