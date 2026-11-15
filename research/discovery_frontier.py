#!/usr/bin/env python3
"""Frontier FX hypotheses — structural / meta-market, not retail indicator stacks.

These target *market ecology* (crowding, topology, phase, silence) rather than
"buy when RSI says so." Each idea is testable; novelty ≠ edge until OOS passes.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _normalize_rows(w: pd.DataFrame) -> pd.DataFrame:
    w = w.fillna(0.0)
    gross = w.abs().sum(axis=1).replace(0, np.nan)
    return w.div(gross, axis=0).fillna(0.0)


def usd_signed_return(prices: pd.DataFrame) -> pd.DataFrame:
    rets = prices.pct_change()
    out = rets.copy()
    for col in prices.columns:
        if col.startswith("USD") and col not in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
            out[col] = -rets[col]
    return out


def front_graph_laplacian_residual(prices: pd.DataFrame, *, corr_w: int = 80) -> pd.DataFrame:
    """Currency network: rolling correlation graph Laplacian × return vector.

    Trade against large Laplacian residuals (stress not explained by neighbors).
    """
    rets = usd_signed_return(prices)
    cols = list(prices.columns)
    n = len(cols)
    sig = pd.DataFrame(0.0, index=prices.index, columns=cols)
    for i in range(corr_w + 1, len(prices)):
        dt = prices.index[i]
        window = rets.iloc[i - corr_w : i]
        c = window.corr().fillna(0.0).to_numpy().copy()
        np.fill_diagonal(c, 1.0)
        d = np.diag(c.sum(axis=1))
        lap = d - c
        r = window.iloc[-1].reindex(cols).fillna(0.0).values
        resid = r - lap @ np.linalg.lstsq(lap + 1e-6 * np.eye(n), r, rcond=None)[0]
        z = resid / (np.std(resid) + 1e-9)
        for j, col in enumerate(cols):
            if abs(z[j]) > 1.25:
                sig.loc[dt, col] = -np.sign(z[j])
    return _normalize_rows(sig)


def front_phase_desync(prices: pd.DataFrame, *, window: int = 40) -> pd.DataFrame:
    """Hilbert phase dispersion across pairs — trade pairs lagging the pack."""
    from numpy.fft import rfft, irfft

    rets = usd_signed_return(prices)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for i in range(window + 5, len(prices)):
        dt = prices.index[i]
        phases = {}
        for col in prices.columns:
            x = rets[col].iloc[i - window : i].fillna(0.0).values
            if len(x) < window:
                continue
            n = len(x)
            h = irfft(rfft(x) * 1j * np.sign(np.arange(n // 2 + 1)), n)
            phases[col] = np.angle(h[-1] + 1j * x[-1])
        if len(phases) < 4:
            continue
        pv = np.array(list(phases.values()))
        med = np.median(pv)
        for col, ph in phases.items():
            diff = np.angle(np.exp(1j * (ph - med)))
            if abs(diff) > 1.2:
                sig.loc[dt, col] = -np.sign(diff)
    return _normalize_rows(sig)


def front_silence_breakout(prices: pd.DataFrame, *, vol_w: int = 10, disp_w: int = 5) -> pd.DataFrame:
    """Vol collapses while cross-pair dispersion stays high → next-day expansion bias."""
    rets = usd_signed_return(prices)
    port_vol = rets.std(axis=1).rolling(vol_w).mean()
    disp = rets.rolling(disp_w).std().mean(axis=1)
    vol_rank = port_vol.rolling(120, min_periods=40).rank(pct=True)
    disp_rank = disp.rolling(120, min_periods=40).rank(pct=True)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for i in range(121, len(prices)):
        dt = prices.index[i]
        if vol_rank.iloc[i - 1] > 0.25 or disp_rank.iloc[i - 1] < 0.70:
            continue
        yday = rets.iloc[i - 1]
        leaders = yday.nlargest(2).index
        laggards = yday.nsmallest(2).index
        for c in leaders:
            sig.loc[dt, c] += 0.5
        for c in laggards:
            sig.loc[dt, c] -= 0.5
    return _normalize_rows(sig)


def front_dual_consensus_fracture(prices: pd.DataFrame, prices_risk: pd.Series) -> pd.DataFrame:
    """Technical crowd (mom) vs fundamental proxy (low-vol rank) maximally disagree."""
    rets = prices.pct_change()
    mom = prices / prices.shift(63) - 1
    tech = np.sign(mom).mean(axis=1)
    lowvol = rets.rolling(20).std().rank(axis=1, pct=True)
    fund = (lowvol < 0.33).astype(float).mean(axis=1) - (lowvol > 0.66).astype(float).mean(axis=1)
    fracture = (np.sign(tech) * np.sign(fund) < 0).astype(float)
    strength = (tech.abs() + fund.abs()) / 2.0
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    q = strength.rolling(180, min_periods=50).quantile(0.85)
    for dt in prices.index[180:]:
        if fracture.loc[dt] < 0.5 or strength.loc[dt] < q.loc[dt]:
            continue
        # Trust slow vol structure, fade fast momentum crowd
        for col in prices.columns:
            sig.loc[dt, col] = np.sign(fund.loc[dt]) * (0.5 - lowvol.loc[dt, col])
    return _normalize_rows(sig)


def front_rank_velocity_carry(prices: pd.DataFrame) -> pd.DataFrame:
    """Speed of change in carry-rank (swap proxy vol rank), not level."""
    rets = prices.pct_change()
    rank = rets.rolling(60).std().rank(axis=1, ascending=True)
    velocity = rank.diff(5)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for dt in prices.index[70:]:
        row = velocity.loc[dt].dropna()
        if row.empty:
            continue
        fast_up = row.nlargest(2).index
        fast_dn = row.nsmallest(2).index
        for c in fast_up:
            sig.loc[dt, c] = 1.0
        for c in fast_dn:
            sig.loc[dt, c] = -1.0
    return _normalize_rows(sig)


FRONTIER_HYPOTHESES = [
    ("front_graph_laplacian", lambda p, r: front_graph_laplacian_residual(p)),
    ("front_phase_desync", lambda p, r: front_phase_desync(p)),
    ("front_silence_breakout", lambda p, r: front_silence_breakout(p)),
    ("front_dual_consensus_fracture", lambda p, r: front_dual_consensus_fracture(p, r)),
    ("front_rank_velocity", lambda p, r: front_rank_velocity_carry(p)),
]
