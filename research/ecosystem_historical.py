#!/usr/bin/env python3
"""Causal professor-style gates at an H1 timestamp (research backtest path)."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "research"))

from ecosystem_pillars import (  # noqa: E402
    cot_positioning,
    event_risk_usd,
    fxmacro_risk_sentiment,
)
from ecosystem_professor import _professor_verdict, _session_flags  # noqa: E402
from fxmopol_market_pulse import analyze_market_pulse  # noqa: E402
from fxmopol_playbook import build_d1_gate  # noqa: E402
from fxmopol_signal_layers import fund_sentiment_allows, prep_macro_series  # noqa: E402

CHOP_STATES = frozenset({"CHOP_SMMA", "CHOP_MIXED", "FLAT_NEUTRAL", "CHOP"})


def prep_liquidity_series(h1: pd.DataFrame) -> pd.Series:
    sp = pd.to_numeric(h1.get("spread", pd.Series(dtype=float)), errors="coerce")
    if len(sp) < 20:
        return pd.Series(np.nan, index=h1.index)
    rank = sp.rolling(168, min_periods=48).apply(
        lambda x: float(x.rank(pct=True).iloc[-1]) if len(x.dropna()) else np.nan,
        raw=False,
    )
    return rank


def prep_vol_elevated(h1: pd.DataFrame) -> pd.Series:
    c = h1["close"].astype(float)
    h, l = h1["high"].astype(float), h1["low"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    rank = atr.rolling(200, min_periods=80).apply(lambda x: float(x.rank(pct=True).iloc[-1]), raw=False)
    return (rank > 0.8).fillna(False)


def gate_at_bar(
    h1: pd.DataFrame,
    bar_i: int,
    symbol: str,
    side: int,
    *,
    ema_mid: int = 50,
    carry_s: pd.Series | None = None,
    vix_chg_s: pd.Series | None = None,
    spread_pctile: pd.Series | None = None,
    vol_elevated: pd.Series | None = None,
    chart_state: str | None = None,
) -> dict:
    """Mirror EA professor gates at bar index (no lookahead)."""
    t = h1.index[bar_i]
    if carry_s is None or vix_chg_s is None:
        carry_s, vix_chg_s = prep_macro_series(h1.index, symbol)
    carry = float(carry_s.iloc[bar_i]) if bar_i < len(carry_s) else np.nan
    vix_chg = float(vix_chg_s.iloc[bar_i]) if bar_i < len(vix_chg_s) else np.nan
    fund_long = fund_sentiment_allows(1, carry, vix_chg)
    fund_short = fund_sentiment_allows(-1, carry, vix_chg)

    events = event_risk_usd(pd.Timestamp(t))
    session = _session_flags(pd.Timestamp(t))
    cot = cot_positioning(symbol, pd.Timestamp(t))
    macro = fxmacro_risk_sentiment(pd.Timestamp(t))

    if chart_state:
        pulse = {
            "chart_state": chart_state,
            "action": "WATCH_FLAT",
            "checklist_pct": 55.0,
            "regime": 1 if side > 0 else (-1 if side < 0 else 0),
        }
    else:
        slice_h1 = h1.iloc[: bar_i + 1].copy()
        if not isinstance(slice_h1.index, pd.DatetimeIndex) and "time" in slice_h1.columns:
            slice_h1 = slice_h1.set_index("time")
        pulse = analyze_market_pulse(
            slice_h1,
            symbol=symbol,
            ema_mid=ema_mid,
            use_proximity=True,
            use_sr=False,
            use_fund=False,
        )
        if pulse.get("error") or pulse.get("action") == "WARMUP":
            pulse = {"chart_state": "", "action": "WAIT_FLAT", "checklist_pct": 0, "regime": 0}

    vol = {"vol_regime": "elevated" if vol_elevated is not None and bool(vol_elevated.iloc[bar_i]) else "normal"}
    sp_pct = float(spread_pctile.iloc[bar_i]) if spread_pctile is not None and bar_i < len(spread_pctile) else np.nan
    liq = {
        "available": not np.isnan(sp_pct),
        "spread_pctile_168h": None if np.isnan(sp_pct) else sp_pct,
    }

    d1_long, d1_short = build_d1_gate(h1)
    d1 = {
        "ready": True,
        "stack_long": bool(d1_long.iloc[bar_i]) if bar_i < len(d1_long) else False,
        "stack_short": bool(d1_short.iloc[bar_i]) if bar_i < len(d1_short) else False,
    }
    gates = _professor_verdict(
        pulse,
        fund_long,
        fund_short,
        d1,
        session,
        {"available": False},
        cot=cot,
        events=events,
        macro_risk=macro,
        xau={"available": False},
        vol=vol,
        liq=liq,
        xsec={"available": False},
        rate_mom={"available": False},
    )

    block = bool(gates["block_long"]) if side > 0 else bool(gates["block_short"])
    if side > 0 and not fund_long:
        block = True
    if side < 0 and not fund_short:
        block = True
    if events.get("available") and events.get("in_usd_event_window_4h"):
        block = True
    if liq.get("spread_pctile_168h") and liq["spread_pctile_168h"] > 0.9:
        block = True
    state = str(pulse.get("chart_state") or "")
    if state in CHOP_STATES:
        block = True

    mult = float(gates["risk_mult"])
    return {
        "block": block,
        "risk_mult": float(np.clip(mult, 0.0, 1.25)),
        "chart_state": state,
        "professor_line": gates.get("professor_line", ""),
        "events": events,
        "session": session,
    }
