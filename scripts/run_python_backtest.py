#!/usr/bin/env python3
"""
Project Sovereign - Python backtest on real MT5 data.
Replicates SovereignRSIBand.mqh and SovereignStructRide.mqh logic exactly.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "data" / "mt5"
REPORT_DIR = ROOT / "backtest" / "reports"


def load_csv(symbol: str, tf: str) -> pd.DataFrame:
    path = DATA_DIR / f"{symbol}_{tf}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)
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
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_spread_p90_gate(spreads: list, pct: int = 90) -> list:
    """Trailing p90 spread gate for each bar."""
    result = []
    buf = []
    for s in spreads:
        buf.append(s)
        if len(buf) > 500:
            buf = buf[-500:]
        if len(buf) < 20:
            result.append(True)  # not enough data, allow
        else:
            s_sorted = sorted(buf)
            idx = int(math.floor((len(s_sorted) - 1) * pct / 100.0))
            p90 = s_sorted[idx]
            result.append(s <= p90)
    return result


def backtest_rsi_band(df: pd.DataFrame,
                       symbol: str,
                       risk_pct: float = 0.5,
                       min_hold_bars: int = 12,
                       margin: float = 3.0,
                       spread_gate_pct: int = 90,
                       stop_atr: float = 1.5,
                       tp_atr: float = 2.0,
                       equity_start: float = 10000.0,
                       comm_pts: float = 7.0,
                       slip_pts: float = 1.0,
                       floor_pts: float = 1.0) -> dict:
    """
    Replicate SovereignRSIBand.mqh logic exactly (corrected to match research/rsi_band_net.py).
    
    Entry: 
      LONG: trend > 0 AND RSI_prev <= 50 AND RSI > 50 + margin
      SHORT: trend < 0 AND RSI_prev >= 50 AND RSI < 50 - margin
    
    Exit:
      LONG: trend < 0 (slope flip, immediate) OR RSI > 75 (after min-hold) OR stop/TP
      SHORT: trend > 0 (slope flip, immediate) OR RSI < 25 (after min-hold) OR stop/TP
    """
    df = df.copy()
    df["ema21"] = ema(df["close"], 21)
    df["atr14"] = wilder_atr(df, 14)
    df["rsi9"] = wilder_rsi(df["close"], 9)
    df = df.dropna(subset=["ema21", "atr14", "rsi9"]).reset_index(drop=True)

    # EMA21 slope over 3 bars / ATR deadband
    df["ema_slope"] = (df["ema21"] - df["ema21"].shift(3)) / df["atr14"]

    # Spread gate
    spread_col = df["spread"].fillna(df["spread"].median())
    spread_ok = compute_spread_p90_gate(spread_col.tolist(), spread_gate_pct)
    df["spread_ok"] = spread_ok

    # Cost per side in price units
    point = 0.001 if symbol.endswith("JPY") else 0.00001
    spread_eff = np.maximum(df["spread"].to_numpy(float), floor_pts)
    cost_side_pts = spread_eff / 2.0 + slip_pts + comm_pts / 2.0
    cost_side = cost_side_pts * point

    tick_size = 0.001 if symbol.endswith("JPY") else 0.00001

    equity = equity_start
    equity_curve = [equity]
    trades = []

    in_position = False
    side = 0
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    entry_atr = 0.0
    bars_held = 0
    entry_bar_idx = 0

    for i in range(4, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        atr_val = row["atr14"]
        rsi_val = row["rsi9"]
        rsi_prev = prev["rsi9"]
        close = row["close"]
        ema_slope = row["ema_slope"]
        spread_ok_val = df["spread_ok"].iloc[i]

        # Determine trend regime
        if ema_slope > 0.10:
            trend = 1
        elif ema_slope < -0.10:
            trend = -1
        else:
            trend = 0

        # Update bars held
        if in_position:
            bars_held += 1

        # --- EXIT LOGIC ---
        if in_position:
            hit = False
            if side > 0:  # long
                # Slope flip down: immediate exit
                if trend < 0:
                    exit_px = close
                    pnl = (exit_px - entry_price) / tick_size * 0.01  # 0.01 lot
                    equity += pnl
                    trades.append({"side": "long", "pnl": pnl, "bars": bars_held, "exit": "slope_flip"})
                    in_position = False
                    side = 0
                    hit = True
                # RSI crosses above 75 (after min-hold)
                if bars_held >= min_hold_bars and rsi_prev <= 75.0 and rsi_val > 75.0 and not hit:
                    exit_px = close
                    pnl = (exit_px - entry_price) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "long", "pnl": pnl, "bars": bars_held, "exit": "rsi_high"})
                    in_position = False
                    side = 0
                    hit = True
                # Hard stop / TP
                if close <= stop_price and not hit:
                    pnl = (stop_price - entry_price) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "long", "pnl": pnl, "bars": bars_held, "exit": "stop"})
                    in_position = False
                    side = 0
                    hit = True
                elif close >= tp_price and not hit:
                    pnl = (tp_price - entry_price) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "long", "pnl": pnl, "bars": bars_held, "exit": "tp"})
                    in_position = False
                    side = 0
                    hit = True
            else:  # short
                # Slope flip up: immediate exit
                if trend > 0:
                    exit_px = close
                    pnl = (entry_price - exit_px) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "short", "pnl": pnl, "bars": bars_held, "exit": "slope_flip"})
                    in_position = False
                    side = 0
                    hit = True
                # RSI crosses below 25 (after min-hold)
                if bars_held >= min_hold_bars and rsi_prev >= 25.0 and rsi_val < 25.0 and not hit:
                    exit_px = close
                    pnl = (entry_price - exit_px) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "short", "pnl": pnl, "bars": bars_held, "exit": "rsi_low"})
                    in_position = False
                    side = 0
                    hit = True
                # Hard stop / TP
                if close >= stop_price and not hit:
                    pnl = (entry_price - stop_price) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "short", "pnl": pnl, "bars": bars_held, "exit": "stop"})
                    in_position = False
                    side = 0
                    hit = True
                elif close <= tp_price and not hit:
                    pnl = (entry_price - tp_price) / tick_size * 0.01
                    equity += pnl
                    trades.append({"side": "short", "pnl": pnl, "bars": bars_held, "exit": "tp"})
                    in_position = False
                    side = 0
                    hit = True

        equity_curve.append(equity)

        # --- ENTRY LOGIC (only when flat) ---
        if not in_position:
            enter = False
            enter_side = 0

            if trend > 0:  # uptrend: RSI crosses up through 50 + margin
                if rsi_prev <= 50.0 and rsi_val > (50.0 + margin) and spread_ok_val:
                    enter = True
                    enter_side = 1
            elif trend < 0:  # downtrend: RSI crosses down through 50 - margin
                if rsi_prev >= 50.0 and rsi_val < (50.0 - margin) and spread_ok_val:
                    enter = True
                    enter_side = -1

            if enter:
                entry_price = close
                entry_atr = atr_val if atr_val > 0 else 0.001
                stop_dist = entry_atr * stop_atr
                if enter_side > 0:
                    stop_price = entry_price - stop_dist
                    tp_price = entry_price + stop_dist * tp_atr
                else:
                    stop_price = entry_price + stop_dist
                    tp_price = entry_price - stop_dist * tp_atr
                in_position = True
                side = enter_side
                bars_held = 0
                entry_bar_idx = i

                # Deduct entry cost (half spread + commission/slippage)
                # cost_side is in price units; convert to dollars same way as P&L
                entry_cost_dollars = cost_side[i] / tick_size * 0.01
                equity -= entry_cost_dollars

    # Deduct exit costs for any remaining open position
    if in_position:
        exit_px = df["close"].iloc[-1]
        if side > 0:
            pnl = (exit_px - entry_price) / tick_size * 0.01
        else:
            pnl = (entry_price - exit_px) / tick_size * 0.01
        exit_cost_dollars = cost_side[-1] / tick_size * 0.01
        equity += pnl - exit_cost_dollars
        trades.append({"side": "long" if side > 0 else "short", "pnl": pnl - exit_cost_dollars, "bars": bars_held, "exit": "end_of_data"})
        in_position = False

    # Compute metrics
    equity_arr = np.array(equity_curve)
    total_return = (equity_arr[-1] - equity_arr[0]) / equity_arr[0]
    peak = np.maximum.accumulate(equity_arr)
    drawdown = (equity_arr / peak - 1).min()
    max_dd = abs(drawdown) * 100

    winning = [t for t in trades if t["pnl"] > 0]
    losing = [t for t in trades if t["pnl"] < 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0

    gross_profit = sum(t["pnl"] for t in winning)
    gross_loss = abs(sum(t["pnl"] for t in losing))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Simple returns for Sharpe
    bar_returns = np.diff(equity_arr) / equity_arr[:-1]
    bar_returns = bar_returns[bar_returns != 0]
    bars_per_year = 24 * 252  # H1
    sharpe = (np.mean(bar_returns) / np.std(bar_returns) * math.sqrt(bars_per_year)) if len(bar_returns) > 0 and np.std(bar_returns) > 0 else 0

    n_years = (df["time"].iloc[-1] - df["time"].iloc[0]).days / 365.25
    cagr = (equity_arr[-1] / equity_arr[0]) ** (1 / n_years) - 1 if n_years > 0 else 0

    # === Daily equity curve (calendar days) ===
    # equity_curve[0] is initial equity (no bar), equity_curve[1:] aligns with bars from index 4
    eq_series = pd.Series(equity_arr[1:], index=df["time"].iloc[4:])
    eq_df = eq_series.to_frame("equity")
    daily_eq = eq_df.resample("D").last().ffill()
    daily_ret = daily_eq["equity"].pct_change().dropna()

    # === Monthly aggregation ===
    monthly_eq = eq_df.resample("ME").last()
    monthly_ret = monthly_eq["equity"].pct_change().dropna()

    # Last 12 months
    last_12m = monthly_ret.tail(12)

    # Save daily and monthly CSVs
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    daily_csv = REPORT_DIR / f"daily_returns_rsi_band_{symbol}_H1.csv"
    monthly_csv = REPORT_DIR / f"monthly_returns_rsi_band_{symbol}_H1.csv"
    daily_ret.to_csv(daily_csv, header=["daily_return"])
    monthly_ret.to_csv(monthly_csv, header=["monthly_return"])

    return {
        "strategy": "RSI Band",
        "symbol": symbol,
        "equity_start": equity_start,
        "equity_end": round(float(equity_arr[-1]), 2),
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "inf",
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        # Daily stats
        "daily_mean_pct": round(float(daily_ret.mean()) * 100, 4),
        "daily_std_pct": round(float(daily_ret.std()) * 100, 4),
        "daily_sharpe": round(float(daily_ret.mean() / daily_ret.std() * math.sqrt(252)) if daily_ret.std() > 0 else 0, 2),
        "daily_win_days": int((daily_ret > 0).sum()),
        "daily_total_days": int(len(daily_ret)),
        "daily_win_pct": round(float((daily_ret > 0).mean()) * 100, 2),
        # Monthly stats
        "monthly_mean_pct": round(float(monthly_ret.mean()) * 100, 4),
        "monthly_std_pct": round(float(monthly_ret.std()) * 100, 4),
        "monthly_sharpe": round(float(monthly_ret.mean() / monthly_ret.std() * math.sqrt(12)) if monthly_ret.std() > 0 else 0, 2),
        "monthly_win_months": int((monthly_ret > 0).sum()),
        "monthly_total_months": int(len(monthly_ret)),
        "monthly_win_pct": round(float((monthly_ret > 0).mean()) * 100, 2),
        # Last 12 months detail
        "last_12m_returns": [round(float(x) * 100, 2) for x in last_12m.values],
        "last_12m_total_pct": round(float((1 + last_12m).prod() - 1) * 100, 2),
        "daily_csv": str(daily_csv),
        "monthly_csv": str(monthly_csv),
        "daily_ret": daily_ret,
        "monthly_ret": monthly_ret,
    }


def main():
    parser = argparse.ArgumentParser(description="Python backtest on real MT5 data")
    parser.add_argument("--strategy", choices=["rsi_band"], required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--tf", choices=["H1", "D1"], default="H1")
    parser.add_argument("--from", dest="from_date", default="2020-01-01")
    parser.add_argument("--to", dest="to_date", default="2026-01-01")
    parser.add_argument("--risk-pct", type=float, default=0.5)
    parser.add_argument("--min-hold", type=int, default=12)
    parser.add_argument("--margin", type=float, default=3.0)
    parser.add_argument("--spread-gate", type=int, default=90)
    parser.add_argument("--comm-pts", type=float, default=7.0)
    parser.add_argument("--slip-pts", type=float, default=1.0)
    parser.add_argument("--floor-pts", type=float, default=1.0)
    args = parser.parse_args()

    df = load_csv(args.symbol, args.tf)
    df = df[(df["time"] >= args.from_date) & (df["time"] <= args.to_date)]
    if len(df) == 0:
        print(f"No data for {args.symbol} {args.tf} in date range {args.from_date} to {args.to_date}")
        return 1

    result = backtest_rsi_band(df, args.symbol,
                                risk_pct=args.risk_pct,
                                min_hold_bars=args.min_hold,
                                margin=args.margin,
                                spread_gate_pct=args.spread_gate,
                                comm_pts=args.comm_pts,
                                slip_pts=args.slip_pts,
                                floor_pts=args.floor_pts)

    # Remove non-serializable Series before printing/saving
    result_for_json = {k: v for k, v in result.items() if k not in ("daily_ret", "monthly_ret")}
    print(json.dumps(result_for_json, indent=2))

    # Print daily breakdown (last 30 days)
    print(f"\n=== DAILY RETURNS (last 30 days) ===")
    last_30d = result["daily_ret"].tail(30)
    for d, r in last_30d.items():
        print(f"  {d.strftime('%Y-%m-%d')}: {r*100:+.2f}%")

    # Print monthly breakdown
    print(f"\n=== MONTHLY RETURNS (last 12 months) ===")
    for d, r in result["monthly_ret"].tail(12).items():
        print(f"  {d.strftime('%Y-%m')}: {r*100:+.2f}%")

    print(f"\nDaily stats: mean {result['daily_mean_pct']:.4f}%/day, "
          f"Sharpe {result['daily_sharpe']}, win days {result['daily_win_days']}/{result['daily_total_days']} ({result['daily_win_pct']}%)")
    print(f"Monthly stats: mean {result['monthly_mean_pct']:.4f}%/month, "
          f"Sharpe {result['monthly_sharpe']}, win months {result['monthly_win_months']}/{result['monthly_total_months']} ({result['monthly_win_pct']}%)")
    print(f"Last 12 months total: {result['last_12m_total_pct']:+.2f}%")
    print(f"\nDaily CSV: {result['daily_csv']}")
    print(f"Monthly CSV: {result['monthly_csv']}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_name = f"python_backtest_{args.strategy}_{args.symbol}_{args.tf}_{args.from_date}_{args.to_date}.json"
    report_path = REPORT_DIR / report_name
    # Remove non-serializable Series before saving
    result_copy = {k: v for k, v in result.items() if k not in ("daily_ret", "monthly_ret")}
    report_path.write_text(json.dumps(result_copy, indent=2) + "\n", encoding="utf-8")
    print(f"\nReport saved: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
