#!/usr/bin/env python3
"""Lane 2: editable custom hypotheses — add your rules here.

Each function returns a weight panel (date × symbol). Discovery Lab imports
CUSTOM_HYPOTHESES and scores them like any other idea.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def _normalize_rows(w: pd.DataFrame) -> pd.DataFrame:
    w = w.fillna(0.0)
    gross = w.abs().sum(axis=1).replace(0, np.nan)
    return w.div(gross, axis=0).fillna(0.0)


def custom_pressure_reversal(prices: pd.DataFrame, _risk: pd.Series) -> pd.DataFrame:
    """Stretch + volume proxy (range pressure): fade violent range expansion."""
    h = prices.copy()
    for col in prices.columns:
        fp = Path(__file__).resolve().parent / "data" / f"{col}_D1.csv"
        if fp.exists():
            raw = pd.read_csv(fp)
            raw.columns = [c.lower() for c in raw.columns]
            tcol = "time" if "time" in raw.columns else "date"
            raw[tcol] = pd.to_datetime(raw[tcol], utc=True, errors="coerce").dt.tz_convert(None)
            raw = raw.set_index(tcol).sort_index()
            if "high" in raw.columns and "low" in raw.columns:
                h[col] = (raw["high"] - raw["low"]).reindex(prices.index).ffill()
    rets = prices.pct_change()
    rng_z = (h - h.rolling(20).mean()) / h.rolling(20).std()
    z = (prices - prices.rolling(20).mean()) / prices.rolling(20).std()
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for dt in prices.index[25:]:
        for col in prices.columns:
            if rng_z.loc[dt, col] > 1.8 and abs(z.loc[dt, col]) > 1.5:
                sig.loc[dt, col] = -np.sign(z.loc[dt, col])
    return _normalize_rows(sig)


def custom_session_handoff(prices: pd.DataFrame, _risk: pd.Series) -> pd.DataFrame:
    """London open handoff: prior Asia range compression → expansion follow."""
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    rets = prices.pct_change()
    comp = rets.rolling(8).std()
    for dt in prices.index[20:]:
        if dt.hour not in (7, 8, 9):
            continue
        row = comp.loc[dt].dropna()
        if row.empty:
            continue
        tight = row.nsmallest(3).index
        impulse = rets.loc[dt, tight].dropna()
        for col in impulse.index:
            sig.loc[dt, col] = np.sign(impulse[col])
    return _normalize_rows(sig)


CUSTOM_HYPOTHESES = [
    ("custom_pressure_reversal", custom_pressure_reversal),
    ("custom_session_handoff", custom_session_handoff),
]
