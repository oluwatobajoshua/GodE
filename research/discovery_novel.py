#!/usr/bin/env python3
"""Novel / contrarian FX hypotheses for Discovery Lab.

Ideas here deliberately avoid textbook indicator stacks. They trade:
  - crowding vs the consensus basket,
  - cross-pair dispersion and entropy,
  - lead-lag propagation shocks,
  - disagreement between a pair and the G10 basket.

Each returns a weight panel (date x symbol) in [-1, 1], dollar-neutral when active.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _normalize_rows(w: pd.DataFrame) -> pd.DataFrame:
    w = w.fillna(0.0)
    gross = w.abs().sum(axis=1).replace(0, np.nan)
    return w.div(gross, axis=0).fillna(0.0)


def usd_signed_return(prices: pd.DataFrame) -> pd.DataFrame:
    """Approximate local-currency return vs USD for each pair."""
    rets = prices.pct_change()
    out = rets.copy()
    for col in prices.columns:
        if col.startswith("USD") and col != "USD":
            out[col] = -rets[col]
    return out


def nov_crowding_fade_usd(prices: pd.DataFrame, *, window: int = 252, q: float = 0.92) -> pd.DataFrame:
    """When almost all G10 legs agree USD is up/down, fade the crowd next day."""
    u = usd_signed_return(prices)
    consensus = np.sign(u).mean(axis=1)
    strength = consensus.abs()
    thresh = strength.rolling(window, min_periods=60).quantile(q)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    active = strength >= thresh
    for dt in prices.index[window:]:
        if not active.loc[dt]:
            continue
        direction = -np.sign(consensus.loc[dt])
        for col in prices.columns:
            base = 1.0 if not col.startswith("USD") or col in ("EURUSD", "GBPUSD") else -1.0
            if col.startswith("USD") and col not in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
                base = -1.0
            if col in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"):
                base = 1.0
            sig.loc[dt, col] = direction * base
    return _normalize_rows(sig)


def nov_dispersion_mr(prices: pd.DataFrame, *, window: int = 120, q: float = 0.90) -> pd.DataFrame:
    """After extreme cross-sectional return dispersion, bet on compression."""
    u = usd_signed_return(prices)
    disp = u.std(axis=1)
    thresh = disp.rolling(window, min_periods=40).quantile(q)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for i in range(1, len(prices)):
        dt = prices.index[i]
        prev = prices.index[i - 1]
        if disp.loc[prev] < thresh.loc[prev]:
            continue
        last = u.loc[prev].dropna()
        if len(last) < 4:
            continue
        winners = last.nlargest(2).index
        losers = last.nsmallest(2).index
        for c in losers:
            sig.loc[dt, c] = 1.0
        for c in winners:
            sig.loc[dt, c] = -1.0
    return _normalize_rows(sig)


def nov_entropy_contrarian(prices: pd.DataFrame, *, window: int = 180, low_q: float = 0.15) -> pd.DataFrame:
    """Low entropy of return signs = one-sided market → contrarian basket."""
    u = usd_signed_return(prices)
    signs = np.sign(u)

    def _entropy(row: pd.Series) -> float:
        row = row.dropna()
        if len(row) == 0:
            return np.nan
        p = (row > 0).mean()
        p = min(max(p, 1e-6), 1 - 1e-6)
        return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))

    ent = signs.apply(_entropy, axis=1)
    thresh = ent.rolling(window, min_periods=50).quantile(low_q)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    crowd = signs.mean(axis=1)
    for dt in prices.index[window:]:
        if ent.loc[dt] > thresh.loc[dt]:
            continue
        direction = -np.sign(crowd.loc[dt])
        if direction == 0:
            continue
        for col in prices.columns:
            r = u.loc[dt, col] if dt in u.index else 0.0
            sig.loc[dt, col] = -np.sign(r) if not np.isnan(r) else 0.0
        row = sig.loc[dt]
        if row.abs().sum() > 0:
            sig.loc[dt] = row / row.abs().sum() * direction
    return _normalize_rows(sig)


def nov_lead_lag_eurusd(prices: pd.DataFrame, *, beta_window: int = 80, shock_z: float = 1.8) -> pd.DataFrame:
    """EURUSD shock → next-day follower basket using rolling betas."""
    if "EURUSD" not in prices.columns:
        return pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    rets = prices.pct_change()
    lead = rets["EURUSD"]
    z = (lead - lead.rolling(60).mean()) / lead.rolling(60).std()
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for i in range(beta_window + 1, len(prices) - 1):
        dt = prices.index[i]
        if abs(z.iloc[i - 1]) < shock_z:
            continue
        train = rets.iloc[i - beta_window : i]
        betas = train.corrwith(train["EURUSD"]).drop("EURUSD", errors="ignore")
        shock_dir = np.sign(z.iloc[i - 1])
        for col, b in betas.items():
            if col == "EURUSD":
                continue
            sig.loc[dt, col] = shock_dir * np.sign(b)
    return _normalize_rows(sig)


def nov_basket_disagreement(prices: pd.DataFrame, *, window: int = 40, z: float = 2.0) -> pd.DataFrame:
    """Pair moves opposite basket z-score extreme → bet on realignment."""
    rets = prices.pct_change()
    basket = rets.mean(axis=1)
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for col in prices.columns:
        spread = rets[col] - basket
        sd = spread.rolling(window, min_periods=20).std()
        score = spread / sd.replace(0, np.nan)
        for i in range(window, len(prices)):
            dt = prices.index[i]
            if abs(score.iloc[i - 1]) < z:
                continue
            sig.loc[dt, col] = -np.sign(score.iloc[i - 1])
    return _normalize_rows(sig)


def nov_vov_defensive_carry(prices: pd.DataFrame, *, vol_w: int = 20, vov_w: int = 60) -> pd.DataFrame:
    """Vol-of-vol spike → only lowest-realized-vol legs; else flat."""
    rets = prices.pct_change()
    port_vol = rets.std(axis=1).rolling(vol_w).mean()
    vov = port_vol.rolling(vov_w).std()
    vov_q = vov.rolling(252, min_periods=60).quantile(0.75)
    realized = rets.rolling(vol_w).std()
    sig = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
    for dt in prices.index[252:]:
        if vov.loc[dt] < vov_q.loc[dt]:
            continue
        row = realized.loc[dt].dropna().nsmallest(3)
        for c in row.index:
            sig.loc[dt, c] = 1.0 / len(row)
    return _normalize_rows(sig)


NOVEL_HYPOTHESES = [
    ("nov_crowding_fade_usd", lambda p, _r: nov_crowding_fade_usd(p)),
    ("nov_dispersion_mr", lambda p, _r: nov_dispersion_mr(p)),
    ("nov_entropy_contrarian", lambda p, _r: nov_entropy_contrarian(p)),
    ("nov_lead_lag_eurusd", lambda p, _r: nov_lead_lag_eurusd(p)),
    ("nov_basket_disagreement", lambda p, _r: nov_basket_disagreement(p)),
    ("nov_vov_defensive", lambda p, _r: nov_vov_defensive_carry(p)),
]
