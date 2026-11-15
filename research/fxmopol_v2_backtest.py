#!/usr/bin/env python3
"""
fxmopol_v2_backtest.py

Simulates fxmopol_v2 entry/exit rules on MT5-exported CSV data with full PnL tracking.
Reports CAGR, Sharpe, maxDD, win rate, profit factor — like other research backtests.

Usage:
    .\.venv\Scripts\python.exe research\fxmopol_v2_backtest.py research\data\mt5\BTCUSD_M5.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {c.lower().strip(): c for c in df.columns}
    time_col = cols.get("datetime") or cols.get("time") or cols.get("date")
    df["datetime"] = pd.to_datetime(df[cols[time_col]])
    for c in ("open", "high", "low", "close"):
        df[c] = df[cols[c]].astype(float)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def rsi(close: pd.Series, period: int = 9) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = np.maximum(h - l, np.maximum((h - c).abs(), (l - c).abs()))
    return pd.Series(tr, index=df.index).rolling(period).mean()


def fractal_high(h: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(h):
        return False
    v = h.iloc[i]
    return bool(v > h.iloc[i - 1] and v > h.iloc[i - 2] and v > h.iloc[i + 1] and v > h.iloc[i + 2])


def fractal_low(l: pd.Series, i: int) -> bool:
    if i < 2 or i + 2 >= len(l):
        return False
    v = l.iloc[i]
    return bool(v < l.iloc[i - 1] and v < l.iloc[i - 2] and v < l.iloc[i + 1] and v < l.iloc[i + 2])


def backtest(df: pd.DataFrame, symbol: str, point: float,
             min_consec: int = 2, near_ema_pts: float = 200.0,
             near_ema_atr_mult: float = 0.35, risk_pct: float = 0.5,
             max_positions: int = 5, max_lot: float = 10.0,
             sl_buffer_pts: float = 20.0,
             cost_pts_per_side: float = 0.0,
             print_trades: bool = False):
    n = len(df)
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    e21 = ema(df["close"], 21).values
    r = rsi(df["close"], 9).values
    a = atr(df, 14).values

    # state
    consec_above = 0
    consec_below = 0
    positions = []  # list of dicts: side, entry_px, sl, lots, entry_i
    equity = 10000.0  # starting equity
    equity_curve = []
    trades = []  # completed round trips
    peak_equity = equity
    max_dd = 0.0

    def calc_lots(sl_dist_price):
        if sl_dist_price <= 0:
            sl_dist_price = sl_buffer_pts * point
        risk_amt = equity * (risk_pct / 100.0)
        # assume $1 per point per lot (BTCUSD approximate); use tick_value=1
        tick_value = 1.0
        lots = risk_amt / ((sl_dist_price / point) * tick_value)
        lots = max(0.01, min(max_lot, lots))
        return lots

    def close_price_at(i, side):
        # exit at next bar open (realistic)
        if i + 1 >= n:
            return None
        return o[i + 1]

    def pnl_points(px_close, side, px_entry):
        if side == "sell":
            return px_entry - px_close
        return px_close - px_entry

    start = 40  # warmup for EMA/RSI/ATR
    for i in range(start, n - 1):
        ci, oi, hi, li = c[i], o[i], h[i], l[i]
        emai = e21[i]
        rsi_i = r[i] if not pd.isna(r[i]) else 50.0
        atr_i = a[i] if not pd.isna(a[i]) else 20 * point
        green = ci > oi
        red = ci < oi
        green1 = c[i - 1] > o[i - 1]
        red1 = c[i - 1] < o[i - 1]

        prev_above = consec_above
        prev_below = consec_below

        if ci > emai:
            consec_above += 1
            consec_below = 0
        elif ci < emai:
            consec_below += 1
            consec_above = 0
        else:
            consec_above = 0
            consec_below = 0

        near_tol = near_ema_pts * point
        if near_ema_atr_mult > 0 and atr_i > 0:
            atr_tol = near_ema_atr_mult * atr_i
            if atr_tol > near_tol:
                near_tol = atr_tol

        # --- EXITS (check first) ---
        new_positions = []
        for pos in positions:
            exit_px = None
            exit_reason = ""
            if pos["side"] == "sell":
                # Exit1 short: prevConsecBelow>=N + green + close>ema
                if prev_below >= min_consec and green and ci > emai:
                    exit_px = ci
                    exit_reason = "EXIT1"
                # Exit2 short: close>swingLow (need swing low - approximate with recent low)
                elif li > min(l[max(0, i - 20):i]):
                    exit_px = ci
                    exit_reason = "EXIT2"
                # trailing stop
                elif pos["sl"] > 0 and ci >= pos["sl"]:
                    exit_px = pos["sl"]
                    exit_reason = "SL"
            else:  # buy
                if prev_above >= min_consec and red and ci < emai:
                    exit_px = ci
                    exit_reason = "EXIT1"
                elif hi < max(h[max(0, i - 20):i]):
                    exit_px = ci
                    exit_reason = "EXIT2"
                elif pos["sl"] > 0 and ci <= pos["sl"]:
                    exit_px = pos["sl"]
                    exit_reason = "SL"

            if exit_px is not None:
                px_close = exit_px
                # cost: round trip spread
                pts = pnl_points(px_close, pos["side"], pos["entry_px"]) / point
                pts -= 2 * cost_pts_per_side  # entry + exit spread
                pnl_usd = pts * point * pos["lots"]
                equity += pnl_usd
                trades.append({
                    "entry_i": pos["entry_i"],
                    "exit_i": i,
                    "side": pos["side"],
                    "entry_px": pos["entry_px"],
                    "exit_px": px_close,
                    "pnl_usd": pnl_usd,
                    "win": pnl_usd > 0,
                    "reason": exit_reason,
                })
            else:
                new_positions.append(pos)
        positions = new_positions

        # --- ENTRIES ---
        if len(positions) >= max_positions:
            # still update equity curve
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            max_dd = max(max_dd, peak_equity - equity)
            continue

        entry_side = None
        entry_px = None
        entry_sl = None
        entry_rule = ""

        # Short 1
        if (prev_above >= min_consec and red and ci < emai and 22.0 < rsi_i < 55.0):
            entry_side, entry_px = "sell", ci
            entry_sl = hi
            entry_rule = "SHORT1"
        # Short 2
        elif (prev_below >= min_consec and green and ci <= emai and
              ci >= emai - near_tol and 22.0 < rsi_i < 55.0):
            entry_side, entry_px = "sell", ci
            entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
            entry_rule = "SHORT2"
        # Short 3
        elif (prev_below >= min_consec and red and ci <= emai and ci < o[i - 1] and green1):
            entry_side, entry_px = "sell", ci
            entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
            entry_rule = "SHORT3"
        # Short 4
        elif (i >= 2 and c[i] > o[i] and ci < emai and
              c[i - 1] > o[i - 1] and c[i - 1] < e21[i - 1] and
              c[i - 2] > o[i - 2] and c[i - 2] < e21[i - 2]):
            entry_side, entry_px = "sell", ci
            entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
            entry_rule = "SHORT4"
        # Short 5
        elif (not any(p["side"] == "sell" for p in positions) and
              prev_below >= min_consec and ci < emai and rsi_i > 22.0 and
              emai - near_tol <= ci <= emai + near_tol):
            entry_side, entry_px = "sell", ci
            entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
            entry_rule = "SHORT5"
        # Reflex Short
        elif (i >= 3 and not any(p["side"] == "sell" for p in positions) and
              red and ci < emai and green1 and c[i - 1] > e21[i - 1] and
              c[i - 2] < e21[i - 2] and c[i - 3] < e21[i - 3]):
            entry_side, entry_px = "sell", ci
            entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
            entry_rule = "REFLEX_SHORT"
        # Long 1
        elif (prev_below >= min_consec and green and ci > emai and 45.0 < rsi_i < 78.0):
            entry_side, entry_px = "buy", ci
            entry_sl = li
            entry_rule = "LONG1"
        # Long 2
        elif (prev_above >= min_consec and red and ci >= emai and
              ci <= emai + near_tol and 45.0 < rsi_i < 78.0):
            entry_side, entry_px = "buy", ci
            entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
            entry_rule = "LONG2"
        # Long 3
        elif (prev_above >= min_consec and green and ci >= emai and ci > o[i - 1] and red1):
            entry_side, entry_px = "buy", ci
            entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
            entry_rule = "LONG3"
        # Long 4
        elif (i >= 2 and c[i] < o[i] and ci > emai and
              c[i - 1] < o[i - 1] and c[i - 1] > e21[i - 1] and
              c[i - 2] < o[i - 2] and c[i - 2] > e21[i - 2]):
            entry_side, entry_px = "buy", ci
            entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
            entry_rule = "LONG4"
        # Long 5
        elif (not any(p["side"] == "buy" for p in positions) and
              prev_above >= min_consec and ci > emai and rsi_i < 78.0 and
              emai - near_tol <= ci <= emai + near_tol):
            entry_side, entry_px = "buy", ci
            entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
            entry_rule = "LONG5"
        # Reflex Long
        elif (i >= 3 and not any(p["side"] == "buy" for p in positions) and
              green and ci > emai and red1 and c[i - 1] < e21[i - 1] and
              c[i - 2] > e21[i - 2] and c[i - 3] > e21[i - 3]):
            entry_side, entry_px = "buy", ci
            entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
            entry_rule = "REFLEX_LONG"

        if entry_side is not None:
            sl_dist = abs(entry_px - entry_sl)
            lots = calc_lots(sl_dist)
            positions.append({
                "side": entry_side,
                "entry_px": entry_px,
                "sl": entry_sl,
                "lots": lots,
                "entry_i": i,
            })

        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity - equity)

    # --- metrics ---
    if not trades:
        print("No trades executed.")
        return

    trades_df = pd.DataFrame(trades)
    total_pnl = trades_df["pnl_usd"].sum()
    wins = trades_df[trades_df["win"]]
    losses = trades_df[~trades_df["win"]]
    win_rate = len(wins) / len(trades_df) * 100
    avg_win = wins["pnl_usd"].mean() if len(wins) else 0
    avg_loss = losses["pnl_usd"].mean() if len(losses) else 0
    profit_factor = wins["pnl_usd"].sum() / abs(losses["pnl_usd"].sum()) if len(losses) and losses["pnl_usd"].sum() != 0 else float("inf")

    # returns
    ec = pd.Series(equity_curve)
    rets = ec.pct_change().dropna()
    periods_per_year = 365 * 24 * 12  # M5 bars per year approx
    years = n / periods_per_year
    cagr = ((equity / 10000.0) ** (1 / max(years, 0.001)) - 1) * 100 if years > 0 else 0
    sharpe = (rets.mean() / rets.std() * np.sqrt(periods_per_year)) if rets.std() > 0 else 0
    max_dd_pct = (max_dd / peak_equity) * 100 if peak_equity > 0 else 0

    print(f"=== fxmopol_v2 backtest | {symbol} | {n} bars ===")
    print(f"Starting equity: $10,000  Ending equity: ${equity:,.2f}")
    print(f"Total PnL: ${total_pnl:,.2f}  CAGR: {cagr:.2f}%  Sharpe: {sharpe:.2f}")
    print(f"Max DD: ${max_dd:,.2f} ({max_dd_pct:.2f}%)  Win rate: {win_rate:.1f}%")
    print(f"Trades: {len(trades_df)}  Wins: {len(wins)}  Losses: {len(losses)}")
    print(f"Avg win: ${avg_win:.2f}  Avg loss: ${avg_loss:.2f}  Profit factor: {profit_factor:.2f}")
    print(f"Cost: {cost_pts_per_side} pts/side  Risk: {risk_pct}%/trade  NearEMA: {near_ema_pts}pts / {near_ema_atr_mult}xATR")
    print()
    print("=== By rule ===")
    for rule, group in trades_df.groupby("reason"):
        if group.empty:
            continue
        g_wins = group[group["win"]]
        print(f"  {rule:12s}: n={len(group):4d}  net=${group['pnl_usd'].sum():9.2f}  "
              f"wr={100*len(g_wins)/len(group):5.1f}%  PF={g_wins['pnl_usd'].sum()/abs(group[~group['win']]['pnl_usd'].sum()):.2f}" if len(group[~group['win']]) and group[~group['win']]['pnl_usd'].sum()!=0 else f"  {rule:12s}: n={len(group):4d}  net=${group['pnl_usd'].sum():9.2f}  wr={100*len(g_wins)/len(group):5.1f}%  PF=inf")


def main():
    if len(sys.argv) < 2:
        print("Usage: fxmopol_v2_backtest.py <CSV> [symbol] [point] [cost_pts_per_side]")
        print("  Example: fxmopol_v2_backtest.py research\\data\\mt5\\BTCUSD_M5.csv BTCUSD 0.1 5")
        sys.exit(1)
    path = sys.argv[1]
    symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSD"
    point = float(sys.argv[3]) if len(sys.argv) > 3 else 0.1
    cost_pts = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    df = load_csv(path)
    backtest(df, symbol, point, cost_pts_per_side=cost_pts)


if __name__ == "__main__":
    main()
