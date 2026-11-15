#!/usr/bin/env python3
"""Walk-forward optimization for FXMOPOL (MT5 best-practice mirror).

References (pre-committed protocol — do not tune these after seeing results):
  - Rolling IS/OOS windows (optimize on IS only, chain OOS segments).
  - Sharpe on trade returns as fitness (more stable than raw profit).
  - Small parameter grid (plateau check vs neighbours).
  - Final static hold-out OOS separate from WFO path.

See docs/FXMOPOL_SYMBOL_TRAIN.md
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class WfoProtocol:
    is_months: int = 18
    oos_months: int = 3
    step_months: int = 3
    min_is_trades: int = 12
    min_oos_trades_chain: int = 40
    min_wfe: float = 0.25  # chained OOS Sharpe / median IS Sharpe
    min_oos_pf: float = 1.0
    min_oos_exp_bps: float = 0.0


DEFAULT_PROTOCOL = WfoProtocol()


def trades_to_returns(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    t = pd.to_datetime(trades["exit_time"])
    r = trades["return_pct"].astype(float) / 100.0
    return pd.Series(r.values, index=t).sort_index()


def trade_metrics(r: pd.Series) -> dict:
    if r is None or len(r) == 0:
        return {"n": 0, "exp_bps": -999.0, "pf": 0.0, "sharpe": -999.0, "total_pct": 0.0}
    wins = r[r > 0]
    losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 0.0
    std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    sharpe = (float(r.mean()) / std * np.sqrt(len(r))) if std > 1e-12 else -999.0
    return {
        "n": len(r),
        "exp_bps": round(float(r.mean()) * 10000, 2),
        "pf": round(float(pf), 2),
        "sharpe": round(sharpe, 3),
        "total_pct": round(((1 + r).prod() - 1) * 100, 2),
    }


def window_iterator(
    start: pd.Timestamp,
    end: pd.Timestamp,
    proto: WfoProtocol,
) -> Iterator[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Yield (is_start, is_end, oos_end) — OOS is (is_end, oos_end]."""
    t = pd.Timestamp(start)
    end = pd.Timestamp(end)
    delta_is = pd.DateOffset(months=proto.is_months)
    delta_oos = pd.DateOffset(months=proto.oos_months)
    delta_step = pd.DateOffset(months=proto.step_months)
    while True:
        is_end = t + delta_is
        oos_end = is_end + delta_oos
        if oos_end > end:
            break
        yield t, is_end, oos_end
        t = t + delta_step


def slice_returns(r: pd.Series, a: pd.Timestamp, b: pd.Timestamp) -> pd.Series:
    if r.empty:
        return r
    return r[(r.index >= a) & (r.index < b)]


def walk_forward_chain(
    config_returns: dict[str, pd.Series],
    wf_start: pd.Timestamp,
    wf_end: pd.Timestamp,
    proto: WfoProtocol = DEFAULT_PROTOCOL,
) -> tuple[pd.Series, list[dict]]:
    """Rolling WFO: each window pick best config on IS Sharpe, record OOS returns."""
    oos_parts: list[pd.Series] = []
    windows: list[dict] = []

    for is_start, is_end, oos_end in window_iterator(wf_start, wf_end, proto):
        best_key = None
        best_sharpe = -1e9
        best_is: dict = {}
        for key, series in config_returns.items():
            is_r = slice_returns(series, is_start, is_end)
            m = trade_metrics(is_r)
            if m["n"] < proto.min_is_trades:
                continue
            if m["sharpe"] > best_sharpe:
                best_sharpe = m["sharpe"]
                best_key = key
                best_is = m
        if best_key is None:
            continue
        oos_r = slice_returns(config_returns[best_key], is_end, oos_end)
        if len(oos_r):
            oos_parts.append(oos_r)
        windows.append(
            {
                "is_start": str(is_start.date()),
                "is_end": str(is_end.date()),
                "oos_end": str(oos_end.date()),
                "pick": best_key,
                "is_sharpe": best_is.get("sharpe"),
                "is_n": best_is.get("n"),
                "oos_n": len(oos_r),
                "oos_exp_bps": trade_metrics(oos_r).get("exp_bps"),
            }
        )

    if not oos_parts:
        return pd.Series(dtype=float), windows
    chained = pd.concat(oos_parts).sort_index()
    return chained, windows


def plateau_ok(
    config_returns: dict[str, pd.Series],
    pick_key: str,
    val_start: pd.Timestamp,
    val_end: pd.Timestamp,
) -> bool:
    """Neighbour stability: best config Sharpe not >2x median neighbour on validation."""
    if pick_key not in config_returns:
        return False
    val_r = slice_returns(config_returns[pick_key], val_start, val_end)
    pick_sh = trade_metrics(val_r)["sharpe"]
    neigh = []
    parts = pick_key.split("_")
    for key in config_returns:
        if key == pick_key:
            continue
        if key.split("_")[:2] == parts[:2]:
            m = trade_metrics(slice_returns(config_returns[key], val_start, val_end))
            if m["n"] >= 8:
                neigh.append(m["sharpe"])
    if not neigh:
        return True
    med = float(np.median(neigh))
    if med <= 0:
        return pick_sh > 0
    return pick_sh < 2.5 * med


def config_key(cfg: dict) -> str:
    rdi = int(cfg.get("rsi_divergence", False))
    sr = int(cfg.get("sr_filter", False))
    fs = int(cfg.get("fund_sentiment", False))
    return (
        f"e{cfg['ema_mid']}_p{int(cfg['proximity'])}_s{int(cfg['strict_stayaway'])}"
        f"_c{cfg['cost_bps']:.0f}_r{rdi}_sr{sr}_f{fs}"
    )
