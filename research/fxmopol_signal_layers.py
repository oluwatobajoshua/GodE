#!/usr/bin/env python3
"""FXMOPOL signal stack — price action, S/R, TA overlays, fundamental + sentiment.

Used by research/fxmopol_playbook.py and walk-forward training so the EA gate
tests the same pillars as live TrafficLight (macro/sentiment) plus chart rules.

Pillars:
  1) Price action — playbook pullbacks, stop-hunt wicks, EMA21 crosses (fxmopol_playbook)
  2) Support / resistance — swing hi/lo, 50–61.8% fib, prior-day pivot S1/R1 (BabyPips)
  3) Technical — EMA21/50/SMMA100 stack, RSI(9), RSI divergence (fxmopol_chart_core)
  4) Fundamental — FRED policy-rate differential (carry bias) under research/data/rates/
  5) Sentiment — VIX level / 20-session change (risk-off filter)

All macro features are causal (monthly rates and daily VIX forward-filled to H1).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RATES = HERE / "data" / "rates"

CCY_MAP = {
    "EUR": "EZ",
    "GBP": "GB",
    "USD": "US",
    "JPY": "JP",
    "AUD": "AU",
    "CAD": "CA",
    "CHF": "CH",
    "NZD": "NZ",
}

SWING_BARS = 20
SR_ATR_TOL = 0.35


@lru_cache(maxsize=32)
def _load_rate_series(ccy_code: str) -> pd.Series:
    p = RATES / f"{ccy_code}.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p)
    col = [c for c in df.columns if c != "observation_date"][0]
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    s = pd.Series(df[col].astype(float).values, index=df["observation_date"]).sort_index()
    return s


@lru_cache(maxsize=1)
def _load_vix() -> pd.Series:
    p = RATES / "VIX.csv"
    if not p.exists():
        return pd.Series(dtype=float)
    df = pd.read_csv(p)
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    s = pd.Series(df["VIXCLS"].astype(float).values, index=df["observation_date"]).sort_index()
    return s


def parse_symbol(symbol: str) -> tuple[str, str]:
    sym = symbol.upper().replace(".", "")
    if len(sym) >= 6:
        return sym[:3], sym[3:6]
    return sym[:3], "USD"


def prep_sr_columns(h1: pd.DataFrame) -> dict[str, pd.Series]:
    """Swing S/R, fib retrace zone, daily pivot S1/R1 aligned to H1 index."""
    h = h1["high"].astype(float)
    l = h1["low"].astype(float)
    c = h1["close"].astype(float)
    sh = h.rolling(SWING_BARS).max().shift(1)
    sl = l.rolling(SWING_BARS).min().shift(1)
    fib50 = sl + (sh - sl) * 0.5
    fib618 = sl + (sh - sl) * 0.618

    d1 = h1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    p = (d1["high"] + d1["low"] + d1["close"]) / 3.0
    s1 = (2 * p - d1["high"]).shift(1)
    r1 = (2 * p - d1["low"]).shift(1)
    s1_h1 = s1.reindex(c.index, method="ffill")
    r1_h1 = r1.reindex(c.index, method="ffill")

    return {"sh": sh, "sl": sl, "fib50": fib50, "fib618": fib618, "s1": s1_h1, "r1": r1_h1}


def sr_long_ok(
    i: int,
    lo: float,
    cls: float,
    atr: float,
    sh: float,
    sl: float,
    fib618: float,
    s1: float,
) -> bool:
    if np.isnan(atr) or atr <= 0:
        return False
    tol = atr * SR_ATR_TOL
    at_fib = not np.isnan(fib618) and lo >= fib618 and lo <= sh * 1.001
    at_pivot = not np.isnan(s1) and abs(lo - s1) < tol
    at_swing = not np.isnan(sl) and abs(lo - sl) < tol
    return bool(at_fib or at_pivot or at_swing or cls > sl)


def sr_short_ok(
    i: int,
    hi: float,
    cls: float,
    atr: float,
    sh: float,
    sl: float,
    fib50: float,
    r1: float,
) -> bool:
    if np.isnan(atr) or atr <= 0:
        return False
    tol = atr * SR_ATR_TOL
    at_fib = not np.isnan(fib50) and hi <= fib50 and hi >= sl * 0.999
    at_pivot = not np.isnan(r1) and abs(hi - r1) < tol
    at_swing = not np.isnan(sh) and abs(hi - sh) < tol
    return bool(at_fib or at_pivot or at_swing or cls < sh)


def _ffill_to_h1(monthly_or_daily: pd.Series, h1_index: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill a coarse series onto H1 bars (handles duplicate H1 timestamps)."""
    day_idx = pd.DatetimeIndex(h1_index).normalize()
    coarse = monthly_or_daily.reindex(day_idx.unique(), method="ffill")
    return pd.Series(coarse.loc[day_idx].to_numpy(), index=h1_index)


def prep_macro_series(h1_index: pd.DatetimeIndex, symbol: str) -> tuple[pd.Series, pd.Series]:
    """Carry diff (base − quote rate) and VIX 20d change, aligned to H1."""
    base, quote = parse_symbol(symbol)
    b_code = CCY_MAP.get(base, "")
    q_code = CCY_MAP.get(quote, "")
    carry = pd.Series(np.nan, index=h1_index)
    if b_code and q_code:
        br = _load_rate_series(b_code)
        qr = _load_rate_series(q_code)
        if len(br) and len(qr):
            diff = br - qr
            carry = _ffill_to_h1(diff, h1_index)
    vix = _load_vix()
    vix_chg = pd.Series(np.nan, index=h1_index)
    if len(vix):
        vix_h1 = _ffill_to_h1(vix, h1_index)
        vix_chg = (vix_h1 - vix_h1.shift(20)) / vix_h1.shift(20).replace(0, np.nan)
    return carry, vix_chg


def carry_allows(side: int, carry: float) -> bool:
    if np.isnan(carry):
        return True
    # Long base vs quote: prefer non-negative carry differential
    if side > 0:
        return carry >= -0.35
    return carry <= 0.35


def sentiment_allows(side: int, vix_chg: float) -> bool:
    if np.isnan(vix_chg):
        return True
    # Risk-off spike: avoid new longs in risk FX; avoid new shorts if vol collapsing
    if side > 0 and vix_chg > 0.12:
        return False
    if side < 0 and vix_chg < -0.12:
        return False
    return True


def fund_sentiment_allows(side: int, carry: float, vix_chg: float) -> bool:
    return carry_allows(side, carry) and sentiment_allows(side, vix_chg)


def layers_available(symbol: str) -> dict[str, bool]:
    base, quote = parse_symbol(symbol)
    has_rates = (
        (RATES / f"{CCY_MAP.get(base, 'XX')}.csv").exists()
        and (RATES / f"{CCY_MAP.get(quote, 'XX')}.csv").exists()
    )
    has_vix = (RATES / "VIX.csv").exists()
    return {"sr": True, "rates": has_rates, "vix": has_vix, "fund_stack": has_rates and has_vix}
