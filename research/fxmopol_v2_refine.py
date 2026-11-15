#!/usr/bin/env python3
"""
fxmopol_v2_refine.py

Parameter sweep / refinement of fxmopol_v2 rules on MT5 CSV data.
Tests: Exit1 on/off, min_consecutive, near-EMA tol, RSI bands, SL buffer, cost.

Usage:
    .\.venv\Scripts\python.exe research\fxmopol_v2_refine.py research\data\mt5\BTCUSD_M5.csv
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


def run(df, cfg):
    n = len(df)
    c = df["close"].values
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    e21 = ema(df["close"], 21).values
    r = rsi(df["close"], 9).values
    a = atr(df, 14).values

    point = cfg["point"]
    min_consec = cfg["min_consec"]
    near_ema_pts = cfg["near_ema_pts"]
    near_ema_atr_mult = cfg["near_ema_atr_mult"]
    risk_pct = cfg["risk_pct"]
    max_positions = cfg["max_positions"]
    max_lot = cfg["max_lot"]
    sl_buffer_pts = cfg["sl_buffer_pts"]
    cost_pts = cfg["cost_pts"]
    exit1_on = cfg["exit1_on"]
    exit2_on = cfg["exit2_on"]
    use_atr_sl = cfg["use_atr_sl"]
    atr_sl_mult = cfg["atr_sl_mult"]
    structure_on = cfg["structure_on"]
    rsi_band_mode = cfg["rsi_band_mode"]
    rsi_band_mid = cfg["rsi_band_mid"]
    rsi_band_hi = cfg["rsi_band_hi"]
    rsi_band_lo = cfg["rsi_band_lo"]
    rsi_band_margin = cfg["rsi_band_margin"]
    rsi_band_min_hold = cfg["rsi_band_min_hold"]
    rsi_band_require_trend = cfg["rsi_band_require_trend"]

    consec_above = 0
    consec_below = 0
    positions = []
    equity = 10000.0
    equity_curve = []
    trades = []
    peak_equity = equity
    max_dd = 0.0

    #--- Market structure state (causal, mirrors MQL5 UpdateMarketStructure)
    red_open_high = 0.0       # highest open of recent red candles (uptrend reclaim level)
    green_close_low = 0.0     # lowest close of recent green candles (downtrend break level)
    struct_intact_long = False   # uptrend structure intact
    struct_intact_short = False  # downtrend structure intact

    #--- RSI Band state
    rsi_prev = 0.0            # RSI on prior bar (for cross detection)
    ema_prev = 0.0            # EMA21 on prior bar (for slope)
    rsi_band_in_short = False # currently in RSI band short
    rsi_band_in_long  = False # currently in RSI band long
    rsi_band_bars_short = 0   # bars held in current short
    rsi_band_bars_long  = 0   # bars held in current long

    def calc_lots(sl_dist_price):
        if sl_dist_price <= 0:
            sl_dist_price = sl_buffer_pts * point
        risk_amt = equity * (risk_pct / 100.0)
        tick_value = 1.0
        lots = risk_amt / ((sl_dist_price / point) * tick_value)
        return max(0.01, min(max_lot, lots))

    start = 40
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

        #--- Market structure tracking (causal, mirrors MQL5 UpdateMarketStructure)
        if structure_on:
            if ci > emai:  # above EMA21 -> uptrend structure context
                if red:
                    if red_open_high == 0.0 or oi > red_open_high:
                        red_open_high = oi
                    struct_intact_long = False   # red appeared, waiting for green reclaim
                elif green:
                    if red_open_high > 0.0 and ci > red_open_high:
                        struct_intact_long = True  # green reclaimed above all red opens
                        red_open_high = 0.0      # reset after successful reclaim
                    elif red_open_high > 0.0 and ci <= red_open_high:
                        struct_intact_long = False  # green failed to reclaim
                    else:
                        struct_intact_long = True   # no prior red to reclaim
            else:
                red_open_high = 0.0
                struct_intact_long = False

            if ci < emai:  # below EMA21 -> downtrend structure context
                if green:
                    if green_close_low == 0.0 or ci < green_close_low:
                        green_close_low = ci
                    struct_intact_short = False   # green appeared, waiting for red break
                elif red:
                    if green_close_low > 0.0 and ci < green_close_low:
                        struct_intact_short = True   # red broke below all green closes
                        green_close_low = 0.0       # reset after successful break
                    elif green_close_low > 0.0 and ci >= green_close_low:
                        struct_intact_short = False   # red failed to break
                    else:
                        struct_intact_short = True   # no prior green to break
            else:
                green_close_low = 0.0
                struct_intact_short = False
        else:
            struct_intact_long = True
            struct_intact_short = True

        structOKShort = struct_intact_short or not structure_on
        structOKLong = struct_intact_long or not structure_on

        #--- RSI band cross detection
        ema_slope_up   = (ema_prev > 0.0 and emai > ema_prev)
        ema_slope_down = (ema_prev > 0.0 and emai < ema_prev)
        rsi_cross_down_mid = (rsi_prev > rsi_band_mid and rsi_i <= rsi_band_mid - rsi_band_margin)
        rsi_cross_up_mid   = (rsi_prev < rsi_band_mid and rsi_i >= rsi_band_mid + rsi_band_margin)

        # --- EXITS ---
        new_positions = []
        for pos in positions:
            exit_px = None
            exit_reason = ""
            if pos["side"] == "sell":
                if exit1_on and prev_below >= min_consec and green and ci > emai:
                    exit_px = ci
                    exit_reason = "EXIT1"
                elif exit2_on and li > min(l[max(0, i - 20):i]):
                    exit_px = ci
                    exit_reason = "EXIT2"
                elif structure_on and not struct_intact_short:
                    exit_px = ci
                    exit_reason = "STRUCT_BREAK_SHORT"
                elif rsi_band_mode and pos.get("rsi_band", False):
                    if rsi_band_bars_short >= rsi_band_min_hold and rsi_i < rsi_band_lo:
                        exit_px = ci
                        exit_reason = "RSI_BAND_SHORT_EXIT"
                        rsi_band_in_short = False
                        rsi_band_bars_short = 0
                    elif rsi_band_require_trend and ema_slope_up:
                        exit_px = ci
                        exit_reason = "RSI_BAND_SHORT_EXIT_TREND"
                        rsi_band_in_short = False
                        rsi_band_bars_short = 0
                elif pos["sl"] > 0 and ci >= pos["sl"]:
                    exit_px = pos["sl"]
                    exit_reason = "SL"
            else:
                if exit1_on and prev_above >= min_consec and red and ci < emai:
                    exit_px = ci
                    exit_reason = "EXIT1"
                elif exit2_on and hi < max(h[max(0, i - 20):i]):
                    exit_px = ci
                    exit_reason = "EXIT2"
                elif structure_on and not struct_intact_long:
                    exit_px = ci
                    exit_reason = "STRUCT_BREAK_LONG"
                elif rsi_band_mode and pos.get("rsi_band", False):
                    if rsi_band_bars_long >= rsi_band_min_hold and rsi_i > rsi_band_hi:
                        exit_px = ci
                        exit_reason = "RSI_BAND_LONG_EXIT"
                    elif rsi_band_require_trend and ema_slope_down:
                        exit_px = ci
                        exit_reason = "RSI_BAND_LONG_EXIT_TREND"
                elif pos["sl"] > 0 and ci <= pos["sl"]:
                    exit_px = pos["sl"]
                    exit_reason = "SL"

            if exit_px is not None:
                pts = (pos["entry_px"] - exit_px if pos["side"] == "sell" else exit_px - pos["entry_px"]) / point
                pts -= 2 * cost_pts
                equity += pts * point * pos["lots"]
                trades.append({
                    "entry_i": pos["entry_i"],
                    "exit_i": i,
                    "side": pos["side"],
                    "pnl_usd": pts * point * pos["lots"],
                    "win": (pts * point * pos["lots"]) > 0,
                    "reason": exit_reason,
                })
            else:
                new_positions.append(pos)
        positions = new_positions

        #--- Track bars held for RSI band positions
        if rsi_band_mode:
            has_rsi_short = False
            has_rsi_long = False
            for pos in positions:
                if pos.get("rsi_band", False):
                    if pos["side"] == "sell":
                        rsi_band_bars_short += 1
                        has_rsi_short = True
                    else:
                        rsi_band_bars_long += 1
                        has_rsi_long = True
            if not has_rsi_short:
                rsi_band_in_short = False
                rsi_band_bars_short = 0
            if not has_rsi_long:
                rsi_band_in_long = False
                rsi_band_bars_long = 0

        if len(positions) >= max_positions:
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            max_dd = max(max_dd, peak_equity - equity)
            continue

        entry_side = None
        entry_px = None
        entry_sl = None
        entry_rule = ""

        rsi_short_lo, rsi_short_hi = cfg["rsi_short_lo"], cfg["rsi_short_hi"]
        rsi_long_lo, rsi_long_hi = cfg["rsi_long_lo"], cfg["rsi_long_hi"]

        #--- RSI BAND entries (only when rsi_band_mode is on; replaces regular rules)
        if rsi_band_mode and len(positions) < max_positions:
            if not rsi_band_in_short and ema_slope_down and rsi_cross_down_mid:
                sl = hi
                sl_dist = abs(ci - sl)
                if sl_dist <= 0:
                    sl_dist = 20 * point
                lots = calc_lots(sl_dist)
                positions.append({
                    "side": "sell", "entry_px": ci, "sl": sl, "lots": lots,
                    "entry_i": i, "rsi_band": True,
                })
                rsi_band_in_short = True
                rsi_band_bars_short = 0
            elif not rsi_band_in_long and ema_slope_up and rsi_cross_up_mid:
                sl = li
                sl_dist = abs(ci - sl)
                if sl_dist <= 0:
                    sl_dist = 20 * point
                lots = calc_lots(sl_dist)
                positions.append({
                    "side": "buy", "entry_px": ci, "sl": sl, "lots": lots,
                    "entry_i": i, "rsi_band": True,
                })
                rsi_band_in_long = True
                rsi_band_bars_long = 0

        if not exit1_on or True:  # entries independent of exit1
            if (not rsi_band_mode and structOKShort and prev_above >= min_consec and red and ci < emai and rsi_short_lo < rsi_i < rsi_short_hi):
                entry_side, entry_px, entry_rule = "sell", ci, "SHORT1"
                entry_sl = hi if not use_atr_sl else ci + atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKShort and prev_below >= min_consec and green and ci <= emai and ci >= emai - near_tol and rsi_short_lo < rsi_i < rsi_short_hi):
                entry_side, entry_px, entry_rule = "sell", ci, "SHORT2"
                entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci + atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKShort and prev_below >= min_consec and red and ci <= emai and ci < o[i - 1] and green1):
                entry_side, entry_px, entry_rule = "sell", ci, "SHORT3"
                entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci + atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKShort and i >= 2 and c[i] > o[i] and ci < emai and c[i - 1] > o[i - 1] and c[i - 1] < e21[i - 1] and c[i - 2] > o[i - 2] and c[i - 2] < e21[i - 2]):
                entry_side, entry_px, entry_rule = "sell", ci, "SHORT4"
                entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci + atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKShort and not any(p["side"] == "sell" for p in positions) and prev_below >= min_consec and ci < emai and rsi_i > rsi_short_lo and emai - near_tol <= ci <= emai + near_tol):
                entry_side, entry_px, entry_rule = "sell", ci, "SHORT5"
                entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci + atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKShort and i >= 3 and not any(p["side"] == "sell" for p in positions) and red and ci < emai and green1 and c[i - 1] > e21[i - 1] and c[i - 2] < e21[i - 2] and c[i - 3] < e21[i - 3]):
                entry_side, entry_px, entry_rule = "sell", ci, "REFLEX_SHORT"
                entry_sl = max(h[max(0, i - 20):i]) if i >= 20 else hi + sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci + atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKLong and prev_below >= min_consec and green and ci > emai and rsi_long_lo < rsi_i < rsi_long_hi):
                entry_side, entry_px, entry_rule = "buy", ci, "LONG1"
                entry_sl = li if not use_atr_sl else ci - atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKLong and prev_above >= min_consec and red and ci >= emai and ci <= emai + near_tol and rsi_long_lo < rsi_i < rsi_long_hi):
                entry_side, entry_px, entry_rule = "buy", ci, "LONG2"
                entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci - atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKLong and prev_above >= min_consec and green and ci >= emai and ci > o[i - 1] and red1):
                entry_side, entry_px, entry_rule = "buy", ci, "LONG3"
                entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci - atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKLong and i >= 2 and c[i] < o[i] and ci > emai and c[i - 1] < o[i - 1] and c[i - 1] > e21[i - 1] and c[i - 2] < o[i - 2] and c[i - 2] > e21[i - 2]):
                entry_side, entry_px, entry_rule = "buy", ci, "LONG4"
                entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci - atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKLong and not any(p["side"] == "buy" for p in positions) and prev_above >= min_consec and ci > emai and rsi_i < rsi_long_hi and emai - near_tol <= ci <= emai + near_tol):
                entry_side, entry_px, entry_rule = "buy", ci, "LONG5"
                entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci - atr_sl_mult * atr_i
            elif (not rsi_band_mode and structOKLong and i >= 3 and not any(p["side"] == "buy" for p in positions) and green and ci > emai and red1 and c[i - 1] < e21[i - 1] and c[i - 2] > e21[i - 2] and c[i - 3] > e21[i - 3]):
                entry_side, entry_px, entry_rule = "buy", ci, "REFLEX_LONG"
                entry_sl = min(l[max(0, i - 20):i]) if i >= 20 else li - sl_buffer_pts * point
                if use_atr_sl:
                    entry_sl = ci - atr_sl_mult * atr_i

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

        #--- Save RSI band state for next bar cross detection
        rsi_prev = rsi_i
        ema_prev = emai

        equity_curve.append(equity)
        peak_equity = max(peak_equity, equity)
        max_dd = max(max_dd, peak_equity - equity)

    if not trades:
        return None

    trades_df = pd.DataFrame(trades)
    total_pnl = trades_df["pnl_usd"].sum()
    wins = trades_df[trades_df["win"]]
    losses = trades_df[~trades_df["win"]]
    win_rate = len(wins) / len(trades_df) * 100
    pf = wins["pnl_usd"].sum() / abs(losses["pnl_usd"].sum()) if len(losses) and losses["pnl_usd"].sum() != 0 else float("inf")

    ec = pd.Series(equity_curve)
    rets = ec.pct_change().dropna()
    periods_per_year = 365 * 24 * 12
    years = n / periods_per_year
    cagr = ((equity / 10000.0) ** (1 / max(years, 0.001)) - 1) * 100 if years > 0 else 0
    sharpe = (rets.mean() / rets.std() * np.sqrt(periods_per_year)) if rets.std() > 0 else 0
    max_dd_pct = (max_dd / peak_equity) * 100 if peak_equity > 0 else 0

    return {
        "end_eq": equity, "cagr": cagr, "sharpe": sharpe,
        "max_dd": max_dd_pct, "wr": win_rate, "pf": pf,
        "n_trades": len(trades_df),
        "exit1_losses": len(trades_df[(trades_df["reason"] == "EXIT1") & (~trades_df["win"])]),
        "exit1_n": len(trades_df[trades_df["reason"] == "EXIT1"]),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: fxmopol_v2_refine.py <CSV> [symbol] [point] [cost_pts]")
        sys.exit(1)
    path = sys.argv[1]
    symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSD"
    point = float(sys.argv[3]) if len(sys.argv) > 3 else 0.1
    cost_pts = float(sys.argv[4]) if len(sys.argv) > 4 else 5.0

    df = load_csv(path)

    base = dict(
        point=point, min_consec=2, near_ema_pts=200, near_ema_atr_mult=0.35,
        risk_pct=0.5, max_positions=5, max_lot=10.0, sl_buffer_pts=20,
        cost_pts=cost_pts, exit1_on=True, exit2_on=True,
        use_atr_sl=False, atr_sl_mult=1.5,
        rsi_short_lo=22, rsi_short_hi=55, rsi_long_lo=45, rsi_long_hi=78,
        structure_on=False,
        rsi_band_mode=False, rsi_band_mid=50, rsi_band_hi=75, rsi_band_lo=25,
        rsi_band_margin=3.0, rsi_band_min_hold=12, rsi_band_require_trend=True,
    )

    print(f"=== fxmopol_v2 refinement sweep | {symbol} | cost={cost_pts}pts/side ===\n")
    print(f"{'Config':38s} {'EndEq':>9s} {'CAGR':>7s} {'Sharpe':>7s} {'MaxDD':>7s} {'WR':>6s} {'PF':>6s} {'Trades':>7s} {'Exit1L':>7s}")
    print("-" * 105)

    results = []

    def show(name, cfg):
        res = run(df, cfg)
        if res is None:
            print(f"{name:38s} {'NO TRADES':>38s}")
            results.append((name, None))
            return
        print(f"{name:38s} {res['end_eq']:9.2f} {res['cagr']:7.2f} {res['sharpe']:7.2f} "
              f"{res['max_dd']:7.2f} {res['wr']:6.1f} {res['pf']:6.2f} {res['n_trades']:7d} {res['exit1_losses']:7d}")
        results.append((name, res))

    show("BASELINE", base)

    # 1. Exit1 off (the big loser)
    c1 = dict(base); c1["exit1_on"] = False
    show("Exit1 OFF", c1)

    # 2. Exit1 off + Exit2 off (pure SL)
    c2 = dict(base); c2["exit1_on"] = False; c2["exit2_on"] = False
    show("Exit1+Exit2 OFF", c2)

    # 3. Higher min_consecutive
    c3 = dict(base); c3["min_consec"] = 4
    show("minConsec=4", c3)
    c4 = dict(base); c4["min_consec"] = 6
    show("minConsec=6", c4)

    # 4. Near-EMA wider
    c5 = dict(base); c5["near_ema_pts"] = 500
    show("nearEMA=500pts", c5)
    c6 = dict(base); c6["near_ema_pts"] = 1000
    show("nearEMA=1000pts", c6)

    # 5. ATR-based SL
    c7 = dict(base); c7["use_atr_sl"] = True; c7["atr_sl_mult"] = 1.5
    show("ATR SL 1.5x", c7)
    c8 = dict(base); c8["use_atr_sl"] = True; c8["atr_sl_mult"] = 2.5
    show("ATR SL 2.5x", c8)

    # 6. Wider RSI bands
    c9 = dict(base); c9["rsi_short_lo"]=15; c9["rsi_short_hi"]=60; c9["rsi_long_lo"]=40; c9["rsi_long_hi"]=85
    show("RSI wide bands", c9)

    # 7. Exit1 off + minConsec=4 + ATR SL
    c10 = dict(base); c10["exit1_on"] = False; c10["min_consec"] = 4; c10["use_atr_sl"] = True; c10["atr_sl_mult"] = 2.0
    show("Exit1OFF+MC4+ATR2x", c10)

    # 8. Lower cost (tight spread)
    c11 = dict(base); c11["cost_pts"] = 1.0
    show("cost=1pt", c11)

    # 9. Exit1 off + wider bands + ATR SL
    c12 = dict(base); c12["exit1_on"] = False; c12["min_consec"] = 4; c12["use_atr_sl"] = True; c12["atr_sl_mult"] = 2.0
    c12["rsi_short_lo"]=15; c12["rsi_short_hi"]=60; c12["rsi_long_lo"]=40; c12["rsi_long_hi"]=85
    show("Exit1OFF+MC4+ATR2x+RSIwide", c12)

    # 10. Structure filter on/off
    c13 = dict(base); c13["structure_on"] = True
    show("StructureFilter ON", c13)
    c14 = dict(base); c14["structure_on"] = True; c14["min_consec"] = 4
    show("Structure ON+MC4", c14)
    c15 = dict(base); c15["structure_on"] = True; c15["min_consec"] = 6
    show("Structure ON+MC6", c15)
    c16 = dict(base); c16["structure_on"] = True; c16["exit1_on"] = False
    show("Structure ON+Exit1OFF", c16)
    c17 = dict(base); c17["structure_on"] = True; c17["exit1_on"] = False; c17["min_consec"] = 4; c17["use_atr_sl"] = True; c17["atr_sl_mult"] = 2.0
    show("Structure ON+Exit1OFF+MC4+ATR2x", c17)

    # 11. RSI band mode
    c18 = dict(base); c18["rsi_band_mode"] = True
    show("RSI Band Mode", c18)
    c19 = dict(base); c19["rsi_band_mode"] = True; c19["rsi_band_mid"] = 50; c19["rsi_band_hi"] = 75; c19["rsi_band_lo"] = 25
    c19["rsi_band_margin"] = 3.0; c19["rsi_band_min_hold"] = 12
    show("RSI Band EURUSD params", c19)
    c20 = dict(base); c20["rsi_band_mode"] = True; c20["rsi_band_require_trend"] = False
    show("RSI Band NoTrend", c20)

    # Summary: best by PF
    valid = [(n, r) for n, r in results if r is not None]
    valid.sort(key=lambda x: x[1]["pf"], reverse=True)
    print("\n=== Top 5 by Profit Factor ===")
    for name, r in valid[:5]:
        print(f"  {name:38s} PF={r['pf']:.2f} CAGR={r['cagr']:.2f}% Sharpe={r['sharpe']:.2f} MaxDD={r['max_dd']:.2f}% WR={r['wr']:.1f}% n={r['n_trades']}")


if __name__ == "__main__":
    main()
