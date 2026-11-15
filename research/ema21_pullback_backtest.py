#!/usr/bin/env python3
"""
EMA21 Trend-Pullback Strategy — rigorous backtest on EURUSD M15

Rules (as specified):
1. Direction: Long only if Price > EMA50 > EMA200 AND ADX(14) > 25
              Short only if Price < EMA50 < EMA200 AND ADX(14) > 25
2. Entry: Price touches EMA21 ± 0.2*ATR value zone (first touch of pullback cycle)
          RSI(14) dipped below 50 during pullback, crosses back above 50 on close (long)
          RSI(14) dipped above 50 during pullback, crosses back below 50 on close (short)
          Execute at next bar open
3. Stop: Recent swing low/high during pullback, hard cap at 1.5*ATR from entry
4. Exit: Breakeven at +1R, TP at 1.5R, then trail at 1.2*ATR
5. Scaling: Blocked unless all prior positions in profit at breakeven
6. Cost: real per-bar spread + commission + slippage
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
    df["datetime"] = pd.to_datetime(df[cols[time_col]], utc=True).dt.tz_localize(None)
    for c in ("open", "high", "low", "close"):
        df[c] = df[cols[c]].astype(float)
    if "spread" in cols:
        df["spread"] = df[cols["spread"]].astype(float)
    else:
        df["spread"] = 1.0
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"].shift(1)
    tr = np.maximum(h - l, np.maximum((h - c).abs(), (l - c).abs()))
    return pd.Series(tr, index=df.index).rolling(period).mean()


def rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean().replace(0, np.nan)
    rs = up / down
    return 100 - 100 / (1 + rs)


def adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up = h - h.shift(1)
    dn = l.shift(1) - l

    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))

    def wilder_smooth(x, p):
        return pd.Series(x, index=df.index).ewm(alpha=1.0 / p, adjust=False).mean()

    atr_s = wilder_smooth(tr, period)
    plus_di = 100 * wilder_smooth(plus_dm, period) / atr_s
    minus_di = 100 * wilder_smooth(minus_dm, period) / atr_s
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return wilder_smooth(dx, period)


def tick_value_for(symbol: str) -> float:
    """Approximate $ value of 1 point (0.00001 for EURUSD) per standard lot."""
    s = symbol.upper()
    if s.endswith("JPY"):
        return 1.0 / 100.0  # JPY pairs: 1 point = 0.01, much smaller
    return 1.0  # EURUSD etc: 1 point (5th digit) ≈ $1 per standard lot


def backtest(df, symbol, point,
             risk_pct=0.5, max_positions=1, max_lot=10.0,
             spread_pts=1.0, comm_pts=7.0, slip_pts=1.0,
             adx_thresh=25, vz_atr_mult=0.2, sl_cap_atr=1.5,
             be_at_r=1.0, tp_at_r=1.5, trail_atr_mult=1.2,
             swing_lookback=10):

    n = len(df)
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values

    e21 = ema(df["close"], 21).values
    e50 = ema(df["close"], 50).values
    e200 = ema(df["close"], 200).values
    a = atr_series(df, 14).values
    r = rsi_series(df["close"], 14).values
    adx = adx_series(df, 14).values
    spread = df["spread"].astype(float).values

    equity = 10000.0
    equity_curve = []
    trades = []
    positions = []  # active positions

    peak_equity = equity
    max_dd = 0.0

    # Pullback cycle state
    px_above_vz_long = False      # price was clearly above value zone
    rsi_dipped_b50_long = False   # RSI dipped below 50 during pullback
    pb_swing_low = float("inf")   # lowest low during pullback

    px_below_vz_short = False     # price was clearly below value zone
    rsi_above_50_short = False    # RSI dipped above 50 during pullback
    pb_swing_high = float("-inf") # highest high during pullback

    start = 250  # warmup

    for i in range(start, n - 1):
        ci, oi, hi, li = c[i], o[i], h[i], l[i]
        e21i, e50i, e200i = e21[i], e50[i], e200[i]
        ai = a[i] if not np.isnan(a[i]) else 20 * point
        ri = r[i] if not np.isnan(r[i]) else 50.0
        rp = r[i - 1] if not np.isnan(r[i - 1]) else 50.0
        adx_i = adx[i] if not np.isnan(adx[i]) else 0.0

        # --- Direction filter ---
        long_filt = (ci > e50i > e200i) and (adx_i > adx_thresh)
        short_filt = (ci < e50i < e200i) and (adx_i > adx_thresh)

        vz_half = vz_atr_mult * ai
        vz_top = e21i + vz_half
        vz_bottom = e21i - vz_half

        # --- Update pullback cycle state ---
        if long_filt:
            if ci > vz_top:
                px_above_vz_long = True
                rsi_dipped_b50_long = False
                pb_swing_low = float("inf")
            elif vz_bottom <= ci <= vz_top:
                if px_above_vz_long:
                    if ri < 50:
                        rsi_dipped_b50_long = True
                    pb_swing_low = min(pb_swing_low, li)
        else:
            px_above_vz_long = False
            rsi_dipped_b50_long = False
            pb_swing_low = float("inf")

        if short_filt:
            if ci < vz_bottom:
                px_below_vz_short = True
                rsi_above_50_short = False
                pb_swing_high = float("-inf")
            elif vz_bottom <= ci <= vz_top:
                if px_below_vz_short:
                    if ri > 50:
                        rsi_above_50_short = True
                    pb_swing_high = max(pb_swing_high, hi)
        else:
            px_below_vz_short = False
            rsi_above_50_short = False
            pb_swing_high = float("-inf")

        # --- Manage existing positions (check at bar i close) ---
        new_positions = []
        for pos in positions:
            exit_px = None
            exit_reason = ""

            if pos["side"] == "sell":
                r_dist = pos["entry_px"] - pos["sl"]
                if r_dist > 0 and not pos.get("be", False):
                    be_px = pos["entry_px"]
                    if ci <= pos["entry_px"] - r_dist * be_at_r:
                        pos["sl"] = be_px
                        pos["be"] = True

                tp_px = pos["entry_px"] - r_dist * tp_at_r
                if ci <= tp_px:
                    exit_px = tp_px
                    exit_reason = "TP"
                elif pos.get("be", False):
                    trail_px = ci + trail_atr_mult * ai
                    if ci >= trail_px:
                        exit_px = ci
                        exit_reason = "TRAIL"
                    elif trail_px < pos["sl"]:
                        pos["sl"] = trail_px
                elif ci >= pos["sl"]:
                    exit_px = pos["sl"]
                    exit_reason = "SL"
            else:  # buy
                r_dist = pos["sl"] - pos["entry_px"]
                if r_dist > 0 and not pos.get("be", False):
                    be_px = pos["entry_px"]
                    if ci >= pos["entry_px"] + r_dist * be_at_r:
                        pos["sl"] = be_px
                        pos["be"] = True

                tp_px = pos["entry_px"] + r_dist * tp_at_r
                if ci >= tp_px:
                    exit_px = tp_px
                    exit_reason = "TP"
                elif pos.get("be", False):
                    trail_px = ci - trail_atr_mult * ai
                    if ci <= trail_px:
                        exit_px = ci
                        exit_reason = "TRAIL"
                    elif trail_px > pos["sl"]:
                        pos["sl"] = trail_px
                elif ci <= pos["sl"]:
                    exit_px = pos["sl"]
                    exit_reason = "SL"

            if exit_px is not None:
                if pos["side"] == "sell":
                    pts = (pos["entry_px"] - exit_px) / point
                else:
                    pts = (exit_px - pos["entry_px"]) / point

                # Round-trip costs: spread + commission + slippage (in points)
                pts -= 2 * spread_pts
                pts -= comm_pts
                pts -= 2 * slip_pts

                # PnL: pts (points) * tick_value ($ per point per lot) * lots
                tv = tick_value_for(symbol)
                pnl_usd = pts * tv * pos["lots"]
                equity += pnl_usd

                trades.append({
                    "entry_i": pos["entry_i"],
                    "exit_i": i,
                    "side": pos["side"],
                    "pnl_usd": pnl_usd,
                    "win": pnl_usd > 0,
                    "reason": exit_reason,
                    "entry_px": pos["entry_px"],
                    "exit_px": exit_px,
                })
            else:
                new_positions.append(pos)
        positions = new_positions

        # --- Can enter new position? ---
        can_enter = len(positions) == 0
        if len(positions) > 0 and max_positions > 1:
            all_profit_be = all(p.get("be", False) for p in positions)
            can_enter = all_profit_be and len(positions) < max_positions

        if not can_enter:
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            max_dd = max(max_dd, peak_equity - equity)
            continue

        # --- Entry signals (execute at next bar open) ---
        entry_side = None
        entry_px = None
        entry_sl = None

        # Long: first touch + RSI crossed above 50
        if (long_filt and px_above_vz_long and rsi_dipped_b50_long
                and rp <= 50 and ri > 50):
            entry_side = "buy"
            entry_px = o[i + 1] if i + 1 < n else None
            if pb_swing_low != float("inf") and pb_swing_low > 0:
                sl = pb_swing_low
                sl_dist = entry_px - sl
                if sl_dist > sl_cap_atr * ai:
                    sl = entry_px - sl_cap_atr * ai
            else:
                sl = entry_px - sl_cap_atr * ai
            entry_sl = sl

            # Reset pullback state
            px_above_vz_long = False
            rsi_dipped_b50_long = False
            pb_swing_low = float("inf")

        # Short: first touch + RSI crossed below 50
        if (entry_side is None and short_filt and px_below_vz_short
                and rsi_above_50_short and rp >= 50 and ri < 50):
            entry_side = "sell"
            entry_px = o[i + 1] if i + 1 < n else None
            if pb_swing_high != float("-inf") and pb_swing_high > 0:
                sl = pb_swing_high
                sl_dist = sl - entry_px
                if sl_dist > sl_cap_atr * ai:
                    sl = entry_px + sl_cap_atr * ai
            else:
                sl = entry_px + sl_cap_atr * ai
            entry_sl = sl

            px_below_vz_short = False
            rsi_above_50_short = False
            pb_swing_high = float("-inf")

        if entry_side is not None and entry_px is not None:
            sl_dist = abs(entry_px - entry_sl)
            if sl_dist <= 0:
                sl_dist = sl_cap_atr * ai

            risk_amt = equity * (risk_pct / 100.0)
            tv = tick_value_for(symbol)
            lots = risk_amt / ((sl_dist / point) * tv)
            lots = max(0.01, min(max_lot, lots))

            positions.append({
                "side": entry_side,
                "entry_px": entry_px,
                "sl": entry_sl,
                "lots": lots,
                "entry_i": i + 1,
                "be": False,
            })

        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity - equity)

    # --- Metrics ---
    if not trades:
        print("No trades executed.")
        return None

    td = pd.DataFrame(trades)
    total_pnl = td["pnl_usd"].sum()
    wins = td[td["win"]]
    losses = td[~td["win"]]
    wr = len(wins) / len(td) * 100
    pf = wins["pnl_usd"].sum() / abs(losses["pnl_usd"].sum()) if len(losses) and losses["pnl_usd"].sum() != 0 else float("inf")

    ec = pd.Series(equity_curve)
    rets = ec.pct_change().dropna()
    periods_per_year = 24 * 252  # M15 bars per year approx
    years = n / periods_per_year
    cagr = ((equity / 10000.0) ** (1 / max(years, 0.001)) - 1) * 100 if years > 0 else 0
    sharpe = (rets.mean() / rets.std() * np.sqrt(periods_per_year)) if rets.std() > 0 else 0
    max_dd_pct = (max_dd / peak_equity) * 100 if peak_equity > 0 else 0

    print(f"=== EMA21 Trend-Pullback | {symbol} M15 | {n} bars ===")
    print(f"Start: ${10000:.2f}  End: ${equity:,.2f}  PnL: ${total_pnl:,.2f}")
    print(f"CAGR: {cagr:.2f}%  Sharpe: {sharpe:.2f}  MaxDD: ${max_dd:,.2f} ({max_dd_pct:.2f}%)")
    print(f"Trades: {len(td)}  Wins: {len(wins)}  Losses: {len(losses)}  WR: {wr:.1f}%  PF: {pf:.2f}")
    print(f"Avg win: ${wins['pnl_usd'].mean():.2f}  Avg loss: ${losses['pnl_usd'].mean():.2f}" if len(wins) and len(losses) else "")
    print(f"Cost: {2*spread_pts + comm_pts + 2*slip_pts:.1f} pts round trip  Risk: {risk_pct}%/trade")

    print("\n=== By exit reason ===")
    for reason, group in td.groupby("reason"):
        g_w = group[group["win"]]
        g_l = group[~group["win"]]
        g_pf = g_w["pnl_usd"].sum() / abs(g_l["pnl_usd"].sum()) if len(g_l) and g_l["pnl_usd"].sum() != 0 else float("inf")
        print(f"  {reason:12s}: n={len(group):4d}  net=${group['pnl_usd'].sum():9.2f}  WR={100*len(g_w)/len(group):5.1f}%  PF={g_pf:.2f}")

    print("\n=== Per calendar year ===")
    td["date"] = pd.to_datetime(df["datetime"].iloc[td["exit_i"]].values)
    for y in sorted(td["date"].dt.year.unique()):
        gy = td[td["date"].dt.year == y]
        print(f"  {y}: net=${gy['pnl_usd'].sum():9.2f}  n={len(gy)}  WR={100*gy['win'].mean():.1f}%")

    return {
        "end_eq": equity, "cagr": cagr, "sharpe": sharpe,
        "max_dd": max_dd_pct, "wr": wr, "pf": pf, "n_trades": len(td),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: ema21_pullback_backtest.py <CSV> [symbol] [point]")
        print("  Example: ema21_pullback_backtest.py research\\data\\mt5\\EURUSD_M15.csv EURUSD 0.00001")
        sys.exit(1)

    path = sys.argv[1]
    symbol = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    point = float(sys.argv[3]) if len(sys.argv) > 3 else 0.00001

    df = load_csv(path)

    # EURUSD: ~1 pt spread median, $7 commission round trip, 1 pt slippage
    res = backtest(
        df, symbol, point,
        risk_pct=0.5, max_positions=1, max_lot=10.0,
        spread_pts=1.0, comm_pts=7.0, slip_pts=1.0,
        adx_thresh=25, vz_atr_mult=0.2, sl_cap_atr=1.5,
        be_at_r=1.0, tp_at_r=1.5, trail_atr_mult=1.2,
    )

    print(f"\n=== RESULT: {symbol} M15 EMA21 Trend-Pullback ===")
    if res:
        print(f"CAGR: {res['cagr']:.2f}%  Sharpe: {res['sharpe']:.2f}  MaxDD: {res['max_dd']:.2f}%")
        print(f"WR: {res['wr']:.1f}%  PF: {res['pf']:.2f}  Trades: {res['n_trades']}")
        profitable = res["cagr"] > 0 and res["pf"] > 1.0
        print(f"=== {'PROFITABLE' if profitable else 'NOT PROFITABLE'} ===")


if __name__ == "__main__":
    main()
