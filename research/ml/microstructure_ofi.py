#!/usr/bin/env python3
"""Order-Flow Imbalance (OFI) on Level-2 snapshots — NumPy vector path.

Cont-style top-of-book OFI from sequences of (bid_p, bid_v, ask_p, ask_v).
When only M5 OHLCV+spread exists, `synthetic_book_from_bars()` builds a research proxy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ofi_top_of_book(
    bid_p: np.ndarray,
    bid_v: np.ndarray,
    ask_p: np.ndarray,
    ask_v: np.ndarray,
) -> np.ndarray:
    """Net supply/demand pressure at BBO. Shape (n-1,) aligned to index 1..n-1."""
    n = len(bid_p)
    if n < 2:
        return np.array([], dtype=np.float64)
    dp_b = bid_p[1:] - bid_p[:-1]
    dp_a = ask_p[1:] - ask_p[:-1]
    vb0, vb1 = bid_v[:-1], bid_v[1:]
    va0, va1 = ask_v[:-1], ask_v[1:]

    bid_term = np.where(dp_b > 0, vb1, np.where(dp_b < 0, vb0, vb1 - vb0))
    ask_term = np.where(dp_a < 0, va1, np.where(dp_a > 0, va0, va1 - va0))
    return bid_term.astype(np.float64) - ask_term.astype(np.float64)


def ofi_multilevel(
    bid_p: np.ndarray,  # (n, depth)
    bid_v: np.ndarray,
    ask_p: np.ndarray,
    ask_v: np.ndarray,
) -> np.ndarray:
    """Sum OFI across book depths (vectorized per level, then sum)."""
    depth = bid_p.shape[1] if bid_p.ndim == 2 else 1
    if bid_p.ndim == 1:
        return ofi_top_of_book(bid_p, bid_v, ask_p, ask_v)
    total = np.zeros(len(bid_p) - 1, dtype=np.float64)
    for k in range(depth):
        total += ofi_top_of_book(bid_p[:, k], bid_v[:, k], ask_p[:, k], ask_v[:, k])
    return total


def synthetic_book_from_bars(df: pd.DataFrame, depth: int = 3) -> dict[str, np.ndarray]:
    """Research proxy until true L2: mid=close, half-spread from MT5 points, depth from volume."""
    c = df["close"].to_numpy(dtype=np.float64)
    sp = df["spread"].to_numpy(dtype=np.float64) if "spread" in df.columns else np.ones(len(c))
    vol = df["tick_volume"].to_numpy(dtype=np.float64) if "tick_volume" in df.columns else np.ones(len(c))
    pip = 1e-4
    half = np.maximum(sp * pip * 0.5, pip * 0.5)
    bid1 = c - half
    ask1 = c + half
    # Deeper levels: ladder with geometric volume decay
    bid_p = np.column_stack([bid1 - k * pip for k in range(depth)])
    ask_p = np.column_stack([ask1 + k * pip for k in range(depth)])
    share = np.array([0.5, 0.3, 0.2][:depth], dtype=np.float64)
    bid_v = np.outer(vol, share)
    ask_v = np.outer(vol, share)
    return {"bid_p": bid_p, "bid_v": bid_v, "ask_p": ask_p, "ask_v": ask_v, "mid": c}


def vpin_proxy(
    buy_vol: np.ndarray,
    sell_vol: np.ndarray,
    window: int = 50,
) -> np.ndarray:
    """Volume-synchronized toxicity proxy: |buy-sell| / (buy+sell) rolling."""
    buy_vol = np.asarray(buy_vol, dtype=np.float64)
    sell_vol = np.asarray(sell_vol, dtype=np.float64)
    b = pd.Series(buy_vol).rolling(window, min_periods=window).sum().to_numpy()
    s = pd.Series(sell_vol).rolling(window, min_periods=window).sum().to_numpy()
    denom = b + s + 1e-9
    return np.abs(b - s) / denom
