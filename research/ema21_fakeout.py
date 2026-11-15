#!/usr/bin/env python3
"""Can a fake EMA21 breakout be detected at the cross candle?

Tests whether filtering entries on the first candle that crosses EMA21 improves
expectancy vs entering every cross. Heuristics (all causal, knowable at the
cross bar's close):

Candle-level:
  * wick rejection    - long wick > k x body on cross candle (rejection)
  * weak close        - close outside the "good" third of range on cross candle
  * range contraction - cross candle range < k x ATR (no conviction)
  * marginal distance - close barely beyond EMA21 (< k x ATR)

Prior-context:
  * trend duration    - consecutive bars already beyond EMA before cross
                        (long trend = overextended = fakeout candidate)
  * RSI divergence    - price makes higher high/low low in last N bars but RSI
                        does not confirm (exhaustion)
  * swing distance    - cross is within k x ATR of a recent swing high/low
                        (likely retest, not breakout)
  * EMA slope         - EMA21 slope opposes the cross direction (counter-trend)

Entry: cross candle close. Exit: opposite cross or ATR stop/TP (via
research/backtest_core.simulate_stops). Reports design/validation/OOS splits plus
trade-level PF, winrate and expectancy per trade.

Usage
    .\\.venv\\Scripts\\python.exe research/ema21_fakeout.py --symbol EURUSD --tf H1 \
        --no-filters
    .\\.venv\\Scripts\\python.exe research/ema21_fakeout.py --symbol EURUSD --tf H1 \
        --all --wick 1.5 --close-third 0.33 --range-k 0.5 --dist-k 0.5 \
        --trend-max 20 --div-lookback 10 --swing-lookback 50 --swing-k 0.5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from backtest_core import simulate_stops, trade_metrics, validate_returns, wilder_atr  # noqa: E402
from ema_cross_harvest import EMA, RSI_P, ema, wilder_rsi  # noqa: E402
from period_returns import publish_results  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"
SPLITS = ("design", "validation", "oos")


def point_size(symbol: str) -> float:
    return 0.001 if symbol.upper().endswith("JPY") else 0.00001


def recent_swing(high, low, t, lookback):
    """Highest high / lowest low in the lookback bars strictly before t."""
    start = max(0, t - lookback)
    return high[start:t].max(), low[start:t].min()


def build_signals(df, ema_col, atr_col, rsi_col,
                  wick_k, close_third, range_k, dist_k,
                  trend_max, div_lookback, swing_lookback, swing_k,
                  use_filters):
    """Build a position signal series (1 long / -1 short / 0 flat).

    Signal is set at the cross bar (close beyond EMA21) and acted on at the
    next bar close by simulate_stops. Entry filters are all causal: they use
    only information available at the cross bar's close.
    """
    n = len(df)
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    ema = df[ema_col].to_numpy(float)
    atr = df[atr_col].to_numpy(float)
    rsi = df[rsi_col].to_numpy(float)

    pos = np.zeros(n, dtype=int)
    prev_ema = np.empty(n)
    prev_ema[0] = np.nan
    prev_ema[1:] = ema[:-1]

    body = np.abs(close - open_)
    rng = high - low
    close_pos = np.where(rng > 0, (close - low) / np.maximum(rng, 1e-12), 0.5)
    upper_wick = high - np.maximum(open_, close)
    lower_wick = np.minimum(open_, close) - low

    #--- EMA slope (per bar, causal: slope at t uses ema[t] vs ema[t-1]) ---
    ema_slope = np.zeros(n)
    ema_slope[1:] = ema[1:] - ema[:-1]

    for t in range(1, n):
        #--- bullish cross: close was at/below EMA, now above ---
        bull = close[t - 1] <= prev_ema[t] and close[t] > ema[t]
        #--- bearish cross: close was at/above EMA, now below ---
        bear = close[t - 1] >= prev_ema[t] and close[t] < ema[t]

        if not (bull or bear):
            continue

        fake = False
        if use_filters:
            b = body[t]
            a = atr[t]

            #--- candle-level filters ---
            if bull:
                if b > 0 and upper_wick[t] > wick_k * b:
                    fake = True  # long upper wick = rejection
                if close_pos[t] < close_third:
                    fake = True  # weak close in lower third
            else:
                if b > 0 and lower_wick[t] > wick_k * b:
                    fake = True  # long lower wick = rejection
                if close_pos[t] > (1.0 - close_third):
                    fake = True  # weak close in upper third

            if a > 0:
                if rng[t] < range_k * a:
                    fake = True  # range contraction = no conviction
                if bull and (close[t] - ema[t]) < dist_k * a:
                    fake = True  # marginal bullish breakout
                if bear and (ema[t] - close[t]) < dist_k * a:
                    fake = True  # marginal bearish breakout

            #--- prior-context filters ---
            if not fake and trend_max > 0:
                # count consecutive bars already beyond EMA in cross direction
                streak = 0
                i = t - 1
                while i >= 1:
                    if bull and close[i] > ema[i]:
                        streak += 1
                        i -= 1
                    elif bear and close[i] < ema[i]:
                        streak += 1
                        i -= 1
                    else:
                        break
                if streak >= trend_max:
                    fake = True  # overextended trend = fakeout candidate

            if not fake and div_lookback > 0 and not np.isnan(rsi[t]):
                # RSI divergence: price extends but RSI does not confirm
                start = max(1, t - div_lookback)
                px_hi = high[start:t + 1].max()
                px_lo = low[start:t + 1].min()
                rsi_hi = rsi[start:t + 1].max()
                rsi_lo = rsi[start:t + 1].min()
                # find bars where px hit extremes
                px_hi_bar = int(np.argmax(high[start:t + 1])) + start
                px_lo_bar = int(np.argmin(low[start:t + 1])) + start
                if bull:
                    # price made a higher high recently but RSI made lower high
                    # -> bearish divergence into a bullish cross = fakeout
                    if px_hi_bar < t and rsi[px_hi_bar] > rsi[t]:
                        fake = True
                else:
                    if px_lo_bar < t and rsi[px_lo_bar] < rsi[t]:
                        fake = True

            if not fake and swing_lookback > 0 and a > 0:
                # cross within k x ATR of recent swing = retest, not breakout
                swing_hi, swing_lo = recent_swing(high, low, t, swing_lookback)
                if bull and (swing_hi - close[t]) <= swing_k * a:
                    fake = True  # near recent swing high = likely retest
                if bear and (close[t] - swing_lo) <= swing_k * a:
                    fake = True  # near recent swing low = likely retest

            if not fake:
                # EMA slope opposes cross direction = counter-trend cross
                if bull and ema_slope[t] < 0:
                    fake = True
                if bear and ema_slope[t] > 0:
                    fake = True

        if not fake:
            pos[t] = 1 if bull else -1

    return pos


def split_mask(time, t0, edge1, edge2):
    bounds = {"design": (t0, edge1), "validation": (edge1, edge2),
              "oos": (edge2, time.iloc[-1])}
    return {sp: ((time >= a) & (time < b)).to_numpy() for sp, (a, b)
            in bounds.items()}


def evaluate(ret, pos, trades, time, bars_per_year, t0, edge1, edge2,
             label, symbol):
    """Print per-split + full metrics for one config."""
    masks = split_mask(time, t0, edge1, edge2)
    tm = trade_metrics(trades)
    print(f"=== {label} ===")
    print(f"  trades={tm['trades']}  PF={tm['pf']}  winrate={tm['winrate']}%")
    for sp in SPLITS:
        m = masks[sp]
        if m.sum() < 50:
            print(f"  {sp:<11} too few bars ({int(m.sum())})")
            continue
        eq = (1.0 + ret[m]).prod() - 1.0
        yrs = max(m.sum() / bars_per_year, 1e-9)
        cagr = (1.0 + eq) ** (1.0 / yrs) - 1.0
        peak = np.maximum.accumulate(np.concatenate(([1.0], np.cumprod(1.0 + ret[m]))))
        dd = (np.cumprod(1.0 + ret[m]) / peak[1:] - 1.0).min()
        sd = ret[m].std()
        sharpe = ret[m].mean() / sd * np.sqrt(bars_per_year) if sd > 0 else 0.0
        print(f"  {sp:<11} {eq * 100:>+7.2f}%  CAGR {cagr * 100:>+6.2f}%  "
              f"Sharpe {sharpe:>5.2f}  maxDD {dd * 100:>6.2f}%")
    eq_all = (1.0 + ret).prod() - 1.0
    print(f"  {'full':<11} {eq_all * 100:>+7.2f}%  "
          f"trades/yr {tm['trades'] / max(len(time) / bars_per_year, 1):.1f}")
    print()
    return tm


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--data-dir", default="",
                    help="override the tape directory")
    ap.add_argument("--stop-atr", type=float, default=1.5)
    ap.add_argument("--tp-atr", type=float, default=2.0)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    # candle-level fakeout filter knobs
    ap.add_argument("--wick", type=float, default=1.5,
                    help="wick/body ratio above which cross is a rejection")
    ap.add_argument("--close-third", type=float, default=0.33,
                    help="close must be outside this third of range")
    ap.add_argument("--range-k", type=float, default=0.5,
                    help="range must exceed k x ATR")
    ap.add_argument("--dist-k", type=float, default=0.5,
                    help="close must exceed EMA by k x ATR")
    # prior-context fakeout filter knobs
    ap.add_argument("--trend-max", type=int, default=20,
                    help="max consecutive bars beyond EMA before cross (0=off)")
    ap.add_argument("--div-lookback", type=int, default=10,
                    help="RSI divergence lookback bars (0=off)")
    ap.add_argument("--swing-lookback", type=int, default=50,
                    help="recent swing lookback bars (0=off)")
    ap.add_argument("--swing-k", type=float, default=0.5,
                    help="cross within k x ATR of swing = retest")
    ap.add_argument("--all", action="store_true",
                    help="run combined filters")
    ap.add_argument("--no-filters", action="store_true",
                    help="run baseline (no fakeout filters) only")
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()

    sym, tf = args.symbol.upper(), args.tf.upper()
    data_dir = Path(args.data_dir) if args.data_dir else DATA
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    path = data_dir / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    df["ema"] = ema(df["close"], EMA)
    df["rsi"] = wilder_rsi(df["close"], RSI_P)
    df["atr"] = wilder_atr(df, 14)
    df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

    close = df["close"].to_numpy(float)
    time = df["time"]
    n = len(df)
    t0, t1 = time.iloc[0], time.iloc[-1]
    span = t1 - t0
    edge1, edge2 = t0 + span * 0.60, t0 + span * 0.80
    bars_per_year = {"H1": 24 * 252, "H4": 6 * 252, "D1": 252}[tf]

    print(f"=== EMA21 fakeout detection  {sym} {tf} ===")
    print(f"bars {n}  {t0:%Y-%m-%d} -> {t1:%Y-%m-%d}")
    print(f"cost model: spread/2 (floor {args.floor_pts} pts) + "
          f"{args.slip_pts} pts slip + {args.comm_pts / 2:.1f} pts half-comm / side")
    print()

    def run(use_filters, label):
        pos = build_signals(df, "ema", "atr", "rsi",
                            args.wick, args.close_third, args.range_k,
                            args.dist_k, args.trend_max, args.div_lookback,
                            args.swing_lookback, args.swing_k, use_filters)
        pos_s = pd.Series(pos, index=df.index)
        rets, trades, _ = simulate_stops(df, pos_s, sym,
                                         comm_pts=args.comm_pts,
                                         slip_pts=args.slip_pts,
                                         floor_pts=args.floor_pts,
                                         stop_atr=args.stop_atr,
                                         tp_atr=args.tp_atr)
        validate_returns(rets.to_numpy(), close, np.abs(pos), label=label)
        evaluate(rets, pos, trades, time, bars_per_year, t0, edge1, edge2,
                 label, sym)
        if not args.no_publish:
            s = pd.Series(rets.to_numpy(), index=pd.DatetimeIndex(time))
            publish_results(f"ema21_fakeout_{sym}_{tf}_{label.replace(' ', '_')}",
                            s, append_to_bundle=False)
        return trades

    if args.no_filters or not args.all:
        run(False, "baseline (no filters)")
    if args.all:
        run(True, f"wick>{args.wick}x close<{args.close_third} "
                  f"range>{args.range_k}xATR dist>{args.dist_k}xATR "
                  f"trend>{args.trend_max} div>{args.div_lookback} "
                  f"swing>{args.swing_lookback}x{args.swing_k} ema-slope")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
