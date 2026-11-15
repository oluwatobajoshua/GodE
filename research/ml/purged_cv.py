#!/usr/bin/env python3
"""Purged cross-validation — remove train rows whose label window touches test."""
from __future__ import annotations

import numpy as np
import pandas as pd


def purge_train_mask(
    index: pd.DatetimeIndex | np.ndarray,
    test_start: int,
    test_end: int,
    label_window: int,
) -> np.ndarray:
    """Boolean mask: True = keep row for training (index positions).

    Purges any training index ``i`` if ``[i, i + label_window)`` overlaps
    ``[test_start, test_end)`` (label spans into test — serial correlation cheat).
    """
    n = len(index)
    test_start = int(test_start)
    test_end = int(test_end)
    label_window = max(1, int(label_window))
    mask = np.ones(n, dtype=bool)
    mask[test_start:test_end] = False
    purge_lo = max(0, test_start - label_window)
    purge_hi = min(n, test_end + label_window)
    mask[purge_lo:test_start] = False
    mask[test_end:purge_hi] = False
    return mask


def purged_splits(
    n: int,
    n_folds: int,
    label_window: int,
    embargo: int = 0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window purged folds on integer positions ``0..n-1``."""
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    fold_size = max(n // (n_folds + 1), label_window + 10)
    for k in range(1, n_folds + 1):
        test_start = k * fold_size
        test_end = min(test_start + fold_size, n)
        if test_end - test_start < label_window + 5:
            break
        train_mask = purge_train_mask(np.arange(n), test_start, test_end, label_window)
        if embargo > 0:
            lo = max(0, test_start - embargo)
            hi = min(n, test_end + embargo)
            train_mask[lo:hi] = False
        train_idx = np.flatnonzero(train_mask)
        test_idx = np.arange(test_start, test_end)
        if len(train_idx) < 100 or len(test_idx) < 20:
            continue
        folds.append((train_idx, test_idx))
    return folds
