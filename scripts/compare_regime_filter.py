#!/usr/bin/env python3
"""
Compare unfiltered vs regime-filtered compression rubberband.
Checks whether regime filters are cutting good trades or only bad ones.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "data" / "mt5"
BARS_PER_YEAR_H1 = 24 * 252


def load_csv(symbol: str, tf: str = "H1") -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_{tf}.csv"
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce").dt.tz_convert(None)
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    return df


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = np.maximum(high - low, np.maximum(abs(high - prev_close), abs(low - prev_close)))

    tr_s = pd.Series(tr, index=df.index).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_s
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr_s

    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def metrics(returns: pd.Series, bars_per_year: int = BARS_PER_YEAR_H1) -> dict:
    r = returns.dropna()
    if r.empty or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0, "pf": 0.0, "winrate": 0.0, "n": 0}
    eq = (1 + r).cumprod()
    years = max(len(r) / bars_per_year, 1e-6)
    cagr = eq.iloc[-1] ** (1 / years) - 1
    sharpe = r.mean() / r.std() * math.sqrt(bars_per_year)
    peak = np.maximum.accumulate(eq)
    maxdd = (eq / peak - 1).min()
    wins = r[r > 0]
    losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(maxdd * 100, 2),
        "pf": round(pf, 2) if pd.notna(pf) else None,
        "winrate": round((r > 0).mean() * 100, 1),
        "n": len(r),
    }


def backtest(df: pd.DataFrame, symbol: str,
             use_filters: bool,
             compress_atr: float = 0.5,
             vol_pctile_min: float = 0.33,
             adx_min: int = 20,
             session_cutoff: int = 21,
             max_spread: int = 12,
             atr_ratio_min: float = 0.70,
             ext_atr: float = 2.0,
             stop_atr: float = 1.5,
             tp_atr: float = 2.0,
             max_hold_bars: int = 24,
             comm_pts: float = 7.0,
             slip_pts: float = 1.0,
             floor_pts: float = 1.0) -> dict:
    """Backtest compression rubberband with optional regime filters."""
    df = df.copy()
    df["ema8"] = ema(df["close"], 8)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["atr14"] = wilder_atr(df, 14)
    df["adx14"] = adx(df, 14)

    n = len(df)
    point = 0.001 if symbol.endswith("JPY") else 0.00001
    tick_size = point

    spread_eff = np.maximum(df["spread"].to_numpy(float), floor_pts)
    cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
    cost_side = cost_side_pts * point

    atr = df["atr14"].to_numpy(float)
    atr_pct = pd.Series(atr).rolling(200, min_periods=20).rank(pct=True).to_numpy(float)
    atr_avg20 = pd.Series(atr).rolling(20, min_periods=5).mean().to_numpy(float)

    equity = 10000.0
    equity_curve = [equity]
    returns_arr = np.zeros(n)
    trades = []
    filtered_out = []  # signals that were filtered out - track what they would have made

    in_position = False
    side = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_atr = 0.0
    bars_held = 0
    entry_bar = 0

    for i in range(60, n):
        ema8 = df["ema8"].iloc[i]
        ema21 = df["ema21"].iloc[i]
        ema50 = df["ema50"].iloc[i]
        atr_val = df["atr14"].iloc[i]
        adx_val = df["adx14"].iloc[i]
        close = df["close"].iloc[i]
        spread = int(df["spread"].iloc[i])
        hour = df["time"].iloc[i].hour

        ma_center = (ema8 + ema21 + ema50) / 3.0

        # Regime filters
        ma_spread = max(abs(ema8 - ema21), abs(ema21 - ema50), abs(ema8 - ema50))
        compressed = ma_spread < compress_atr * atr_val if atr_val > 0 else True
        pct = atr_pct[i] if not np.isnan(atr_pct[i]) else 1.0
        low_vol = pct < vol_pctile_min
        range_mkt = adx_val > 0 and adx_val < adx_min
        late_session = hour >= session_cutoff
        wide_spread = spread > max_spread
        atr_avg = atr_avg20[i] if not np.isnan(atr_avg20[i]) else atr_val
        atr_ratio = atr_val / atr_avg if atr_avg > 0 else 1.0
        squeeze = atr_ratio < atr_ratio_min

        filtered = False
        if use_filters:
            filtered = compressed or low_vol or range_mkt or late_session or wide_spread or squeeze

        # Track what filtered-out signals would have done (look ahead to see if profitable)
        ext_dist = ext_atr * atr_val if atr_val > 0 else 0
        signal_side = 0
        if close < ma_center - ext_dist:
            signal_side = 1
        elif close > ma_center + ext_dist:
            signal_side = -1

        if signal_side != 0 and filtered:
            # Record this filtered signal - check next 24 bars for max favorable excursion
            mfe_long = 0.0
            mfe_short = 0.0
            for j in range(i + 1, min(i + max_hold_bars + 1, n)):
                c2 = df["close"].iloc[j]
                if signal_side == 1:
                    mfe_long = max(mfe_long, c2 - close)
                else:
                    mfe_short = max(mfe_short, close - c2)
            filtered_out.append({
                "bar": i, "side": signal_side, "entry": close,
                "mfe": mfe_long if signal_side == 1 else mfe_short,
                "reasons": []
            })
            if compressed:
                filtered_out[-1]["reasons"].append("compression")
            if low_vol:
                filtered_out[-1]["reasons"].append("low_vol")
            if range_mkt:
                filtered_out[-1]["reasons"].append("range")
            if late_session:
                filtered_out[-1]["reasons"].append("late_session")
            if wide_spread:
                filtered_out[-1]["reasons"].append("wide_spread")
            if squeeze:
                filtered_out[-1]["reasons"].append("squeeze")

        # Manage open position
        if in_position:
            hit = False
            if side > 0:
                if close >= ma_center:
                    pnl = (ma_center - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "snap-back", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
                elif close <= stop_price and not hit:
                    pnl = (stop_price - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
                elif close >= tp_price and not hit:
                    pnl = (tp_price - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
                elif bars_held >= max_hold_bars and not hit:
                    pnl = (close - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "max-hold", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
            else:
                if close <= ma_center:
                    pnl = (entry_price - ma_center) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "snap-back", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
                elif close >= stop_price and not hit:
                    pnl = (entry_price - stop_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
                elif close <= tp_price and not hit:
                    pnl = (entry_price - tp_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True
                elif bars_held >= max_hold_bars and not hit:
                    pnl = (entry_price - close) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "max-hold", "entry_bar": entry_bar})
                    in_position = False
                    side = 0
                    hit = True

        if not in_position and signal_side != 0 and not filtered:
            entry_price = close
            entry_atr = atr_val if atr_val > 0 else 0.001
            stop_dist = entry_atr * stop_atr
            if signal_side > 0:
                stop_price = entry_price - stop_dist
                tp_price = entry_price + stop_dist * tp_atr
            else:
                stop_price = entry_price + stop_dist
                tp_price = entry_price - stop_dist * tp_atr
            in_position = True
            side = signal_side
            bars_held = 0
            entry_bar = i
            equity -= cost_side[i] / tick_size * 0.01

        if in_position:
            bars_held += 1

        if in_position and side != 0:
            if side > 0:
                bar_ret = (close - entry_price) / tick_size * 0.01 / equity
            else:
                bar_ret = (entry_price - close) / tick_size * 0.01 / equity
            returns_arr[i] = bar_ret

        equity_curve.append(equity)

    if in_position:
        close = df["close"].iloc[-1]
        if side > 0:
            pnl = (close - entry_price) / tick_size * 0.01
        else:
            pnl = (entry_price - close) / tick_size * 0.01
        equity += pnl - cost_side[-1] / tick_size * 0.01
        trades.append({"side": "long" if side > 0 else "short",
                       "pnl": pnl - cost_side[-1] / tick_size * 0.01,
                       "bars": bars_held, "exit": "end", "entry_bar": entry_bar})

    rets = pd.Series(returns_arr[60:], index=df["time"].iloc[60:])
    m = metrics(rets)
    m["equity_start"] = 10000.0
    m["equity_end"] = round(float(equity_curve[-1]), 2)
    m["total_return_pct"] = round((equity_curve[-1] - equity_curve[0]) / equity_curve[0] * 100, 2)
    m["n_trades"] = len(trades)
    m["filtered_out"] = filtered_out
    return m, trades, equity_curve


def analyze_filtered_out(filtered_out: list) -> dict:
    """Of the signals filtered out, how many would have been profitable?"""
    if not filtered_out:
        return {"n": 0, "profitable_pct": 0.0, "avg_mfe": 0.0, "by_reason": {}}
    profitable = sum(1 for t in filtered_out if t["mfe"] > 0)
    by_reason = {}
    for t in filtered_out:
        for r in t["reasons"]:
            if r not in by_reason:
                by_reason[r] = {"n": 0, "profitable": 0}
            by_reason[r]["n"] += 1
            if t["mfe"] > 0:
                by_reason[r]["profitable"] += 1
    return {
        "n": len(filtered_out),
        "profitable_pct": round(profitable / len(filtered_out) * 100, 1),
        "avg_mfe": round(sum(t["mfe"] for t in filtered_out) / len(filtered_out), 5),
        "by_reason": by_reason,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=["EURUSD", "GBPUSD", "USDJPY", "all"], default="EURUSD")
    ap.add_argument("--tf", choices=["H1"], default="H1")
    args = ap.parse_args()

    symbols = ["EURUSD", "GBPUSD", "USDJPY"] if args.symbol == "all" else [args.symbol]

    for sym in symbols:
        df = load_csv(sym, args.tf)
        print(f"\n{'='*70}")
        print(f"{sym} {args.tf} — Unfiltered vs Regime-Filtered")
        print(f"{'='*70}")
        print(f"  bars: {len(df)}  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")

        m_unf, _, _ = backtest(df, sym, use_filters=False)
        m_filt, _, _ = backtest(df, sym, use_filters=True)

        print(f"\n  UNFILTERED (no regime gates):")
        print(f"    Trades: {m_unf['n_trades']}  PF: {m_unf['pf']}  CAGR: {m_unf['cagr']:+.2f}%  "
              f"Sharpe: {m_unf['sharpe']}  MaxDD: {m_unf['maxdd']:.2f}%  Total: {m_unf['total_return_pct']:+.2f}%")
        print(f"    Win rate: {m_unf['winrate']}%")

        print(f"\n  REGIME-FILTERED:")
        print(f"    Trades: {m_filt['n_trades']}  PF: {m_filt['pf']}  CAGR: {m_filt['cagr']:+.2f}%  "
              f"Sharpe: {m_filt['sharpe']}  MaxDD: {m_filt['maxdd']:.2f}%  Total: {m_filt['total_return_pct']:+.2f}%")
        print(f"    Win rate: {m_filt['winrate']}%")

        # Analyze what was filtered out
        # Re-run filtered to get filtered_out list
        m_filt2, _, _ = backtest(df, sym, use_filters=True)
        fo = analyze_filtered_out(m_filt2["filtered_out"])
        print(f"\n  FILTERED OUT SIGNALS: {fo['n']} total")
        print(f"    Of those, {fo['profitable_pct']}% would have been profitable (MFE > 0)")
        print(f"    Avg MFE of filtered: {fo['avg_mfe']}")
        print(f"    By filter reason:")
        for reason, stats in fo["by_reason"].items():
            prof_pct = round(stats["profitable"] / stats["n"] * 100, 1) if stats["n"] > 0 else 0
            print(f"      {reason:<18} n={stats['n']:4d}  profitable={prof_pct:>5}%")

        # Trade reduction
        reduction = (1 - m_filt["n_trades"] / m_unf["n_trades"]) * 100 if m_unf["n_trades"] > 0 else 0
        print(f"\n  Trade reduction: {reduction:.1f}% fewer trades")

        # CAGR comparison per trade
        unf_per_trade = m_unf["total_return_pct"] / m_unf["n_trades"] if m_unf["n_trades"] > 0 else 0
        filt_per_trade = m_filt["total_return_pct"] / m_filt["n_trades"] if m_filt["n_trades"] > 0 else 0
        print(f"    Unfiltered return/trade: {unf_per_trade:+.4f}%")
        print(f"    Filtered return/trade:   {filt_per_trade:+.4f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
