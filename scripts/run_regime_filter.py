#!/usr/bin/env python3
"""
Python backtest for SovereignRegimeFilter (MQL5: EA/Include/SovereignRegimeFilter.mqh).
Replicates the MQL5 logic exactly on real MT5 H1 data.

Strategy: Regime-filtered MA compression rubberband.
  - Skip bad regimes: MA compression, low vol, range, late session, wide spread, ATR squeeze
  - Trade only in good regimes: fade price extensions from MA center during expansion
  - LONG: close < MA_center - extAtr*ATR  |  SHORT: close > MA_center + extAtr*ATR
  - Exit: price crosses back through MA_center | Stop: 1.5 ATR | TP: 2.0 ATR | max 24 bars

Run:
    .\.venv\Scripts\python.exe scripts/run_regime_filter.py --symbol EURUSD --tf H1
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "data" / "mt5"
REPORT_DIR = ROOT / "backtest" / "reports"

BARS_PER_YEAR_H1 = 24 * 252


def load_csv(symbol: str, tf: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
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


def wilder_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


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


def backtest_regime_filter(df: pd.DataFrame, symbol: str,
                           compress_atr: float = 0.5,
                           vol_pctile_min: float = 0.33,
                           adx_min: int = 20,
                           session_cutoff: int = 21,
                           max_spread: int = 12,
                           atr_ratio_min: float = 0.70,
                           ext_atr: float = 2.0,
                           stop_atr: float = 1.5,
                           tp_atr: float = 2.0,
                           risk_pct: float = 0.5,
                           max_hold_bars: int = 24,
                           equity_start: float = 10000.0,
                           comm_pts: float = 7.0,
                           slip_pts: float = 1.0,
                           floor_pts: float = 1.0) -> dict:
    """Replicate SovereignRegimeFilter.mqh logic exactly."""
    df = df.copy()
    df["ema8"] = ema(df["close"], 8)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["atr14"] = wilder_atr(df, 14)
    df["adx14"] = adx(df, 14)

    n = len(df)
    point = 0.001 if symbol.endswith("JPY") else 0.00001
    tick_size = point

    # Cost per side in dollars (same conversion as MQL5 tick value)
    spread_eff = np.maximum(df["spread"].to_numpy(float), floor_pts)
    cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
    cost_side = cost_side_pts * point

    # ATR rolling history for percentile and ratio
    atr = df["atr14"].to_numpy(float)
    atr_pct = pd.Series(atr).rolling(200, min_periods=20).rank(pct=True).to_numpy(float)
    atr_avg20 = pd.Series(atr).rolling(20, min_periods=5).mean().to_numpy(float)

    equity = equity_start
    equity_curve = [equity]
    returns_arr = np.zeros(n)
    trades = []

    in_position = False
    side = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_atr = 0.0
    bars_held = 0

    for i in range(60, n):  # need warmup for indicators
        ema8 = df["ema8"].iloc[i]
        ema21 = df["ema21"].iloc[i]
        ema50 = df["ema50"].iloc[i]
        atr_val = df["atr14"].iloc[i]
        adx_val = df["adx14"].iloc[i]
        close = df["close"].iloc[i]
        spread = int(df["spread"].iloc[i])
        hour = df["time"].iloc[i].hour

        # --- Regime filters ---
        # 1. MA compression
        ma_spread = max(abs(ema8 - ema21), abs(ema21 - ema50), abs(ema8 - ema50))
        compressed = ma_spread < compress_atr * atr_val if atr_val > 0 else True

        # 2. Low vol (ATR percentile)
        pct = atr_pct[i] if not np.isnan(atr_pct[i]) else 1.0
        low_vol = pct < vol_pctile_min

        # 3. Range (ADX)
        range_mkt = adx_val > 0 and adx_val < adx_min

        # 4. Late session
        late_session = hour >= session_cutoff

        # 5. Wide spread
        wide_spread = spread > max_spread

        # 6. ATR squeeze
        atr_avg = atr_avg20[i] if not np.isnan(atr_avg20[i]) else atr_val
        atr_ratio = atr_val / atr_avg if atr_avg > 0 else 1.0
        squeeze = atr_ratio < atr_ratio_min

        filtered = compressed or low_vol or range_mkt or late_session or wide_spread or squeeze

        # --- MA center ---
        ma_center = (ema8 + ema21 + ema50) / 3.0

        # --- Manage open position ---
        if in_position:
            hit = False
            if side > 0:  # long
                # snap-back to MA center
                if close >= ma_center:
                    pnl = (ma_center - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "snap-back"})
                    in_position = False
                    side = 0
                    hit = True
                # stop / TP
                elif close <= stop_price and not hit:
                    pnl = (stop_price - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp"})
                    in_position = False
                    side = 0
                    hit = True
                elif close >= tp_price and not hit:
                    pnl = (tp_price - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp"})
                    in_position = False
                    side = 0
                    hit = True
                elif bars_held >= max_hold_bars and not hit:
                    pnl = (close - entry_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "long", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "max-hold"})
                    in_position = False
                    side = 0
                    hit = True
            else:  # short
                if close <= ma_center:
                    pnl = (entry_price - ma_center) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "snap-back"})
                    in_position = False
                    side = 0
                    hit = True
                elif close >= stop_price and not hit:
                    pnl = (entry_price - stop_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp"})
                    in_position = False
                    side = 0
                    hit = True
                elif close <= tp_price and not hit:
                    pnl = (entry_price - tp_price) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "stop/tp"})
                    in_position = False
                    side = 0
                    hit = True
                elif bars_held >= max_hold_bars and not hit:
                    pnl = (entry_price - close) / tick_size * 0.01
                    equity += pnl - cost_side[i] / tick_size * 0.01
                    trades.append({"side": "short", "pnl": pnl - cost_side[i] / tick_size * 0.01,
                                   "bars": bars_held, "exit": "max-hold"})
                    in_position = False
                    side = 0
                    hit = True

        # --- Entry when flat and not filtered ---
        if not in_position and not filtered:
            ext_dist = ext_atr * atr_val if atr_val > 0 else 0
            if close < ma_center - ext_dist:
                entry_price = close
                entry_atr = atr_val if atr_val > 0 else 0.001
                stop_dist = entry_atr * stop_atr
                stop_price = entry_price - stop_dist
                tp_price = entry_price + stop_dist * tp_atr
                in_position = True
                side = 1
                bars_held = 0
                equity -= cost_side[i] / tick_size * 0.01
            elif close > ma_center + ext_dist:
                entry_price = close
                entry_atr = atr_val if atr_val > 0 else 0.001
                stop_dist = entry_atr * stop_atr
                stop_price = entry_price + stop_dist
                tp_price = entry_price - stop_dist * tp_atr
                in_position = True
                side = -1
                bars_held = 0
                equity -= cost_side[i] / tick_size * 0.01

        if in_position:
            bars_held += 1

        # bar return (mark-to-market)
        if in_position and side != 0:
            if side > 0:
                bar_ret = (close - entry_price) / tick_size * 0.01 / equity
            else:
                bar_ret = (entry_price - close) / tick_size * 0.01 / equity
            returns_arr[i] = bar_ret

        equity_curve.append(equity)

    # close remaining position
    if in_position:
        close = df["close"].iloc[-1]
        if side > 0:
            pnl = (close - entry_price) / tick_size * 0.01
        else:
            pnl = (entry_price - close) / tick_size * 0.01
        equity += pnl - cost_side[-1] / tick_size * 0.01
        trades.append({"side": "long" if side > 0 else "short",
                       "pnl": pnl - cost_side[-1] / tick_size * 0.01,
                       "bars": bars_held, "exit": "end"})

    rets = pd.Series(returns_arr[60:], index=df["time"].iloc[60:])
    m = metrics(rets)
    m["equity_start"] = equity_start
    m["equity_end"] = round(float(equity_curve[-1]), 2)
    m["total_return_pct"] = round((equity_curve[-1] - equity_curve[0]) / equity_curve[0] * 100, 2)
    m["n_trades"] = len(trades)
    m["gross_profit"] = round(sum(t["pnl"] for t in trades if t["pnl"] > 0), 2)
    m["gross_loss"] = round(abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)), 2)
    return m, trades, equity_curve


def main() -> int:
    ap = argparse.ArgumentParser(description="Regime Filter backtest on MT5 H1 data")
    ap.add_argument("--symbol", choices=["EURUSD", "GBPUSD", "USDJPY", "all"], default="all")
    ap.add_argument("--tf", choices=["H1"], default="H1")
    ap.add_argument("--from", dest="from_date", default="2018-01-01")
    ap.add_argument("--to", dest="to_date", default="2026-12-31")
    ap.add_argument("--compress-atr", type=float, default=0.5)
    ap.add_argument("--vol-pctile", type=float, default=0.33)
    ap.add_argument("--adx-min", type=int, default=20)
    ap.add_argument("--session-cut", type=int, default=21)
    ap.add_argument("--max-spread", type=int, default=12)
    ap.add_argument("--atr-ratio", type=float, default=0.70)
    ap.add_argument("--ext-atr", type=float, default=2.0)
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--tp-atr", type=float, default=2.0)
    ap.add_argument("--risk-pct", type=float, default=0.5)
    ap.add_argument("--max-hold", type=int, default=24)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    args = ap.parse_args()

    symbols = ["EURUSD", "GBPUSD", "USDJPY"] if args.symbol == "all" else [args.symbol]

    for sym in symbols:
        df = load_csv(sym, args.tf)
        df = df[(df["time"] >= args.from_date) & (df["time"] <= args.to_date)]
        if len(df) == 0:
            print(f"No data for {sym} in {args.from_date} to {args.to_date}")
            continue

        m, trades, eq_curve = backtest_regime_filter(
            df, sym,
            compress_atr=args.compress_atr,
            vol_pctile_min=args.vol_pctile,
            adx_min=args.adx_min,
            session_cutoff=args.session_cut,
            max_spread=args.max_spread,
            atr_ratio_min=args.atr_ratio,
            ext_atr=args.ext_atr,
            stop_atr=args.stop_atr,
            tp_atr=args.tp_atr,
            risk_pct=args.risk_pct,
            max_hold_bars=args.max_hold,
            comm_pts=args.comm_pts,
            slip_pts=args.slip_pts,
            floor_pts=args.floor_pts,
        )

        print(f"\n=== {sym} {args.tf} Regime Filter ===")
        print(f"  bars: {len(df)}  {df['time'].iloc[0]} -> {df['time'].iloc[-1]}")
        print(f"  equity: {m['equity_start']} -> {m['equity_end']}  total: {m['total_return_pct']:+.2f}%")
        print(f"  CAGR: {m['cagr']:+.2f}%  Sharpe: {m['sharpe']}  maxDD: {m['maxdd']:.2f}%")
        print(f"  PF: {m['pf']}  Win rate: {m['winrate']}%  Trades: {m['n_trades']}")
        print(f"  Gross profit: {m['gross_profit']:+.2f}  Gross loss: {m['gross_loss']:+.2f}")

        # Daily / monthly reporting
        # loop starts at bar 60, so equity_curve[1:] aligns with df["time"].iloc[60:]
        eq_df = pd.DataFrame({"equity": eq_curve[1:]}, index=df["time"].iloc[60:])
        daily_eq = eq_df.resample("D").last().ffill()
        daily_ret = daily_eq["equity"].pct_change().dropna()
        monthly_eq = eq_df.resample("ME").last()
        monthly_ret = monthly_eq["equity"].pct_change().dropna()

        print(f"\n  === DAILY (last 30 days) ===")
        for d, r in daily_ret.tail(30).items():
            print(f"    {d.strftime('%Y-%m-%d')}: {r*100:+.2f}%")
        print(f"\n  === MONTHLY (last 12 months) ===")
        for d, r in monthly_ret.tail(12).items():
            print(f"    {d.strftime('%Y-%m')}: {r*100:+.2f}%")

        # Save CSVs
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        daily_csv = REPORT_DIR / f"daily_returns_regime_filter_{sym}_H1.csv"
        monthly_csv = REPORT_DIR / f"monthly_returns_regime_filter_{sym}_H1.csv"
        daily_ret.to_csv(daily_csv, header=["daily_return"])
        monthly_ret.to_csv(monthly_csv, header=["monthly_return"])
        print(f"\n  Daily CSV: {daily_csv}")
        print(f"  Monthly CSV: {monthly_csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
