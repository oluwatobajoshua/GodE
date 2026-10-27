#!/usr/bin/env python3
"""Project Sovereign research lab.

Event-driven M5 backtester used to compare regime-aware entry models before
porting the winner to MQL5. It reads the CSVs produced by scripts/export_rates.py.

The model is deliberately conservative:
  * M5 bars are bid prices.
  * A fixed spread (default 10 points) is paid on every round trip.
  * Signals use completed bars only.
  * If a bar hits both SL and TP, SL is assumed first.
  * One position at a time, no pyramiding.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "research" / "data"
POINT = 0.00001


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------
def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1.0 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)

    tr = true_range(df)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_s
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame(
        {
            "plus_di": plus_di,
            "minus_di": minus_di,
            "adx": dx.ewm(alpha=1.0 / period, adjust=False).mean(),
            "atr": atr_s,
        },
        index=df.index,
    )


def rsi(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------
def load_data() -> pd.DataFrame:
    m5 = pd.read_csv(DATA_DIR / "EURUSD_M5.csv", parse_dates=["time"])
    h1 = pd.read_csv(DATA_DIR / "EURUSD_H1.csv", parse_dates=["time"])
    d1 = pd.read_csv(DATA_DIR / "EURUSD_D1.csv", parse_dates=["time"])

    h1 = h1.sort_values("time").reset_index(drop=True)
    d1 = d1.sort_values("time").reset_index(drop=True)

    h1["h1_sma20"] = h1["close"].rolling(20).mean()
    h1["h1_sma50"] = h1["close"].rolling(50).mean()
    h1["h1_sma200"] = h1["close"].rolling(200).mean()
    h1["h1_ema20"] = h1["close"].ewm(span=20, adjust=False).mean()
    h1_adx = adx(h1, 14)
    h1["h1_adx"] = h1_adx["adx"]
    h1["h1_atr"] = h1_adx["atr"]
    h1["h1_plus_di"] = h1_adx["plus_di"]
    h1["h1_minus_di"] = h1_adx["minus_di"]
    # An H1 bar becomes usable only after it has closed.
    h1["available"] = h1["time"] + pd.Timedelta(hours=1)

    d1["d1_sma50"] = d1["close"].rolling(50).mean()
    d1["d1_sma200"] = d1["close"].rolling(200).mean()
    d1["available"] = d1["time"] + pd.Timedelta(days=1)

    m5 = m5.sort_values("time").reset_index(drop=True)
    m5["atr"] = atr(m5, 14)
    m5["ema20"] = m5["close"].ewm(span=20, adjust=False).mean()
    m5["ema50"] = m5["close"].ewm(span=50, adjust=False).mean()
    m5["rsi2"] = rsi(m5["close"], 2)
    m5["rsi14"] = rsi(m5["close"], 14)
    m5["range_high"] = m5["high"].rolling(50).max().shift(1)
    m5["range_low"] = m5["low"].rolling(50).min().shift(1)

    h1_cols = [
        "available",
        "close",
        "h1_sma20",
        "h1_sma50",
        "h1_sma200",
        "h1_ema20",
        "h1_adx",
        "h1_atr",
        "h1_plus_di",
        "h1_minus_di",
    ]
    h1_features = h1[h1_cols].rename(columns={"close": "h1_close"})
    d1_features = d1[["available", "close", "d1_sma50", "d1_sma200"]].rename(
        columns={"close": "d1_close"}
    )

    merged = pd.merge_asof(
        m5,
        h1_features,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    merged = pd.merge_asof(
        merged,
        d1_features,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])
    return merged


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------
@dataclass
class Config:
    strategy: str = "adaptive"
    risk: float = 0.5
    spread: float = 10.0
    slippage: float = 1.0
    sl_atr: float = 2.0
    tp_r: float = 3.0
    be_r: float = 0.0
    partial_r: float = 0.0
    trail_r: float = 0.0
    adx_trend: float = 24.0
    adx_range: float = 18.0
    extreme: float = 0.15
    lookback: int = 50
    expiry_bars: int = 6
    session_start: int = 7
    session_end: int = 16
    min_sl_points: float = 25.0
    max_spread: float = 18.0
    start_equity: float = 10_000.0


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str
    entry: float
    exit: float
    sl: float
    tp: float
    lots: float
    pnl: float
    r: float
    reason: str


@dataclass
class Backtest:
    trades: list[Trade] = field(default_factory=list)
    equity: list[tuple[pd.Timestamp, float]] = field(default_factory=list)


def in_session(ts: pd.Timestamp, start: int, end: int) -> bool:
    hour = ts.hour
    return start <= hour <= end and ts.weekday() < 5


def trend_state(row: pd.Series, cfg: Config) -> str:
    if pd.isna(row["h1_sma200"]) or pd.isna(row["h1_adx"]):
        return "none"
    bull = (
        row["h1_close"] > row["h1_sma20"] > row["h1_sma50"]
        and row["h1_close"] > row["h1_sma200"]
        and row["h1_adx"] >= cfg.adx_trend
        and row["h1_plus_di"] > row["h1_minus_di"]
    )
    bear = (
        row["h1_close"] < row["h1_sma20"] < row["h1_sma50"]
        and row["h1_close"] < row["h1_sma200"]
        and row["h1_adx"] >= cfg.adx_trend
        and row["h1_minus_di"] > row["h1_plus_di"]
    )
    if bull:
        return "bull"
    if bear:
        return "bear"
    return "none"


def range_state(row: pd.Series, cfg: Config) -> bool:
    return (
        not pd.isna(row["h1_adx"])
        and row["h1_adx"] <= cfg.adx_range
        and not pd.isna(row["range_high"])
        and not pd.isna(row["range_low"])
    )


def backtest(df: pd.DataFrame, cfg: Config) -> Backtest:
    result = Backtest()
    equity = cfg.start_equity
    pos = None
    pending = None
    last_entry_time = None

    n = len(df)
    warmup = max(cfg.lookback + 5, 220)
    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    times = df["time"].to_numpy()
    atr_arr = df["atr"].to_numpy()
    ema20_arr = df["ema20"].to_numpy()
    ema50_arr = df["ema50"].to_numpy()
    rh_arr = df["range_high"].to_numpy()
    rl_arr = df["range_low"].to_numpy()
    rsi2_arr = df["rsi2"].to_numpy()
    spread_arr = df["spread"].to_numpy()

    for i in range(warmup, n):
        ts = pd.Timestamp(times[i])
        if ts.weekday() >= 5:
            continue

        # ---- manage open position on this bar --------------------------------
        if pos is not None:
            side = pos["side"]
            sl = pos["sl"]
            tp = pos["tp"]
            entry = pos["entry"]
            lots = pos["lots"]
            hit = None
            exit_price = None
            reason = ""

            if side == "long":
                # Conservative: if both levels are touched, assume SL first.
                if lows[i] <= sl:
                    exit_price, hit, reason = sl, True, "sl"
                elif highs[i] >= tp:
                    exit_price, hit, reason = tp, True, "tp"
                else:
                    # Optional trailing stop.
                    if cfg.trail_r > 0:
                        risk = entry - pos["initial_sl"]
                        new_sl = highs[i] - risk * cfg.trail_r
                        if new_sl > sl and new_sl < closes[i]:
                            pos["sl"] = new_sl
                            sl = new_sl
            else:
                if highs[i] >= sl:
                    exit_price, hit, reason = sl, True, "sl"
                elif lows[i] <= tp:
                    exit_price, hit, reason = tp, True, "tp"
                else:
                    if cfg.trail_r > 0:
                        risk = pos["initial_sl"] - entry
                        new_sl = lows[i] + risk * cfg.trail_r
                        if new_sl < sl and new_sl > closes[i]:
                            pos["sl"] = new_sl
                            sl = new_sl

            if hit:
                cost_points = cfg.spread + cfg.slippage
                if side == "long":
                    pnl = (exit_price - entry) / POINT * lots - cost_points * lots
                else:
                    pnl = (entry - exit_price) / POINT * lots - cost_points * lots
                equity += pnl
                result.trades.append(
                    Trade(
                        entry_time=pos["time"],
                        exit_time=ts,
                        side=side,
                        entry=entry,
                        exit=exit_price,
                        sl=pos["initial_sl"],
                        tp=pos["tp"],
                        lots=lots,
                        pnl=pnl,
                        r=pnl / max(pos["risk_money"], 1e-9),
                        reason=reason,
                    )
                )
                result.equity.append((ts, equity))
                pos = None

        # ---- pending limit/stop order ----------------------------------------
        if pos is None and pending is not None:
            side = pending["side"]
            kind = pending["kind"]
            price = pending["price"]
            filled = False
            fill_price = None
            if kind == "limit":
                if side == "long" and lows[i] <= price:
                    fill_price, filled = price, True
                elif side == "short" and highs[i] >= price:
                    fill_price, filled = price, True
            elif kind == "stop":
                if side == "long" and highs[i] >= price:
                    fill_price, filled = max(opens[i], price), True
                elif side == "short" and lows[i] <= price:
                    fill_price, filled = min(opens[i], price), True

            if filled:
                sl_dist = pending["sl_dist"]
                tp_dist = pending["tp_dist"]
                entry = fill_price
                if side == "long":
                    sl = entry - sl_dist * POINT
                    tp = entry + tp_dist * POINT
                else:
                    sl = entry + sl_dist * POINT
                    tp = entry - tp_dist * POINT
                cost_points = cfg.spread + cfg.slippage
                risk_points = sl_dist + cost_points
                lots = equity * cfg.risk / 100.0 / max(risk_points, 1.0)
                lots = max(0.01, round(lots, 2))
                pos = {
                    "side": side,
                    "entry": entry,
                    "sl": sl,
                    "initial_sl": sl,
                    "tp": tp,
                    "lots": lots,
                    "time": ts,
                    "risk_money": equity * cfg.risk / 100.0,
                }
                pending = None
                last_entry_time = ts
                # Check same-bar stop/target conservatively.
                if side == "long" and lows[i] <= sl:
                    pnl = (sl - entry) / POINT * lots - cost_points * lots
                    equity += pnl
                    result.trades.append(
                        Trade(ts, ts, side, entry, sl, sl, tp, lots, pnl, -1.0, "sl")
                    )
                    result.equity.append((ts, equity))
                    pos = None
                elif side == "short" and highs[i] >= sl:
                    pnl = (entry - sl) / POINT * lots - cost_points * lots
                    equity += pnl
                    result.trades.append(
                        Trade(ts, ts, side, entry, sl, sl, tp, lots, pnl, -1.0, "sl")
                    )
                    result.equity.append((ts, equity))
                    pos = None
            else:
                pending["age"] += 1
                if pending["age"] > cfg.expiry_bars:
                    pending = None

        # ---- new signal -------------------------------------------------------
        if pos is None and pending is None:
            prev = df.iloc[i - 1]
            if not in_session(ts, cfg.session_start, cfg.session_end):
                continue
            if spread_arr[i - 1] > cfg.max_spread:
                continue
            if last_entry_time is not None and (ts - last_entry_time) < pd.Timedelta(minutes=5):
                continue

            tstate = trend_state(prev, cfg)
            rstate = range_state(prev, cfg)
            atr_pts = atr_arr[i - 1] / POINT
            if pd.isna(atr_pts) or atr_pts <= 0:
                continue

            signal = None
            sl_dist = 0.0
            tp_dist = 0.0

            if cfg.strategy == "trend_breakout":
                if tstate == "bull" and closes[i - 1] > rh_arr[i - 1]:
                    signal = "long"
                elif tstate == "bear" and closes[i - 1] < rl_arr[i - 1]:
                    signal = "short"
                sl_dist = cfg.sl_atr * atr_pts
                tp_dist = sl_dist * cfg.tp_r

            elif cfg.strategy == "trend_pullback":
                if tstate == "bull" and lows[i - 1] <= ema20_arr[i - 1] <= highs[i - 1] and closes[i - 1] > ema20_arr[i - 1]:
                    signal = "long"
                elif tstate == "bear" and lows[i - 1] <= ema20_arr[i - 1] <= highs[i - 1] and closes[i - 1] < ema20_arr[i - 1]:
                    signal = "short"
                sl_dist = cfg.sl_atr * atr_pts
                tp_dist = sl_dist * cfg.tp_r

            elif cfg.strategy == "range_fade":
                if rstate:
                    rng = rh_arr[i - 1] - rl_arr[i - 1]
                    premium = rh_arr[i - 1] - rng * cfg.extreme
                    discount = rl_arr[i - 1] + rng * cfg.extreme
                    if closes[i - 1] <= discount and rsi2_arr[i - 1] < 15:
                        signal = "long"
                    elif closes[i - 1] >= premium and rsi2_arr[i - 1] > 85:
                        signal = "short"
                sl_dist = cfg.sl_atr * atr_pts
                tp_dist = sl_dist * cfg.tp_r

            elif cfg.strategy == "mr_market":
                rng = rh_arr[i - 1] - rl_arr[i - 1]
                premium = rh_arr[i - 1] - rng * cfg.extreme
                discount = rl_arr[i - 1] + rng * cfg.extreme
                if tstate == "bull" and prev["low"] <= discount and prev["close"] > prev["open"] and prev["close"] > discount:
                    signal = "long"
                elif tstate == "bear" and prev["high"] >= premium and prev["close"] < prev["open"] and prev["close"] < premium:
                    signal = "short"
                sl_dist = cfg.sl_atr * atr_pts
                tp_dist = sl_dist * cfg.tp_r

            elif cfg.strategy == "adaptive":
                if tstate == "bull" and lows[i - 1] <= ema20_arr[i - 1] <= highs[i - 1] and closes[i - 1] > ema20_arr[i - 1]:
                    signal = "long"
                    sl_dist = cfg.sl_atr * atr_pts
                elif tstate == "bear" and lows[i - 1] <= ema20_arr[i - 1] <= highs[i - 1] and closes[i - 1] < ema20_arr[i - 1]:
                    signal = "short"
                    sl_dist = cfg.sl_atr * atr_pts
                elif rstate:
                    rng = rh_arr[i - 1] - rl_arr[i - 1]
                    premium = rh_arr[i - 1] - rng * cfg.extreme
                    discount = rl_arr[i - 1] + rng * cfg.extreme
                    if closes[i - 1] <= discount and rsi2_arr[i - 1] < 15:
                        signal = "long"
                    elif closes[i - 1] >= premium and rsi2_arr[i - 1] > 85:
                        signal = "short"
                    sl_dist = cfg.sl_atr * atr_pts
                tp_dist = sl_dist * cfg.tp_r

            if signal is not None:
                sl_dist = max(sl_dist, cfg.min_sl_points)
                tp_dist = max(tp_dist, sl_dist * cfg.tp_r)
                # Market entry at this bar's open. The round-trip spread is
                # charged against P&L, so entry/exit levels stay symmetric.
                entry = opens[i]
                if signal == "long":
                    sl = entry - sl_dist * POINT
                    tp = entry + tp_dist * POINT
                else:
                    sl = entry + sl_dist * POINT
                    tp = entry - tp_dist * POINT
                cost_points = cfg.spread + cfg.slippage
                risk_points = sl_dist + cost_points
                lots = equity * cfg.risk / 100.0 / max(risk_points, 1.0)
                lots = max(0.01, round(lots, 2))
                pos = {
                    "side": signal,
                    "entry": entry,
                    "sl": sl,
                    "initial_sl": sl,
                    "tp": tp,
                    "lots": lots,
                    "time": ts,
                    "risk_money": equity * cfg.risk / 100.0,
                }
                last_entry_time = ts
                # Same-bar conservative stop check.
                if signal == "long" and lows[i] <= sl:
                    pnl = (sl - entry) / POINT * lots - cost_points * lots
                    equity += pnl
                    result.trades.append(
                        Trade(ts, ts, signal, entry, sl, sl, tp, lots, pnl, -1.0, "sl")
                    )
                    result.equity.append((ts, equity))
                    pos = None
                elif signal == "short" and highs[i] >= sl:
                    pnl = (entry - sl) / POINT * lots - cost_points * lots
                    equity += pnl
                    result.trades.append(
                        Trade(ts, ts, signal, entry, sl, sl, tp, lots, pnl, -1.0, "sl")
                    )
                    result.equity.append((ts, equity))
                    pos = None

    if pos is not None:
        # Mark-to-market close at the last bar.
        last = closes[-1]
        cost_points = cfg.spread + cfg.slippage
        if pos["side"] == "long":
            pnl = (last - pos["entry"]) / POINT * pos["lots"] - cost_points * pos["lots"]
        else:
            pnl = (pos["entry"] - last) / POINT * pos["lots"] - cost_points * pos["lots"]
        equity += pnl
        result.trades.append(
            Trade(
                pos["time"],
                pd.Timestamp(times[-1]),
                pos["side"],
                pos["entry"],
                last,
                pos["sl"],
                pos["tp"],
                pos["lots"],
                pnl,
                pnl / max(pos["risk_money"], 1e-9),
                "open",
            )
        )
        result.equity.append((pd.Timestamp(times[-1]), equity))
    return result


def report(result: Backtest, cfg: Config) -> None:
    if not result.trades:
        print("No trades.")
        return
    t = pd.DataFrame([trade.__dict__ for trade in result.trades])
    t["entry_time"] = pd.to_datetime(t["entry_time"])
    t["exit_time"] = pd.to_datetime(t["exit_time"])
    t["month"] = t["exit_time"].dt.to_period("M")
    t["win"] = t["pnl"] > 0

    net = t["pnl"].sum()
    gross_profit = t.loc[t["pnl"] > 0, "pnl"].sum()
    gross_loss = -t.loc[t["pnl"] < 0, "pnl"].sum()
    pf = gross_profit / gross_loss if gross_loss else float("inf")
    wins = t[t["pnl"] > 0]
    losses = t[t["pnl"] < 0]
    print(f"strategy={cfg.strategy} trades={len(t)} win%={t['win'].mean() * 100:.1f} "
          f"net=${net:,.2f} pf={pf:.2f} avgR={t['r'].mean():.2f} "
          f"avgWin=${wins['pnl'].mean() if len(wins) else 0:.2f} "
          f"avgLoss=${losses['pnl'].mean() if len(losses) else 0:.2f}")
    print(t.groupby("month").agg(
        n=("pnl", "size"),
        wins=("win", "sum"),
        net=("pnl", "sum"),
        avgR=("r", "mean"),
    ).to_string())
    # Max drawdown on closed-trade equity.
    eq = t["pnl"].cumsum() + cfg.start_equity
    dd = (eq.cummax() - eq).max()
    print(f"max closed-trade DD=${dd:,.2f} ({dd / cfg.start_equity * 100:.2f}%) "
          f"final=${cfg.start_equity + net:,.2f}")
    print(t.groupby(["month", "side"]).agg(n=("pnl", "size"), net=("pnl", "sum")).to_string())


def main() -> int:
    parser = argparse.ArgumentParser(description="Sovereign research lab")
    parser.add_argument("--strategy", default="adaptive",
                        choices=["adaptive", "trend_breakout", "trend_pullback", "range_fade", "mr_market"])
    parser.add_argument("--risk", type=float, default=0.5)
    parser.add_argument("--spread", type=float, default=10.0)
    parser.add_argument("--sl-atr", type=float, default=2.0)
    parser.add_argument("--tp-r", type=float, default=3.0)
    parser.add_argument("--adx-trend", type=float, default=24.0)
    parser.add_argument("--adx-range", type=float, default=18.0)
    parser.add_argument("--extreme", type=float, default=0.15)
    parser.add_argument("--lookback", type=int, default=50)
    parser.add_argument("--session-start", type=int, default=7)
    parser.add_argument("--session-end", type=int, default=16)
    parser.add_argument("--min-sl", type=float, default=25.0)
    parser.add_argument("--trail-r", type=float, default=0.0)
    parser.add_argument("--from-date", default=None)
    parser.add_argument("--to-date", default=None)
    args = parser.parse_args()

    df = load_data()
    if args.from_date:
        df = df[df["time"] >= pd.Timestamp(args.from_date, tz="UTC")]
    if args.to_date:
        df = df[df["time"] <= pd.Timestamp(args.to_date, tz="UTC")]
    df = df.reset_index(drop=True)

    cfg = Config(
        strategy=args.strategy,
        risk=args.risk,
        spread=args.spread,
        sl_atr=args.sl_atr,
        tp_r=args.tp_r,
        adx_trend=args.adx_trend,
        adx_range=args.adx_range,
        extreme=args.extreme,
        lookback=args.lookback,
        session_start=args.session_start,
        session_end=args.session_end,
        min_sl_points=args.min_sl,
        trail_r=args.trail_r,
    )
    result = backtest(df, cfg)
    report(result, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
