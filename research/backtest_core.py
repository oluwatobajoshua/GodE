#!/usr/bin/env python3
"""One backtest contract for the whole project.

Every research script must emit per-bar equity returns where
``(1 + ret).cumprod() - 1`` equals the account return. This repo broke that rule
in three places by storing ``(close[i] - entry_price)`` - the *cumulative* gain
since entry - in the return series. The same gain was then counted once for
every bar the trade stayed open, which inflated Sharpe to 5-11, crushed
drawdown to ~0 and made a losing rule look like a 6% CAGR machine. See
``docs/HANDOFF.md`` (MA-compression retraction).

Use ``simulate_stops`` for an ATR stop/TP position series. Call
``validate_returns`` on any series you build yourself.

Run the self-test:
    .\\.venv\\Scripts\\python.exe research\\backtest_core.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = np.maximum(high - low,
                    np.maximum(abs(high - prev_close), abs(low - prev_close)))
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def point_size(symbol: str) -> float:
    return 0.001 if symbol.upper().endswith("JPY") else 0.00001


def cost_per_side(df: pd.DataFrame, symbol: str, comm_pts: float = 7.0,
                  slip_pts: float = 1.0, floor_pts: float = 1.0) -> np.ndarray:
    """All-in cost per side in price units: spread/2 + slippage + half commission.

    The quoted spread in ``research/data/mt5/*.csv`` is sparse (median 0 on
    EURUSD), so it is floored before it is believed.
    """
    spread_eff = np.maximum(df["spread"].to_numpy(float), floor_pts)
    return (spread_eff / 2.0 + slip_pts + comm_pts / 2.0) * point_size(symbol)


def simulate_stops(df: pd.DataFrame, pos: pd.Series, symbol: str,
                   comm_pts: float = 7.0, slip_pts: float = 1.0,
                   floor_pts: float = 1.0, stop_atr: float = 1.5,
                   tp_atr: float = 2.0) -> tuple:
    """Backtest an ATR stop/TP position series.

    ``pos[t]`` is the desired side for bar t+1. Entry and exit both happen at
    the bar close, so ``ret[i]`` is the return earned by the position held
    *into* bar i. Cost is charged on every change of side.

    Returns ``(per_bar_returns, per_trade_returns, trade_exit_indices)``.
    """
    df = df.copy()
    atr = wilder_atr(df, 14)
    close = df["close"].to_numpy(float)
    n = len(df)
    cost_side = cost_per_side(df, symbol, comm_pts, slip_pts, floor_pts)

    rets = np.zeros(n)
    trades: list[float] = []
    trade_exit: list[int] = []

    side = 0
    stop_price = 0.0
    tp_price = 0.0
    trade_ret = 0.0

    for i in range(1, n):
        sig = int(pos.iloc[i - 1])
        c = close[i]
        a = atr.iloc[i]

        #--- mark the position that was held into this bar
        bar_ret = side * (c / close[i - 1] - 1.0) if side != 0 else 0.0
        rets[i] = bar_ret
        trade_ret += bar_ret

        exit_now = False
        if side > 0:
            exit_now = c <= stop_price or c >= tp_price or sig <= 0
        elif side < 0:
            exit_now = c >= stop_price or c <= tp_price or sig >= 0

        if exit_now:
            cost = cost_side[i] / c
            rets[i] -= cost
            trade_ret -= cost
            trades.append(trade_ret)
            trade_exit.append(i)
            side = 0
            trade_ret = 0.0

        if side == 0 and sig != 0:
            entry_atr = a if a > 0 else 0.001
            stop_dist = entry_atr * stop_atr
            if sig > 0:
                stop_price = c - stop_dist
                tp_price = c + stop_dist * tp_atr
            else:
                stop_price = c + stop_dist
                tp_price = c - stop_dist * tp_atr
            side = sig
            cost = cost_side[i] / c
            rets[i] -= cost
            trade_ret = -cost

    if side != 0:
        trades.append(trade_ret)
        trade_exit.append(n - 1)

    return (pd.Series(rets, index=df["time"]),
            np.asarray(trades, dtype=float),
            np.asarray(trade_exit, dtype=int))


def trade_metrics(trades: np.ndarray) -> dict:
    """Trade-level profit factor and win rate.

    Summing per-bar marks and calling the ratio a profit factor is not a
    profit factor - it counts one trade many times.
    """
    trades = np.asarray(trades, dtype=float)
    if trades.size == 0:
        return {"pf": None, "winrate": 0.0, "trades": 0}
    wins = float(trades[trades > 0].sum())
    losses = float(trades[trades < 0].sum())
    return {
        "pf": round(wins / abs(losses), 2) if losses < 0 else None,
        "winrate": round(float((trades > 0).mean() * 100), 1),
        "trades": int(trades.size),
    }


def validate_returns(rets, close, side_held=None, label: str = "returns",
                     max_bad_frac: float = 0.01) -> None:
    """Raise if a return series looks cumulative rather than per-bar.

    A cumulative-since-entry series repeats the same gain on consecutive bars
    and routinely exceeds the underlying bar move. A genuine per-bar return
    cannot exceed the move of the bar it belongs to (plus its cost).
    """
    r = np.abs(np.asarray(rets, dtype=float))
    c = np.asarray(close, dtype=float)
    bar_move = np.zeros_like(c)
    bar_move[1:] = np.abs(np.diff(c) / c[:-1])

    if side_held is not None:
        held = np.abs(np.asarray(side_held, dtype=float)) > 0
    else:
        held = r > 0

    # generous allowance: 3x the bar move, or 20 bp where nothing moved
    limit = np.where(held, bar_move * 3.0 + 2e-3, 2e-3)
    bad = int((r > limit).sum())
    if bad > max(3, int(max_bad_frac * len(r))):
        raise ValueError(
            f"{label}: {bad} of {len(r)} bars exceed the bar's own move - the "
            f"series is probably cumulative-since-entry, not per-bar")


def _selftest() -> int:
    rng = np.random.default_rng(0)
    n = 800
    price = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    df = pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=n, freq="h"),
        "open": price,
        "high": price * 1.0002,
        "low": price * 0.9998,
        "close": price,
        "spread": np.full(n, 1.0),
    })

    # hold a constant long with stops far away: must reproduce buy-and-hold
    pos = pd.Series(1, index=df.index)
    rets, trades, tix = simulate_stops(df, pos, "EURUSD",
                                       stop_atr=1e6, tp_atr=1e6)
    total = float((1 + rets.to_numpy()).prod() - 1)
    gross = price[-1] / price[1] - 1
    entry_cost = cost_per_side(df, "EURUSD")[1] / price[1]
    expect = (1 - entry_cost) * (1 + gross) - 1
    ok = abs(total - expect) < 1e-9

    print(f"selftest hold: total {total:+.8f}  expect {expect:+.8f}  "
          f"{'OK' if ok else 'FAIL'}")

    validate_returns(rets, price, np.ones(n), label="hold")

    # the old bug: cumulative-since-entry must be rejected
    cum = np.zeros(n)
    for i in range(1, n):
        cum[i] = price[i] / price[1] - 1.0
    try:
        validate_returns(cum, price, np.ones(n), label="cumulative")
        print("selftest cumulative: NOT CAUGHT  FAIL")
        ok = False
    except ValueError:
        print("selftest cumulative: caught  OK")

    print(f"trades recorded: {len(trades)} (expect 1)  "
          f"{'OK' if len(trades) == 1 else 'FAIL'}")
    ok = ok and len(trades) == 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
