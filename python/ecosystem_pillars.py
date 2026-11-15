#!/usr/bin/env python3
"""Extended FX ecosystem pillars — hedge-fund style context from local CSVs only."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RESEARCH = REPO / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from fxmopol_signal_layers import CCY_MAP, parse_symbol  # noqa: E402

DATA = RESEARCH / "data"
COT_DIR = DATA / "cot"
FXMACRO = DATA / "fxmacrodata"
RATES = DATA / "rates"

G10_H1 = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "NZDUSD",
)

COT_CCY = {"EUR", "GBP", "JPY", "AUD", "CAD", "CHF"}

COT_MARKET = {
    "EUR": "EURO FX - CHICAGO",
    "GBP": "BRITISH POUND - CHICAGO",
    "JPY": "JAPANESE YEN - CHICAGO",
    "AUD": "AUSTRALIAN DOLLAR - CHICAGO",
    "CAD": "CANADIAN DOLLAR - CHICAGO",
    "CHF": "SWISS FRANC - CHICAGO",
}


@lru_cache(maxsize=1)
def _cot_net_series(currency: str) -> pd.Series:
    if currency not in COT_CCY:
        return pd.Series(dtype=float)
    needle = COT_MARKET[currency]
    frames: list[pd.DataFrame] = []
    for path in sorted(COT_DIR.glob("fut_fin_*.csv")):
        d = pd.read_csv(path, low_memory=False)
        date_col = (
            "Report_Date_as_YYYY-MM-DD"
            if "Report_Date_as_YYYY-MM-DD" in d.columns
            else "Report_Date_as_MM_DD_YYYY"
        )
        d["report_date"] = pd.to_datetime(d[date_col], errors="coerce", format="mixed")
        m = d[d["Market_and_Exchange_Names"].astype(str).str.contains(needle, regex=False)]
        if m.empty:
            continue
        m = m.copy()
        m["net_pct"] = (
            m["Lev_Money_Positions_Long_All"] - m["Lev_Money_Positions_Short_All"]
        ) / m["Open_Interest_All"].replace(0, np.nan)
        frames.append(m[["report_date", "net_pct"]])
    if not frames:
        return pd.Series(dtype=float)
    x = pd.concat(frames).dropna().drop_duplicates("report_date").set_index("report_date")["net_pct"].sort_index()
    x.index = x.index + pd.Timedelta(days=3)
    return x.resample("W-FRI").last().dropna()


def cot_positioning(symbol: str, as_of: pd.Timestamp) -> dict[str, Any]:
    base, quote = parse_symbol(symbol)
    ccy = base if base in COT_CCY else (quote if quote in COT_CCY else "")
    if not ccy:
        return {"available": False, "reason": "no_cot_leg"}
    s = _cot_net_series(ccy)
    if s.empty:
        return {"available": False, "reason": "missing_cot_files"}
    s = s[s.index <= as_of]
    if s.empty:
        return {"available": False, "reason": "cot_before_history"}
    net = float(s.iloc[-1])
    win = s.iloc[-156:] if len(s) >= 52 else s
    z = float((net - win.mean()) / (win.std() + 1e-9))
    chg4 = float(s.diff(4).iloc[-1]) if len(s) >= 5 else 0.0
    crowd = "extreme_long" if z > 1.5 else ("extreme_short" if z < -1.5 else "neutral")
    fade_long = z > 1.5
    fade_short = z < -1.5
    return {
        "available": True,
        "currency": ccy,
        "lev_net_pct_oi": round(net, 4),
        "zscore_3y_weekly": round(z, 3),
        "flow_4w_chg": round(chg4, 4),
        "crowding": crowd,
        "contrarian_fade_long": fade_long,
        "contrarian_fade_short": fade_short,
    }


def _load_calendar() -> pd.DataFrame | None:
    path = FXMACRO / "calendar_USD.csv"
    if not path.exists():
        return None
    cal = pd.read_csv(path)
    cal["event_time"] = pd.to_datetime(cal["event_time"], utc=True, errors="coerce")
    cal["importance"] = cal["importance"].fillna("unknown").astype(str).str.lower()
    return cal.dropna(subset=["event_time"])


def event_risk_usd(bar_time: pd.Timestamp) -> dict[str, Any]:
    cal = _load_calendar()
    if cal is None or cal.empty:
        return {"available": False}
    bt = pd.Timestamp(bar_time).tz_localize("UTC") if bar_time.tzinfo is None else bar_time.tz_convert("UTC")
    hi = cal[cal["importance"] == "high"].sort_values("event_time")
    future = hi[hi["event_time"] >= bt]
    past = hi[hi["event_time"] < bt]
    next_ev = future.iloc[0] if len(future) else None
    prev_ev = past.iloc[-1] if len(past) else None
    hours_next = None
    in_window = False
    if next_ev is not None:
        delta = next_ev["event_time"] - bt
        hours_next = round(delta.total_seconds() / 3600.0, 1)
        in_window = 0 <= hours_next <= 4
    return {
        "available": True,
        "hours_to_next_high_impact_usd": hours_next,
        "in_usd_event_window_4h": in_window,
        "next_event": str(next_ev["name"]) if next_ev is not None else None,
        "last_event_hours_ago": round((bt - prev_ev["event_time"]).total_seconds() / 3600.0, 1)
        if prev_ev is not None
        else None,
    }


def fxmacro_risk_sentiment(as_of: pd.Timestamp) -> dict[str, Any]:
    path = FXMACRO / "risk_sentiment.csv"
    if not path.exists():
        return {"available": False}
    risk = pd.read_csv(path)
    risk["date"] = pd.to_datetime(risk["date"], errors="coerce")
    risk["risk_score"] = pd.to_numeric(risk["risk_score"], errors="coerce")
    risk = risk.dropna(subset=["date", "risk_score"]).sort_values("date")
    row = risk[risk["date"].dt.normalize() <= as_of.normalize()]
    if row.empty:
        return {"available": False}
    last = row.iloc[-1]
    score = float(last["risk_score"])
    tail = risk.tail(20)
    z = float((score - tail["risk_score"].mean()) / (tail["risk_score"].std() + 1e-9))
    return {
        "available": True,
        "risk_score": round(score, 3),
        "risk_z_20d": round(z, 3),
        "risk_on": z < -0.3,
        "risk_off": z > 0.3,
        "pit_safe": False,
        "note": "provider composite; exploratory not OOS-certified",
    }


def _load_m5_close(symbol: str) -> pd.Series | None:
    path = DATA / f"{symbol}_M5.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time")
    return df.drop_duplicates("time").set_index("time")["close"].astype(float)


def intermarket_xau() -> dict[str, Any]:
    s = _load_m5_close("XAUUSD")
    if s is None or len(s) < 500:
        return {"available": False}
    daily = s.resample("1D").last().dropna()
    if len(daily) < 10:
        return {"available": False}
    ret5 = float(daily.pct_change(5).iloc[-1])
    bias = "gold_bid" if ret5 > 0.002 else ("gold_offer" if ret5 < -0.002 else "neutral")
    return {"available": True, "gold_ret_5d": round(ret5, 5), "safe_haven_bias": bias}


def volatility_micro(h1: pd.DataFrame) -> dict[str, Any]:
    c = h1["close"].astype(float)
    ret = np.log(c / c.shift(1))
    rv5 = float(ret.iloc[-(5 * 24) :].std() * np.sqrt(24 * 252)) if len(ret) > 120 else None
    rv21 = float(ret.iloc[-(21 * 24) :].std() * np.sqrt(24 * 252)) if len(ret) > 21 * 24 else None
    h, l = h1["high"].astype(float), h1["low"].astype(float)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_pct = float(atr.iloc[-2] / c.iloc[-2]) if len(c) > 2 and c.iloc[-2] else None
    atr_rank = None
    if len(atr) > 200:
        atr_rank = float(atr.iloc[-200:].rank(pct=True).iloc[-1])
    return {
        "realized_vol_5d_ann": None if rv5 is None else round(rv5, 4),
        "realized_vol_21d_ann": None if rv21 is None else round(rv21, 4),
        "atr_pct_price": None if atr_pct is None else round(atr_pct, 5),
        "atr_percentile_200h": None if atr_rank is None else round(atr_rank, 3),
        "vol_regime": "elevated" if atr_rank and atr_rank > 0.8 else ("quiet" if atr_rank and atr_rank < 0.2 else "normal"),
    }


def liquidity_micro(h1: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}
    if "spread" in h1.columns:
        sp = pd.to_numeric(h1["spread"], errors="coerce").dropna()
        if len(sp) > 10:
            out = {
                "available": True,
                "spread_last": int(sp.iloc[-2]),
                "spread_median_24h": float(sp.iloc[-24:].median()),
                "spread_pctile_168h": float(sp.iloc[-168:].rank(pct=True).iloc[-1]) if len(sp) >= 168 else None,
            }
    if "tick_volume" in h1.columns:
        tv = pd.to_numeric(h1["tick_volume"], errors="coerce")
        if len(tv) > 24:
            out["volume_last"] = int(tv.iloc[-2])
            out["volume_pctile_168h"] = float(tv.iloc[-168:].rank(pct=True).iloc[-1]) if len(tv) >= 168 else None
    return out


def rate_momentum(symbol: str, as_of: pd.Timestamp) -> dict[str, Any]:
    base, quote = parse_symbol(symbol)
    b_code, q_code = CCY_MAP.get(base, ""), CCY_MAP.get(quote, "")

    def _diff_series() -> pd.Series | None:
        from fxmopol_signal_layers import _load_rate_series  # noqa: PLC0415

        if not b_code or not q_code:
            return None
        br, qr = _load_rate_series(b_code), _load_rate_series(q_code)
        if br.empty or qr.empty:
            return None
        return (br - qr).dropna()

    diff = _diff_series()
    if diff is None or diff.empty:
        return {"available": False}
    diff = diff[diff.index <= as_of.normalize()]
    if len(diff) < 4:
        return {"available": False}
    now = float(diff.iloc[-1])
    m3 = float(diff.iloc[-4]) if len(diff) >= 4 else now
    chg = now - m3
    direction = "carry_improving" if chg > 0.05 else ("carry_fading" if chg < -0.05 else "stable")
    return {
        "available": True,
        "carry_diff_now_pct": round(now, 4),
        "carry_diff_chg_3m_pct": round(chg, 4),
        "carry_momentum": direction,
    }


def cross_section_fx(symbol: str, h1: pd.DataFrame) -> dict[str, Any]:
    """G10 momentum rank + correlation vs EURUSD (hedge fund relative value context)."""
    sym = symbol.upper()
    rets: dict[str, float] = {}
    for pair in G10_H1:
        path = DATA / f"{pair}_H1.csv"
        if not path.exists():
            continue
        try:
            d = pd.read_csv(path, parse_dates=["time"], usecols=["time", "close"])
            d = d.drop_duplicates("time").set_index("time")["close"].astype(float).sort_index()
            if len(d) < 24 * 6:
                continue
            rets[pair] = float(d.pct_change(5 * 24).iloc[-1])
        except (OSError, ValueError, KeyError):
            continue
    if sym not in rets or len(rets) < 3:
        return {"available": False, "pairs_loaded": len(rets)}
    ranked = sorted(rets.items(), key=lambda x: x[1], reverse=True)
    rank = next(i for i, (p, _) in enumerate(ranked, start=1) if p == sym)
    leader = ranked[0][0]
    laggard = ranked[-1][0]
    corr_eur = None
    if sym != "EURUSD" and "EURUSD" in rets:
        try:
            a = h1["close"].astype(float).pct_change().iloc[-24 * 20 :]
            epath = DATA / "EURUSD_H1.csv"
            e = pd.read_csv(epath, parse_dates=["time"], usecols=["time", "close"])
            e = e.drop_duplicates("time").set_index("time")["close"].astype(float).sort_index()
            e = e.reindex(h1.index).ffill(limit=2).pct_change().iloc[-24 * 20 :]
            corr_eur = float(a.corr(e))
        except (OSError, ValueError, KeyError):
            corr_eur = None
    return {
        "available": True,
        "mom_5d_rank": rank,
        "mom_5d_of": len(ranked),
        "mom_leader": leader,
        "mom_laggard": laggard,
        "mom_5d_return": round(rets[sym], 5),
        "corr_eurusd_20d": None if corr_eur is None or np.isnan(corr_eur) else round(corr_eur, 3),
    }


def knowledge_inventory() -> dict[str, Any]:
    """What hedge funds track vs what we have on disk right now."""
    checks = {
        "technical_multi_tf": bool(list(DATA.glob("*_H1.csv"))),
        "fundamental_policy_rates": (RATES / "US.csv").exists(),
        "fundamental_rate_momentum": (RATES / "US.csv").exists(),
        "sentiment_vix": (RATES / "VIX.csv").exists(),
        "sentiment_cot_positioning": bool(list(COT_DIR.glob("fut_fin_*.csv"))),
        "sentiment_fxmacro_risk": (FXMACRO / "risk_sentiment.csv").exists(),
        "events_usd_calendar": (FXMACRO / "calendar_USD.csv").exists(),
        "intermarket_dxy": (DATA / "DXYUSD_M5.csv").exists(),
        "intermarket_gold": (DATA / "XAUUSD_M5.csv").exists(),
        "cross_section_g10_momentum": len(list(DATA.glob("*_H1.csv"))) >= 5,
        "volatility_realized": bool(list(DATA.glob("*_H1.csv"))),
        "liquidity_spread_ticks": True,
        "options_fx_skew": False,
        "em_credit_spreads": False,
        "bank_fx_flows": False,
        "central_bank_speakers_nlp": False,
    }
    have = sum(1 for v in checks.values() if v)
    return {
        "pillars_on_disk": checks,
        "coverage_pct": round(100.0 * have / len(checks), 1),
        "ahead_of_retail": have >= 10,
        "gaps_to_close": [k for k, v in checks.items() if not v],
    }
