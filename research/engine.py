#!/usr/bin/env python3
"""Fast event-driven backtester for the Sovereign research pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core import POINT

EPS = 1e-9


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
    partial_frac: float = 0.5
    trail_atr: float = 0.0
    trail_start_r: float = 1.0
    time_exit_bars: int = 0
    adx_trend: float = 24.0
    adx_range: float = 18.0
    extreme: float = 0.15
    lookback: int = 50
    expiry_bars: int = 6
    session_start: int = 7
    session_end: int = 16
    min_sl_points: float = 25.0
    min_atr_points: float = 20.0
    max_spread: float = 20.0
    start_equity: float = 10_000.0
    daily_dd_limit: float = 0.0
    max_trades_day: int = 0
    cooldown_bars: int = 1
    require_stack: bool = True
    require_di: bool = True
    max_h1_dist_atr: float = 0.0
    entry_buffer_atr: float = 0.05
    asian_start: int = 0
    session_breakout_expiry: int = 60
    session_breakout_tp_r: float = 2.0
    momentum_atr: float = 1.0
    rsi_long: float = 10.0
    rsi_short: float = 90.0
    trend_engine: str = "pullback"  # pullback, breakout, both
    mr_limit_require_trend: bool = False
    max_lots: float = 100.0
    timeframe: str = "M5"  # metadata only


@dataclass
class Trade:
    position_id: int
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
    bars_held: int
    mfe_r: float
    mae_r: float
    regime: str
    adx: float
    atr_points: float
    hour: int


@dataclass
class Result:
    cfg: Config
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    start_equity: float = 10_000.0
    final_equity: float = 10_000.0


def _nan(*values: float) -> bool:
    return any(np.isnan(v) for v in values)


def trend_at(i: int, a: dict[str, np.ndarray], cfg: Config) -> str:
    vals = (
        a["h1_close"][i],
        a["h1_sma20"][i],
        a["h1_sma50"][i],
        a["h1_sma200"][i],
        a["h1_adx"][i],
        a["h1_plus_di"][i],
        a["h1_minus_di"][i],
        a["h1_atr"][i],
    )
    if _nan(*vals):
        return "none"
    close, sma20, sma50, sma200, adx, plus_di, minus_di, h1_atr = vals
    if cfg.max_h1_dist_atr > 0 and h1_atr > 0:
        if abs(close - sma200) / h1_atr > cfg.max_h1_dist_atr:
            return "none"
    bull = close > sma20
    bear = close < sma20
    if cfg.require_stack:
        bull = bull and sma20 > sma50 and close > sma200
        bear = bear and sma20 < sma50 and close < sma200
    else:
        bull = bull and close > sma200
        bear = bear and close < sma200
    if cfg.require_di:
        bull = bull and plus_di > minus_di
        bear = bear and minus_di > plus_di
    bull = bull and adx >= cfg.adx_trend
    bear = bear and adx >= cfg.adx_trend
    if bull:
        return "bull"
    if bear:
        return "bear"
    return "none"


def range_at(i: int, a: dict[str, np.ndarray], cfg: Config) -> bool:
    adx = a["h1_adx"][i]
    return not np.isnan(adx) and adx <= cfg.adx_range


def _signal(
    side: str,
    sl_dist: float,
    atr_points: float,
    cfg: Config,
    *,
    kind: str = "market",
    price: float | None = None,
) -> dict[str, Any]:
    sl_dist = max(sl_dist, cfg.min_sl_points)
    tp_dist = sl_dist * cfg.tp_r if cfg.tp_r > 0 else 0.0
    return {
        "side": side,
        "kind": kind,
        "price": price,
        "sl_dist": sl_dist,
        "tp_dist": tp_dist,
        "atr_points": atr_points,
    }


def run(df: pd.DataFrame, cfg: Config) -> Result:
    a = {
        c: df[c].to_numpy()
        for c in [
            "open",
            "high",
            "low",
            "close",
            "atr",
            "atr_ratio",
            "atr_pct",
            "ema20",
            "ema50",
            "ema100",
            "rsi2",
            "rsi14",
            "range_high",
            "range_low",
            "range_width",
            "range_pos",
            "body_atr",
            "ret1_atr",
            "mom6_atr",
            "mom12_atr",
            "swing_low5",
            "swing_high5",
            "h1_close",
            "h1_sma20",
            "h1_sma50",
            "h1_sma200",
            "h1_adx",
            "h1_atr",
            "h1_plus_di",
            "h1_minus_di",
            "spread",
            "hour",
            "weekday",
        ]
    }
    dates = df["date"].to_numpy()
    times = df["time"].to_numpy()
    n = len(df)
    warmup = max(cfg.lookback + 5, 220)

    # Daily Asian range for the session-breakout model.
    asian = (
        df[(df["hour"] >= cfg.asian_start) & (df["hour"] < cfg.session_start)]
        .groupby("date")
        .agg(asian_high=("high", "max"), asian_low=("low", "min"))
    )
    asian_high_map = dict(zip(asian.index, asian["asian_high"]))
    asian_low_map = dict(zip(asian.index, asian["asian_low"]))

    result = Result(cfg=cfg, start_equity=cfg.start_equity)
    equity = cfg.start_equity
    pos: dict[str, Any] | None = None
    pending: list[dict[str, Any]] = []
    last_entry_i = -10_000
    next_position_id = 1
    current_date = None
    day_start_equity = equity
    day_trades = 0
    day_breakout_done = False

    def close_lots(
        pos: dict[str, Any],
        lots: float,
        price: float,
        reason: str,
        i: int,
    ) -> dict[str, Any] | None:
        nonlocal equity
        lots = min(round(lots, 2), pos["lots"])
        if lots < 0.01:
            return pos
        cost = cfg.spread + cfg.slippage
        if pos["side"] == "long":
            pnl = (price - pos["entry"]) / POINT * lots - cost * lots
        else:
            pnl = (pos["entry"] - price) / POINT * lots - cost * lots
        equity += pnl
        held = i - pos["entry_i"]
        sl_dist = pos["sl_dist"]
        mfe_r = pos["mfe"] / sl_dist if sl_dist else 0.0
        mae_r = pos["mae"] / sl_dist if sl_dist else 0.0
        result.trades.append(
            Trade(
                position_id=pos["id"],
                entry_time=pd.Timestamp(pos["entry_time"]),
                exit_time=pd.Timestamp(times[i]),
                side=pos["side"],
                entry=pos["entry"],
                exit=price,
                sl=pos["initial_sl"],
                tp=pos["tp"],
                lots=lots,
                pnl=pnl,
                r=pnl / max(pos["risk_money"], EPS),
                reason=reason,
                bars_held=held,
                mfe_r=mfe_r,
                mae_r=mae_r,
                regime=pos["regime"],
                adx=pos["adx"],
                atr_points=pos["atr_points"],
                hour=pos["hour"],
            )
        )
        result.equity_curve.append((pd.Timestamp(times[i]), equity))
        pos["lots"] = round(pos["lots"] - lots, 2)
        if pos["lots"] <= 0.001:
            return None
        return pos

    def manage(pos: dict[str, Any], i: int, same_bar: bool = False) -> dict[str, Any] | None:
        high = a["high"][i]
        low = a["low"][i]
        close = a["close"][i]
        atr_pts = pos["atr_points"]
        entry = pos["entry"]
        sl = pos["sl"]
        tp = pos["tp"]
        sl_dist = pos["sl_dist"]

        # Track excursions in points.
        if pos["side"] == "long":
            pos["mfe"] = max(pos["mfe"], (high - entry) / POINT)
            pos["mae"] = min(pos["mae"], (low - entry) / POINT)
            if low <= sl:
                fill = min(a["open"][i], sl)
                return close_lots(pos, pos["lots"], fill, "sl", i)
        else:
            pos["mfe"] = max(pos["mfe"], (entry - low) / POINT)
            pos["mae"] = min(pos["mae"], (entry - high) / POINT)
            if high >= sl:
                fill = max(a["open"][i], sl)
                return close_lots(pos, pos["lots"], fill, "sl", i)

        # Partial at a defined R multiple.
        if cfg.partial_r > 0 and not pos["partial_done"]:
            if pos["side"] == "long":
                level = entry + cfg.partial_r * sl_dist * POINT
                touched = high >= level
            else:
                level = entry - cfg.partial_r * sl_dist * POINT
                touched = low <= level
            if touched:
                part_lots = max(0.01, round(pos["lots"] * cfg.partial_frac, 2))
                if part_lots >= pos["lots"]:
                    part_lots = max(0.01, round(pos["lots"] - 0.01, 2))
                pos = close_lots(pos, part_lots, level, "partial", i)
                if pos is None:
                    return None
                pos["partial_done"] = True
                pos["sl"] = entry if pos["side"] == "long" else entry
                sl = pos["sl"]

        # Fixed target.
        if tp > 0:
            if pos["side"] == "long" and high >= tp:
                return close_lots(pos, pos["lots"], tp, "tp", i)
            if pos["side"] == "short" and low <= tp:
                return close_lots(pos, pos["lots"], tp, "tp", i)

        # Break-even.
        if cfg.be_r > 0:
            if pos["side"] == "long" and high >= entry + cfg.be_r * sl_dist * POINT:
                pos["sl"] = max(pos["sl"], entry)
            elif pos["side"] == "short" and low <= entry - cfg.be_r * sl_dist * POINT:
                pos["sl"] = min(pos["sl"], entry)

        # ATR trailing stop.
        if cfg.trail_atr > 0 and not same_bar:
            if pos["side"] == "long":
                if (high - entry) / POINT >= cfg.trail_start_r * sl_dist:
                    new_sl = high - cfg.trail_atr * atr_pts * POINT
                    if new_sl > pos["sl"] and new_sl < close:
                        pos["sl"] = new_sl
            else:
                if (entry - low) / POINT >= cfg.trail_start_r * sl_dist:
                    new_sl = low + cfg.trail_atr * atr_pts * POINT
                    if new_sl < pos["sl"] and new_sl > close:
                        pos["sl"] = new_sl

        # Time-based exit.
        if cfg.time_exit_bars > 0 and (i - pos["entry_i"]) >= cfg.time_exit_bars:
            return close_lots(pos, pos["lots"], close, "time", i)
        return pos

    def open_position(
        side: str,
        price: float,
        sl_dist: float,
        tp_dist: float,
        atr_points: float,
        regime: str,
        adx: float,
        i: int,
        strategy: str,
    ) -> dict[str, Any]:
        nonlocal next_position_id
        sl_dist = max(sl_dist, cfg.min_sl_points)
        cost = cfg.spread + cfg.slippage
        risk_points = sl_dist + cost
        risk_money = equity * cfg.risk / 100.0
        lots = risk_money / max(risk_points, 1.0)
        lots = max(0.01, round(lots, 2))
        lots = min(lots, cfg.max_lots)
        if side == "long":
            sl = price - sl_dist * POINT
            tp = price + tp_dist * POINT if tp_dist > 0 else 0.0
        else:
            sl = price + sl_dist * POINT
            tp = price - tp_dist * POINT if tp_dist > 0 else 0.0
        pos = {
            "id": next_position_id,
            "side": side,
            "entry": price,
            "entry_i": i,
            "entry_time": times[i],
            "initial_sl": sl,
            "sl": sl,
            "tp": tp,
            "lots": lots,
            "initial_lots": lots,
            "sl_dist": sl_dist,
            "tp_dist": tp_dist,
            "risk_money": risk_money,
            "partial_done": False,
            "mfe": 0.0,
            "mae": 0.0,
            "regime": regime,
            "adx": adx,
            "atr_points": atr_points,
            "hour": int(a["hour"][i]),
            "strategy": strategy,
        }
        next_position_id += 1
        return pos

    for i in range(warmup, n):
        ts = pd.Timestamp(times[i])
        if a["weekday"][i] >= 5:
            continue

        date = dates[i]
        if date != current_date:
            current_date = date
            day_start_equity = equity
            day_trades = 0
            day_breakout_done = False

        # Manage an existing position first.
        closed_this_bar = False
        if pos is not None:
            pos = manage(pos, i, same_bar=False)
            if pos is None:
                closed_this_bar = True

        # Check pending orders.
        if pos is None and pending:
            fills: list[dict[str, Any]] = []
            both_breakout = False
            for order in pending:
                kind = order["kind"]
                side = order["side"]
                price = order["price"]
                fill: float | None = None
                if kind == "limit":
                    if side == "long" and a["low"][i] <= price:
                        fill = min(a["open"][i], price)
                    elif side == "short" and a["high"][i] >= price:
                        fill = max(a["open"][i], price)
                elif kind == "stop":
                    if side == "long" and a["high"][i] >= price:
                        fill = max(a["open"][i], price)
                    elif side == "short" and a["low"][i] <= price:
                        fill = min(a["open"][i], price)
                if fill is not None:
                    fills.append({**order, "fill": fill})
            if len(fills) >= 2 and any(o["tag"] == "breakout" for o in fills):
                both_breakout = True
                fills = []
            if both_breakout:
                pending = []
            elif fills:
                order = fills[0]
                pending = []
                pos = open_position(
                    order["side"],
                    order["fill"],
                    order["sl_dist"],
                    order["tp_dist"],
                    order["atr_points"],
                    order["regime"],
                    order["adx"],
                    i,
                    order["strategy"],
                )
                last_entry_i = i
                day_trades += 1
                pos = manage(pos, i, same_bar=True)
                if pos is None:
                    closed_this_bar = True
            else:
                pending = [o for o in pending if i - o["placed_i"] < o["expiry_bars"]]

        # New entry signal.
        if pos is None and not pending and not closed_this_bar:
            if not (cfg.session_start <= a["hour"][i] < cfg.session_end):
                continue
            if a["weekday"][i] >= 5:
                continue
            if a["spread"][i] > cfg.max_spread:
                continue
            if cfg.daily_dd_limit > 0 and equity <= day_start_equity * (1.0 - cfg.daily_dd_limit / 100.0):
                continue
            if cfg.max_trades_day > 0 and day_trades >= cfg.max_trades_day:
                continue
            if i - last_entry_i < cfg.cooldown_bars:
                continue

            j = i - 1
            atr_pts = a["atr"][j] / POINT
            if np.isnan(atr_pts) or atr_pts < cfg.min_atr_points:
                continue

            regime = trend_at(j, a, cfg)
            in_range = range_at(j, a, cfg)
            if regime == "none" and in_range:
                regime_label = "range"
            elif regime in ("bull", "bear"):
                regime_label = regime
            else:
                regime_label = "chop"

            adx_val = a["h1_adx"][j]
            signal: dict[str, Any] | None = None
            strategy_used = cfg.strategy

            def trend_pullback_signal() -> dict[str, Any] | None:
                if regime == "bull":
                    touched = a["low"][j] <= a["ema20"][j] <= a["high"][j]
                    confirmed = a["close"][j] > a["ema20"][j] and a["close"][j] > a["open"][j]
                    if touched and confirmed and a["rsi2"][j] < 55:
                        return _signal("long", cfg.sl_atr * atr_pts, atr_pts, cfg)
                elif regime == "bear":
                    touched = a["low"][j] <= a["ema20"][j] <= a["high"][j]
                    confirmed = a["close"][j] < a["ema20"][j] and a["close"][j] < a["open"][j]
                    if touched and confirmed and a["rsi2"][j] > 45:
                        return _signal("short", cfg.sl_atr * atr_pts, atr_pts, cfg)
                return None

            def trend_breakout_signal() -> dict[str, Any] | None:
                buf = cfg.entry_buffer_atr * atr_pts * POINT
                if regime == "bull" and a["close"][j] > a["range_high"][j] + buf:
                    return _signal("long", cfg.sl_atr * atr_pts, atr_pts, cfg)
                if regime == "bear" and a["close"][j] < a["range_low"][j] - buf:
                    return _signal("short", cfg.sl_atr * atr_pts, atr_pts, cfg)
                return None

            def range_fade_signal() -> dict[str, Any] | None:
                if not in_range or np.isnan(a["range_pos"][j]):
                    return None
                if (
                    a["range_pos"][j] <= cfg.extreme
                    and a["rsi2"][j] <= cfg.rsi_long
                    and a["close"][j] > a["open"][j]
                ):
                    return _signal("long", cfg.sl_atr * atr_pts, atr_pts, cfg)
                if (
                    a["range_pos"][j] >= 1.0 - cfg.extreme
                    and a["rsi2"][j] >= cfg.rsi_short
                    and a["close"][j] < a["open"][j]
                ):
                    return _signal("short", cfg.sl_atr * atr_pts, atr_pts, cfg)
                return None

            if cfg.strategy == "trend_pullback":
                signal = trend_pullback_signal()
            elif cfg.strategy == "trend_breakout":
                signal = trend_breakout_signal()
            elif cfg.strategy == "range_fade":
                signal = range_fade_signal()
            elif cfg.strategy == "momentum":
                if regime == "bull" and a["body_atr"][j] >= cfg.momentum_atr and a["close"][j] > a["open"][j]:
                    signal = _signal("long", cfg.sl_atr * atr_pts, atr_pts, cfg)
                elif regime == "bear" and a["body_atr"][j] >= cfg.momentum_atr and a["close"][j] < a["open"][j]:
                    signal = _signal("short", cfg.sl_atr * atr_pts, atr_pts, cfg)
            elif cfg.strategy == "mr_limit":
                if (regime in ("bull", "bear") or not cfg.mr_limit_require_trend) and not np.isnan(a["range_pos"][j]):
                    buf = cfg.entry_buffer_atr * atr_pts * POINT
                    if a["range_pos"][j] <= cfg.extreme:
                        signal = _signal(
                            "long",
                            cfg.sl_atr * atr_pts,
                            atr_pts,
                            cfg,
                            kind="limit",
                            price=a["close"][j] - buf,
                        )
                    elif a["range_pos"][j] >= 1.0 - cfg.extreme:
                        signal = _signal(
                            "short",
                            cfg.sl_atr * atr_pts,
                            atr_pts,
                            cfg,
                            kind="limit",
                            price=a["close"][j] + buf,
                        )
            elif cfg.strategy == "session_breakout":
                if (
                    a["hour"][i] == cfg.session_start
                    and not day_breakout_done
                    and dates[i] in asian_high_map
                ):
                    high = asian_high_map[dates[i]]
                    low = asian_low_map[dates[i]]
                    if not (np.isnan(high) or np.isnan(low)):
                        day_breakout_done = True
                        buf = cfg.entry_buffer_atr * atr_pts * POINT
                        buy = high + buf
                        sell = low - buf
                        sl_long = max((buy - low) / POINT, cfg.sl_atr * atr_pts)
                        sl_short = max((high - sell) / POINT, cfg.sl_atr * atr_pts)
                        pending = [
                            {
                                "side": "long",
                                "kind": "stop",
                                "price": buy,
                                "sl_dist": sl_long,
                                "tp_dist": sl_long * cfg.session_breakout_tp_r,
                                "atr_points": atr_pts,
                                "regime": "breakout",
                                "adx": adx_val,
                                "strategy": "session_breakout",
                                "placed_i": i,
                                "expiry_bars": cfg.session_breakout_expiry,
                                "tag": "breakout",
                            },
                            {
                                "side": "short",
                                "kind": "stop",
                                "price": sell,
                                "sl_dist": sl_short,
                                "tp_dist": sl_short * cfg.session_breakout_tp_r,
                                "atr_points": atr_pts,
                                "regime": "breakout",
                                "adx": adx_val,
                                "strategy": "session_breakout",
                                "placed_i": i,
                                "expiry_bars": cfg.session_breakout_expiry,
                                "tag": "breakout",
                            },
                        ]
            elif cfg.strategy == "adaptive":
                if regime in ("bull", "bear"):
                    if cfg.trend_engine == "pullback":
                        signal = trend_pullback_signal()
                    elif cfg.trend_engine == "breakout":
                        signal = trend_breakout_signal()
                    else:
                        signal = trend_breakout_signal() or trend_pullback_signal()
                elif in_range:
                    signal = range_fade_signal()

            if signal is not None:
                if signal["kind"] == "market":
                    pos = open_position(
                        signal["side"],
                        a["open"][i],
                        signal["sl_dist"],
                        signal["tp_dist"],
                        signal["atr_points"],
                        regime_label,
                        adx_val,
                        i,
                        strategy_used,
                    )
                    last_entry_i = i
                    day_trades += 1
                    pos = manage(pos, i, same_bar=True)
                    if pos is None:
                        closed_this_bar = True
                else:
                    pending = [
                        {
                            "side": signal["side"],
                            "kind": signal["kind"],
                            "price": signal["price"],
                            "sl_dist": signal["sl_dist"],
                            "tp_dist": signal["tp_dist"],
                            "atr_points": signal["atr_points"],
                            "regime": regime_label,
                            "adx": adx_val,
                            "strategy": strategy_used,
                            "placed_i": i,
                            "expiry_bars": cfg.expiry_bars,
                            "tag": "entry",
                        }
                    ]

    # Close any open position at the end of the data.
    if pos is not None:
        last = n - 1
        pos = close_lots(pos, pos["lots"], a["close"][last], "eod", last)

    result.final_equity = equity
    return result


def metrics(result: Result, trades: pd.DataFrame | None = None) -> dict[str, Any]:
    """Compute compact metrics, including monthly consistency."""
    if trades is None:
        trades = pd.DataFrame([t.__dict__ for t in result.trades])
    if trades.empty:
        return {
            "trades": 0,
            "positions": 0,
            "net": 0.0,
            "pf": 0.0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "expectancy": 0.0,
            "max_dd": 0.0,
            "max_dd_pct": 0.0,
            "final_equity": result.start_equity,
            "positive_months": 0,
            "months": 0,
            "worst_month": 0.0,
            "best_month": 0.0,
            "avg_trades_month": 0.0,
            "long_net": 0.0,
            "short_net": 0.0,
            "avg_bars": 0.0,
        }

    t = trades.copy()
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True)
    t["month"] = t["exit_time"].dt.strftime("%Y-%m")
    t["win"] = t["pnl"] > 0
    net = float(t["pnl"].sum())
    gp = float(t.loc[t["pnl"] > 0, "pnl"].sum())
    gl = float(-t.loc[t["pnl"] < 0, "pnl"].sum())
    pf = gp / gl if gl > 0 else (999.0 if gp > 0 else 0.0)
    eq = t["pnl"].cumsum() + result.start_equity
    dd = float((eq.cummax() - eq).max())
    monthly = t.groupby("month")["pnl"].sum()
    positions = t.groupby("position_id")["pnl"].sum()
    return {
        "trades": int(len(t)),
        "positions": int(t["position_id"].nunique()),
        "net": net,
        "pf": pf,
        "win_rate": float(t["win"].mean() * 100.0),
        "avg_r": float(t["r"].mean()),
        "expectancy": float(t["r"].mean()),
        "max_dd": dd,
        "max_dd_pct": dd / result.start_equity * 100.0,
        "final_equity": float(result.start_equity + net),
        "positive_months": int((monthly > 0).sum()),
        "months": int(len(monthly)),
        "worst_month": float(monthly.min()) if len(monthly) else 0.0,
        "best_month": float(monthly.max()) if len(monthly) else 0.0,
        "avg_trades_month": float(len(t) / max(len(monthly), 1)),
        "long_net": float(t.loc[t["side"] == "long", "pnl"].sum()),
        "short_net": float(t.loc[t["side"] == "short", "pnl"].sum()),
        "avg_bars": float(t["bars_held"].mean()),
        "position_win_rate": float((positions > 0).mean() * 100.0),
        "position_avg": float(positions.mean()),
    }


def slice_trades(trades: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    if trades.empty:
        return trades
    t = trades.copy()
    t["exit_time"] = pd.to_datetime(t["exit_time"], utc=True)
    if start:
        t = t[t["exit_time"] >= pd.Timestamp(start, tz="UTC")]
    if end:
        t = t[t["exit_time"] <= pd.Timestamp(end, tz="UTC")]
    return t
