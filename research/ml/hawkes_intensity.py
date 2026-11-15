#!/usr/bin/env python3
"""Discrete Hawkes self-exciting intensity — NumPy (no torch)."""
from __future__ import annotations

import numpy as np


def hawkes_intensity_series(
    event_times: np.ndarray,
    t_grid: np.ndarray,
    *,
    mu: float = 0.1,
    alpha: float = 0.4,
    beta: float = 1.0,
) -> np.ndarray:
    """λ(t) = μ + Σ α exp(-β (t - t_i)) for events t_i < t.

    event_times: float array of event epochs (seconds or bar index)
    t_grid: evaluation times, same units
    """
    event_times = np.sort(event_times.astype(np.float64))
    t_grid = t_grid.astype(np.float64)
    lam = np.full(len(t_grid), mu, dtype=np.float64)
    if len(event_times) == 0:
        return lam
    # O(T * E) — fine for research bar grids; swap to C++ for tick firehose
    for i, t in enumerate(t_grid):
        past = event_times[event_times < t]
        if past.size:
            lam[i] += float(np.sum(alpha * np.exp(-beta * (t - past))))
    return lam


def events_from_returns(returns: np.ndarray, threshold: float = 2.5) -> np.ndarray:
    """Mark bar indices where |z-score return| exceeds threshold as 'bursts'."""
    r = returns.astype(np.float64)
    sd = np.nanstd(r) + 1e-12
    z = np.abs(r) / sd
    return np.flatnonzero(z >= threshold).astype(np.float64)
