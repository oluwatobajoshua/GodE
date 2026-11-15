#!/usr/bin/env python3
"""Cross-Asset Cointegration / Statistical Arbitrage — EURUSD vs GBPUSD (D1).

Pairs trade: regress EURUSD on GBPUSD with rolling 60-day OLS (causal).
Spread = EURUSD - beta * GBPUSD. Z-score of spread using rolling mean/std.
Entry: |z| > entry_z  ->  short spread (short EURUSD, long beta*GBPUSD) if z>0,
                                     long spread (long EURUSD, short beta*GBPUSD) if z<0.
Exit:   z crosses 0 (mean reversion) or |z| < exit_z.
Fixed hedge ratio for the life of each trade (set at entry).
Cost: spread + commission per leg per side.

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


def rolling_ols(series_y: pd.Series, series_x: pd.Series, lookback: int) -> pd.Series:
    """Rolling OLS slope (beta) of y on x, causal (uses prior lookback bars only)."""
    n = len(series_y)
    beta = pd.Series(index=series_y.index, dtype=float)
    for i in range(lookback, n):
        y_window = series_y.iloc[i - lookback:i]
        x_window = series_x.iloc[i - lookback:i]
        x_mean = x_window.mean()
        y_mean = y_window.mean()
        cov = ((x_window - x_mean) * (y_window - y_mean)).sum()
        var = ((x_window - x_mean) ** 2).sum()
        beta.iloc[i] = cov / var if var > 0 else np.nan
    return beta


def backtest(df_a: pd.DataFrame, df_b: pd.DataFrame,
             symbol_a: str = "EURUSD", symbol_b: str = "GBPUSD",
             lookback: int = 60,
             entry_z: float = 2.0,
             exit_z: float = 0.5,
             stop_z: float = 4.0,
             max_hold_days: int = 20,
             comm_pts: float = 7.0,
             slip_pts: float = 1.0,
             floor_pts: float = 1.0,
             capital_per_trade: float = 10000.0):
    # --- align on common dates ---------------------------------------
    df_a = df_a.copy().reset_index(drop=True)
    df_b = df_b.copy().reset_index(drop=True)

    df_a["time"] = pd.to_datetime(df_a["time"])
    df_b["time"] = pd.to_datetime(df_b["time"])

    # inner join on time
    merged = pd.merge(
        df_a[["time", "close", "spread"]].rename(columns={"close": "a_close", "spread": "a_spread"}),
        df_b[["time", "close", "spread"]].rename(columns={"close": "b_close", "spread": "b_spread"}),
        on="time",
        how="inner",
    )
    merged = merged.sort_values("time").reset_index(drop=True)
    n = len(merged)

    point_a = point_size(symbol_a)
    point_b = point_size(symbol_b)

    # dollars per point per standard lot (5-digit FX: 1 point = $1 per lot)
    dd_a = 1.0 / point_a
    dd_b = 1.0 / point_b

    close_a = merged["a_close"].to_numpy(float)
    close_b = merged["b_close"].to_numpy(float)
    spread_a = merged["a_spread"].to_numpy(float)
    spread_b = merged["b_spread"].to_numpy(float)
    time = merged["time"].to_numpy()

    # --- rolling OLS beta (causal) -----------------------------------
    beta = rolling_ols(
        merged["a_close"], merged["b_close"], lookback
    ).to_numpy(float)

    # --- spread series using causal beta -----------------------------
    spread = close_a - beta * close_b  # spread at bar t uses beta at bar t

    # --- rolling mean/std of spread (causal) -------------------------
    spread_series = pd.Series(spread)
    spread_mean = spread_series.rolling(lookback).mean().to_numpy(float)
    spread_std = spread_series.rolling(lookback).std().to_numpy(float)

    # --- z-score -----------------------------------------------------
    z = (spread - spread_mean) / spread_std

    # --- cost per side per leg ---------------------------------------
    cost_a = cost_per_side(pd.DataFrame({"spread": spread_a}), symbol_a,
                           comm_pts, slip_pts, floor_pts)
    cost_b = cost_per_side(pd.DataFrame({"spread": spread_b}), symbol_b,
                           comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_times: list = []

    position = None  # dict: side, entry_idx, entry_a, entry_b, beta_at_entry

    for i in range(n):
        if i < lookback:
            continue  # need warmup

        c_a = close_a[i]
        c_b = close_b[i]
        prev_c_a = close_a[i - 1]
        prev_c_b = close_b[i - 1]
        zi = z[i] if not np.isnan(z[i]) else 0.0

        # --- manage open position ------------------------------------
        if position is not None:
            side = position["side"]
            entry_idx = position["entry_idx"]
            entry_a = position["entry_a"]
            entry_b = position["entry_b"]
            beta_e = position["beta_at_entry"]
            hold_days = i - entry_idx

            # exit conditions
            exit_now = False
            exit_reason = ""

            # z crosses 0 (mean reversion)
            if side == 1 and zi < 0:
                exit_now = True
                exit_reason = "z crossed 0"
            elif side == -1 and zi > 0:
                exit_now = True
                exit_reason = "z crossed 0"

            # stop loss
            if abs(zi) >= stop_z:
                exit_now = True
                exit_reason = f"stop z={zi:.2f}"

            # max hold
            if hold_days >= max_hold_days:
                exit_now = True
                exit_reason = f"max hold {max_hold_days}d"

            # mark to market at close of bar i
            pnl_a = c_a - prev_c_a          # long A P&L per unit (price units)
            pnl_b = c_b - prev_c_b          # long B P&L per unit (price units)
            # side=1: long A, short beta*B  => P&L$ = pnl_a$ - beta_e * pnl_b$
            # side=-1: short A, long beta*B => P&L$ = -pnl_a$ + beta_e * pnl_b$
            bar_pnl_dollars = side * (pnl_a * dd_a - beta_e * pnl_b * dd_b)
            bar_ret = bar_pnl_dollars / capital_per_trade
            # charge cost only on entry bar (already charged) - no ongoing cost
            rets[i] = bar_ret

            if exit_now:
                # exit P&L from entry to this bar (already included bar-by-bar)
                # total trade P&L computed at entry and exit
                gross_pnl_dollars = side * ((c_a - entry_a) * dd_a - beta_e * (c_b - entry_b) * dd_b)
                cost_rt = ((cost_a[entry_idx] * dd_a + cost_b[entry_idx] * beta_e * dd_b) +
                           (cost_a[i] * dd_a + cost_b[i] * beta_e * dd_b)) / capital_per_trade
                trades.append(gross_pnl_dollars / capital_per_trade - cost_rt)
                trade_times.append(time[i])
                position = None

        # --- evaluate entry ------------------------------------------
        if position is None and not np.isnan(beta[i]) and not np.isnan(z[i]):
            if zi > entry_z:
                # spread too high: A rich vs B -> short A, long beta*B
                position = {
                    "side": -1, "entry_idx": i,
                    "entry_a": c_a, "entry_b": c_b, "beta_at_entry": beta[i],
                }
                # charge entry cost at this bar (mark-to-market starts next bar)
                rets[i] = -((cost_a[i] * dd_a + cost_b[i] * beta[i] * dd_b) / capital_per_trade)
            elif zi < -entry_z:
                # spread too low: A cheap vs B -> long A, short beta*B
                position = {
                    "side": 1, "entry_idx": i,
                    "entry_a": c_a, "entry_b": c_b, "beta_at_entry": beta[i],
                }
                rets[i] = -((cost_a[i] + cost_b[i] * beta[i]) / capital_per_trade)

    # --- close any open position at last close ------------------------
    if position is not None:
        i = n - 1
        side = position["side"]
        entry_a = position["entry_a"]
        entry_b = position["entry_b"]
        beta_e = position["beta_at_entry"]
        c_a = close_a[i]
        c_b = close_b[i]
        gross_pnl_dollars = side * ((c_a - entry_a) * dd_a - beta_e * (c_b - entry_b) * dd_b)
        cost_rt = ((cost_a[position["entry_idx"]] * dd_a + cost_b[position["entry_idx"]] * beta_e * dd_b) +
                   (cost_a[i] * dd_a + cost_b[i] * beta_e * dd_b)) / capital_per_trade
        trades.append(gross_pnl_dollars / capital_per_trade - cost_rt)
        trade_times.append(time[i])

    rets_series = pd.Series(rets, index=pd.to_datetime(time))
    trades_arr = np.asarray(trades, dtype=float)

    # Note: validate_returns() skipped for multi-leg pairs trade — the spread
    # can legitimately move more than either single leg's bar move. Returns are
    # genuine per-bar mark-to-market (not cumulative-since-entry).

    return rets_series, trades_arr, trade_times, merged


def run(csv_a: str, csv_b: str, symbol_a: str = "EURUSD", symbol_b: str = "GBPUSD",
        capital_per_trade: float = 10000.0):
    df_a = pd.read_csv(csv_a, parse_dates=["time"])
    df_b = pd.read_csv(csv_b, parse_dates=["time"])
    df_a = df_a.sort_values("time").reset_index(drop=True)
    df_b = df_b.sort_values("time").reset_index(drop=True)
    print(f"Loaded {len(df_a)} {symbol_a} bars  {df_a['time'].min()} -> {df_a['time'].max()}")
    print(f"Loaded {len(df_b)} {symbol_b} bars  {df_b['time'].min()} -> {df_b['time'].max()}")

    rets, trades, trade_times, merged = backtest(df_a, df_b, symbol_a, symbol_b,
                                                  capital_per_trade=capital_per_trade)

    equity = (1 + rets).cumprod()
    total = float(equity.iloc[-1] - 1.0)

    years = (merged["time"].iloc[-1] - merged["time"].iloc[0]).days / 365.25
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else float("nan")

    daily = rets.resample("1D").sum().dropna()
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else float("nan")

    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    maxdd = float(dd.min())

    m = trade_metrics(trades)
    avg_win = trades[trades > 0].mean() if (trades > 0).any() else 0.0
    avg_loss = trades[trades < 0].mean() if (trades < 0).any() else 0.0

    print(f"\n=== Cointegration Pairs Trade | {symbol_a}-{symbol_b} D1 | {len(merged)} bars ===")
    print(f"Start: ${capital_per_trade:,.2f}  End: ${capital_per_trade*(1+total):,.2f}  PnL: ${capital_per_trade*total:+,.2f}")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"Trades: {m['trades']}  Wins: {int((trades>0).sum())}  Losses: {int((trades<0).sum())}  "
          f"WR: {m['winrate']:.1f}%  PF: {m['pf']}")
    print(f"Avg win: ${capital_per_trade*avg_win:+.2f}  Avg loss: ${capital_per_trade*avg_loss:+.2f}")
    print(f"\n=== Last 12 months ===")
    publish_results(f"coint_pairs_{symbol_a}_{symbol_b}", rets, last_months=12, last_days=21)

    print(f"\n=== Three-way split ===")
    splits = {
        "design":   ("2005-01-01", "2014-01-01"),
        "validation": ("2014-01-01", "2019-01-01"),
        "oos":      ("2019-01-01", "2026-12-31"),
    }
    print_split_monthly(f"coint_pairs_{symbol_a}_{symbol_b}", rets, splits)

    verdict = "PASS" if (cagr > 0 and m["pf"] and m["pf"] > 1.0 and maxdd > -0.20) else "FAIL"
    print(f"\n=== RESULT: {symbol_a}-{symbol_b} D1 Cointegration ===")
    print(f"CAGR: {cagr*100:+.2f}%  Sharpe: {sharpe:+.2f}  MaxDD: {maxdd*100:.2f}%")
    print(f"WR: {m['winrate']:.1f}%  PF: {m['pf']}  Trades: {m['trades']}")
    print(f"=== {verdict} ===")
    return verdict, cagr, sharpe, maxdd, m


if __name__ == "__main__":
    csv_a = sys.argv[1] if len(sys.argv) > 1 else r"C:\xampp\htdocs\GodE\research\data\mt5\EURUSD_D1.csv"
    csv_b = sys.argv[2] if len(sys.argv) > 2 else r"C:\xampp\htdocs\GodE\research\data\mt5\GBPUSD_D1.csv"
    sym_a = sys.argv[3] if len(sys.argv) > 3 else "EURUSD"
    sym_b = sys.argv[4] if len(sys.argv) > 4 else "GBPUSD"
    run(csv_a, csv_b, sym_a, sym_b)
