#!/usr/bin/env python3
"""Sovereign Ecosystem Professor — multi-pillar FX intelligence (offline).

Pillars (each symbol + global):
  * technical  — H1 stack, regime, RSI, S/R proximity, D1 trend, playbook pulse
  * fundamental — FRED policy-rate differential (carry)
  * sentiment   — VIX level & 20-session change (risk regime)
  * intermarket — DXY, gold, cross-asset bias
  * positioning — CFTC COT leveraged-money crowding (contrarian)
  * events      — USD high-impact calendar windows (FXMacroData)
  * cross_section — G10 5d momentum rank + EUR correlation
  * volatility / liquidity — realized vol, ATR%, spread & volume percentiles
  * rate_momentum — 3m change in policy-rate carry
  * session     — London / NY overlap flags (UTC bar time)
  * policy      — learned filters + trade learn (optional manifests)

Does not place trades. Writes structured dict for market_snapshot.json.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RESEARCH = REPO / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from fxmopol_chart_core import build_stack, stack_long, stack_short  # noqa: E402
from fxmopol_market_pulse import analyze_market_pulse  # noqa: E402
from bar_store import bar_source_meta, canonical_source, load_h1  # noqa: E402
from ecosystem_pillars import (  # noqa: E402
    cot_positioning,
    cross_section_fx,
    event_risk_usd,
    fxmacro_risk_sentiment,
    intermarket_xau,
    knowledge_inventory,
    liquidity_micro,
    rate_momentum,
    volatility_micro,
)
from fxmopol_signal_layers import (  # noqa: E402
    CCY_MAP,
    _load_rate_series,
    _load_vix,
    fund_sentiment_allows,
    layers_available,
    parse_symbol,
    prep_macro_series,
)

DATA = RESEARCH / "data"
RATES = DATA / "rates"
COT_DIR = DATA / "cot"
FXMACRO = DATA / "fxmacrodata"
RESULTS = RESEARCH / "results"
DEFAULT_SYMBOLS = ("GBPUSD", "EURUSD", "USDJPY")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _latest_rate(code: str, as_of: pd.Timestamp) -> float | None:
    if not code:
        return None
    s = _load_rate_series(code)
    if s.empty:
        return None
    s = s[s.index <= as_of.normalize()]
    if s.empty:
        return None
    return float(s.iloc[-1])


def _session_flags(bar_time: pd.Timestamp) -> dict[str, Any]:
    h = int(bar_time.hour)
    dow = int(bar_time.dayofweek)
    london = 7 <= h <= 11
    overlap = 13 <= h <= 16
    ny = 13 <= h <= 21
    good_fxmopol = london or overlap
    return {
        "hour_utc": h,
        "weekday": dow,
        "london_morning": london,
        "london_ny_overlap": overlap,
        "ny_afternoon": ny,
        "good_session_fxmopol": good_fxmopol,
    }


def _d1_technical(h1: pd.DataFrame) -> dict[str, Any]:
    if len(h1) < 250:
        return {"ready": False, "bias": "unknown"}
    d1 = (
        h1.resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna(subset=["close"])
    )
    if len(d1) < 120:
        return {"ready": False, "bias": "unknown"}
    d1_df = d1.reset_index()
    if "time" not in d1_df.columns:
        d1_df = d1_df.rename(columns={d1_df.columns[0]: "time"})
    st = build_stack(d1_df, 50)
    i = len(d1) - 2
    cl = float(st["close"].iloc[i])
    e21 = float(st["ema21"].iloc[i])
    long_ok = stack_long(st["close"], st["ema21"], st["ema50"], st["smma100"], i)
    short_ok = stack_short(st["close"], st["ema21"], st["ema50"], st["smma100"], i)
    bias = "bull" if long_ok else ("bear" if short_ok else "neutral")
    return {
        "ready": True,
        "bias": bias,
        "close_above_ema21": cl > e21,
        "stack_long": bool(long_ok),
        "stack_short": bool(short_ok),
    }


def _intermarket_dxy(h1_index: pd.DatetimeIndex) -> dict[str, Any]:
    path = DATA / "DXYUSD_M5.csv"
    if not path.exists():
        return {"available": False}
    try:
        dxy = pd.read_csv(path, parse_dates=["time"]).sort_values("time")
        dxy = dxy.drop_duplicates("time").set_index("time")
        daily = dxy["close"].resample("1D").last().dropna()
        if len(daily) < 6:
            return {"available": False}
        ret5 = float(daily.pct_change(5).iloc[-1])
        z = float((daily.iloc[-1] - daily.rolling(20).mean().iloc[-1]) / (daily.rolling(20).std().iloc[-1] + 1e-9))
        regime = "usd_strong" if ret5 > 0.003 else ("usd_weak" if ret5 < -0.003 else "neutral")
        return {
            "available": True,
            "dxy_ret_5d": round(ret5, 5),
            "dxy_z_20d": round(z, 3),
            "usd_regime": regime,
        }
    except (OSError, ValueError, KeyError):
        return {"available": False}


def _risk_regime(vix: float | None, vix_chg: float | None) -> str:
    if vix is None:
        return "unknown"
    if vix > 25 or (vix_chg is not None and vix_chg > 0.15):
        return "risk_off"
    if vix < 16 and (vix_chg is None or vix_chg < 0.05):
        return "risk_on"
    return "neutral"


def _professor_verdict(
    pulse: dict,
    fund_long: bool,
    fund_short: bool,
    d1: dict,
    session: dict,
    inter: dict,
    *,
    cot: dict,
    events: dict,
    macro_risk: dict,
    xau: dict,
    vol: dict,
    liq: dict,
    xsec: dict,
    rate_mom: dict,
) -> dict[str, Any]:
    ta_pct = float(pulse.get("checklist_pct") or 0.0) / 100.0
    action = str(pulse.get("action") or "WAIT_FLAT")
    state = str(pulse.get("chart_state") or "")
    reg = int(pulse.get("regime") or 0)

    edge_long = 0.35
    edge_short = 0.35
    if "ENTER" in action and "LONG" in action:
        edge_long = 0.85
    elif "ENTER" in action and "SHORT" in action:
        edge_short = 0.85
    elif reg == 1:
        edge_long = 0.55
    elif reg == -1:
        edge_short = 0.55

    edge_long *= 0.5 + 0.5 * ta_pct
    edge_short *= 0.5 + 0.5 * ta_pct
    if d1.get("ready"):
        if d1.get("stack_long"):
            edge_long *= 1.1
        if d1.get("stack_short"):
            edge_short *= 1.1
        if d1.get("stack_long") and edge_short > 0.5:
            edge_short *= 0.85
        if d1.get("stack_short") and edge_long > 0.5:
            edge_long *= 0.85

    if not fund_long:
        edge_long *= 0.6
    if not fund_short:
        edge_short *= 0.6

    if inter.get("available") and inter.get("usd_regime") == "usd_strong":
        edge_short += 0.05
        edge_long -= 0.05
    elif inter.get("available") and inter.get("usd_regime") == "usd_weak":
        edge_long += 0.05
        edge_short -= 0.05

    if cot.get("available"):
        if cot.get("contrarian_fade_long"):
            edge_long *= 0.75
        if cot.get("contrarian_fade_short"):
            edge_short *= 0.75
        if cot.get("flow_4w_chg", 0) > 0.02:
            edge_long *= 1.05
        if cot.get("flow_4w_chg", 0) < -0.02:
            edge_short *= 1.05

    if macro_risk.get("available") and macro_risk.get("risk_off"):
        edge_long *= 0.85
    if xau.get("available") and xau.get("safe_haven_bias") == "gold_bid":
        edge_long *= 0.95

    if rate_mom.get("available"):
        if rate_mom.get("carry_momentum") == "carry_improving":
            edge_long *= 1.05
        elif rate_mom.get("carry_momentum") == "carry_fading":
            edge_long *= 0.9

    if xsec.get("available") and xsec.get("mom_5d_rank", 99) <= 2:
        edge_long *= 1.03
    if xsec.get("available") and xsec.get("mom_5d_rank", 0) >= xsec.get("mom_5d_of", 1) - 1:
        edge_short *= 1.03

    edge_long = float(np.clip(edge_long, 0.0, 1.0))
    edge_short = float(np.clip(edge_short, 0.0, 1.0))

    chop = state in ("CHOP_SMMA", "CHOP_MIXED", "FLAT_NEUTRAL")
    block_long = chop or not fund_long
    block_short = chop or not fund_short
    if events.get("available") and events.get("in_usd_event_window_4h"):
        block_long = True
        block_short = True
    if vol.get("vol_regime") == "elevated":
        pass
    if liq.get("available") and liq.get("spread_pctile_168h") and liq["spread_pctile_168h"] > 0.9:
        block_long = True
        block_short = True
    if "ENTER_LONG" in action and fund_long:
        block_long = False
    if "ENTER_SHORT" in action and fund_short:
        block_short = False

    mult = 1.0
    if chop:
        mult = 0.5
    elif "WATCH" in action:
        mult = 0.85
    elif ta_pct < 0.4:
        mult = 0.75
    if not session.get("good_session_fxmopol"):
        mult = min(mult, 0.9)
    if events.get("available") and events.get("in_usd_event_window_4h"):
        mult = min(mult, 0.25)
    elif events.get("available") and events.get("hours_to_next_high_impact_usd") is not None:
        h_next = float(events["hours_to_next_high_impact_usd"])
        if h_next < 12:
            mult = min(mult, 0.7)
    if vol.get("vol_regime") == "elevated":
        mult = min(mult, 0.8)
    if liq.get("spread_pctile_168h") and liq["spread_pctile_168h"] > 0.75:
        mult = min(mult, 0.85)

    if edge_long >= edge_short and edge_long >= 0.55:
        verdict = f"FAVOR_LONG ({edge_long:.2f})"
    elif edge_short > edge_long and edge_short >= 0.55:
        verdict = f"FAVOR_SHORT ({edge_short:.2f})"
    else:
        verdict = "STUDY_ONLY — no clear edge"

    cot_z = cot.get("zscore_3y_weekly") if cot.get("available") else None
    evt = "EV+" if events.get("in_usd_event_window_4h") else "EV-"
    line = (
        f"TA:{state} | COTz={cot_z} | {evt} | "
        f"Vol:{vol.get('vol_regime','?')} | {verdict}"
    )

    return {
        "verdict": verdict,
        "edge_long": round(edge_long, 3),
        "edge_short": round(edge_short, 3),
        "risk_mult": round(mult, 3),
        "block_long": bool(block_long),
        "block_short": bool(block_short),
        "professor_line": line,
        "action_mirror": action,
        "pillar_notes": {
            "cot_crowding": cot.get("crowding"),
            "usd_event_hours": events.get("hours_to_next_high_impact_usd"),
            "g10_mom_rank": xsec.get("mom_5d_rank"),
        },
    }


def analyze_symbol(symbol: str, *, ema_mid: int = 50) -> dict[str, Any]:
    sym = symbol.upper()
    h1 = load_h1(sym)
    if h1.empty or len(h1) < 120:
        return {"symbol": sym, "error": "insufficient_h1"}

    bar_time = pd.Timestamp(h1.index[-2])
    pulse = analyze_market_pulse(
        h1, symbol=sym, ema_mid=ema_mid, use_proximity=True, use_sr=True, use_fund=False
    )
    carry_s, vix_chg_s = prep_macro_series(h1.index, sym)
    t = h1.index[-2]
    carry = float(carry_s.loc[t]) if t in carry_s.index and pd.notna(carry_s.loc[t]) else np.nan
    vix_chg = float(vix_chg_s.loc[t]) if t in vix_chg_s.index and pd.notna(vix_chg_s.loc[t]) else np.nan

    base, quote = parse_symbol(sym)
    b_rate = _latest_rate(CCY_MAP.get(base, ""), bar_time)
    q_rate = _latest_rate(CCY_MAP.get(quote, ""), bar_time)

    vix_s = _load_vix()
    vix_level = None
    if len(vix_s):
        vs = vix_s[vix_s.index <= bar_time.normalize()]
        if len(vs):
            vix_level = float(vs.iloc[-1])

    fund_long = fund_sentiment_allows(1, carry, vix_chg)
    fund_short = fund_sentiment_allows(-1, carry, vix_chg)

    technical = {
        "bar_time": str(bar_time),
        "data_plane": bar_source_meta(sym),
        "chart_state": pulse.get("chart_state"),
        "regime": pulse.get("regime"),
        "rsi": pulse.get("rsi"),
        "grade_long": pulse.get("grade_long"),
        "grade_short": pulse.get("grade_short"),
        "checklist_pct": pulse.get("checklist_pct"),
        "triggers": pulse.get("triggers"),
        "best_move": pulse.get("best_move"),
        "d1": _d1_technical(h1),
    }
    fundamental = {
        "base_ccy": base,
        "quote_ccy": quote,
        "base_rate_pct": b_rate,
        "quote_rate_pct": q_rate,
        "carry_diff_pct": None if np.isnan(carry) else round(float(carry), 4),
        "carry_supports_long": fund_sentiment_allows(1, carry, np.nan),
        "carry_supports_short": fund_sentiment_allows(-1, carry, np.nan),
        "layers": layers_available(sym),
    }
    sentiment = {
        "vix": vix_level,
        "vix_chg_20sess": None if np.isnan(vix_chg) else round(float(vix_chg), 4),
        "risk_regime": _risk_regime(vix_level, vix_chg if not np.isnan(vix_chg) else None),
        "allows_new_long": fund_long,
        "allows_new_short": fund_short,
    }
    session = _session_flags(bar_time)
    inter = _intermarket_dxy(h1.index)
    inter["gold"] = intermarket_xau()
    positioning = cot_positioning(sym, bar_time)
    events = event_risk_usd(bar_time)
    macro_risk = fxmacro_risk_sentiment(bar_time)
    vol = volatility_micro(h1)
    liq = liquidity_micro(h1)
    xsec = cross_section_fx(sym, h1)
    rate_mom = rate_momentum(sym, bar_time)
    fundamental["rate_momentum"] = rate_mom
    sentiment["fxmacro_risk"] = macro_risk
    sentiment["cot"] = positioning
    gates = _professor_verdict(
        pulse,
        fund_long,
        fund_short,
        technical["d1"],
        session,
        inter,
        cot=positioning,
        events=events,
        macro_risk=macro_risk,
        xau=inter.get("gold", {}),
        vol=vol,
        liq=liq,
        xsec=xsec,
        rate_mom=rate_mom,
    )

    return {
        "symbol": sym,
        "technical": technical,
        "fundamental": fundamental,
        "sentiment": sentiment,
        "intermarket": inter,
        "positioning": positioning,
        "events": events,
        "cross_section": xsec,
        "volatility": vol,
        "liquidity": liq,
        "session": session,
        "gates": gates,
    }


def load_microstructure_manifest(sym: str, mt5_files: Path) -> dict[str, Any] | None:
    """OFI/Hawkes/ODE manifest from research/ml/microstructure_pipeline.py."""
    sym = sym.upper()
    for base in (RESULTS, mt5_files):
        for name in (f"microstructure_{sym}_manifest.json", "microstructure_manifest.json"):
            p = base / name
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if name == "microstructure_manifest.json" and data.get("symbol", sym).upper() != sym:
                continue
            return data
    return None


def apply_microstructure_gates(gates: dict[str, Any], micro: dict[str, Any] | None) -> dict[str, Any]:
    if not micro or "last" not in micro:
        return gates
    last = micro["last"]
    mult = float(gates.get("risk_mult") or 1.0)
    tox = float(last.get("toxicity_head") or 0.0)
    ofi = float(last.get("ofi") or 0.0)
    if last.get("throttle_execution"):
        mult = min(mult, 0.65)
    elif tox > 0.55:
        mult = min(mult, 0.85)
    if tox > 0.82:
        gates = {**gates, "block_long": True, "block_short": True}
    line = str(gates.get("professor_line", ""))
    src = micro.get("book_source", "?")
    line += f" | µstruct:{src} OFI={ofi:.1f} tox={tox:.2f}"
    return {**gates, "risk_mult": round(mult, 3), "professor_line": line, "microstructure": last}


def load_policy_manifests(mt5_files: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"learned_filters": None, "trade_learn": None, "century": None}
    for name, key in (
        ("fxmopol_learned_filters.json", "learned_filters"),
        ("fxmopol_trade_learn.json", "trade_learn"),
        ("century_directives.json", "century"),
    ):
        for base in (mt5_files, RESULTS):
            p = base / name
            if p.exists():
                try:
                    out[key] = json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    out[key] = {"error": "invalid_json", "path": str(p)}
                break
    return out


def build_ecosystem_snapshot(
    symbols: list[str] | None = None,
    *,
    ttl_hours: int = 4,
    mt5_files: Path | None = None,
) -> dict[str, Any]:
    syms = [s.upper() for s in (symbols or list(DEFAULT_SYMBOLS))]
    now = _utcnow()
    expires = now + timedelta(hours=ttl_hours)
    mt5 = mt5_files or (REPO / "backtest" / "mt5_files" / "Sovereign")
    policy = load_policy_manifests(mt5)

    per_sym: dict[str, Any] = {}
    symbol_gates: dict[str, Any] = {}
    microstructure: dict[str, Any] = {}
    vix_global = None
    risk_regimes: list[str] = []

    for sym in syms:
        micro = load_microstructure_manifest(sym, mt5)
        if micro:
            microstructure[sym] = {
                "book_source": micro.get("book_source"),
                "data_gap": micro.get("data_gap"),
                "last": micro.get("last"),
                "rows": micro.get("rows"),
            }
        try:
            block = analyze_symbol(sym)
        except FileNotFoundError as exc:
            block = {"symbol": sym, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — snapshot must always emit
            block = {"symbol": sym, "error": repr(exc)}
        per_sym[sym] = block
        if "gates" in block:
            g = apply_microstructure_gates(dict(block["gates"]), micro)
            symbol_gates[sym] = {
                "risk_mult": g["risk_mult"],
                "block_long": g["block_long"],
                "block_short": g["block_short"],
                "verdict": g["verdict"],
                "professor_line": g["professor_line"],
                "edge_long": g["edge_long"],
                "edge_short": g["edge_short"],
            }
            if g.get("microstructure"):
                symbol_gates[sym]["microstructure"] = g["microstructure"]
        sent = block.get("sentiment") or {}
        if sent.get("vix") is not None:
            vix_global = sent["vix"]
        if sent.get("risk_regime"):
            risk_regimes.append(sent["risk_regime"])

    halt = False
    global_mult = 1.0
    century = policy.get("century")
    if isinstance(century, dict):
        halt = bool(century.get("halt_trading"))
        global_mult = float(century.get("global_risk_scalar") or 1.0)
        global_mult = float(np.clip(global_mult, 0.0, 1.25))

    dom_risk = max(set(risk_regimes), key=risk_regimes.count) if risk_regimes else "unknown"
    inventory = knowledge_inventory()
    prophet: dict | None = None
    for base in (mt5, RESULTS):
        pf = base / "prophet_forecasts.json"
        if pf.exists():
            try:
                prophet = json.loads(pf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prophet = None
            break
    headline = (
        f"Professor v2 | VIX={vix_global} | risk={dom_risk} | "
        f"knowledge={inventory['coverage_pct']}% | symbols={len(symbol_gates)}"
    )
    if prophet:
        headline += " | Prophet:ON"
    if microstructure:
        throttled = sum(
            1 for m in microstructure.values() if (m.get("last") or {}).get("throttle_execution")
        )
        headline += f" | µstruct:{len(microstructure)}"
        if throttled:
            headline += f" throttle={throttled}"

    return {
        "schema_version": 2,
        "role": "sovereign_ecosystem_professor",
        "generated_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "source": "python/ecosystem_professor.py",
        "knowledge_coverage": inventory,
        "data_plane": {
            "canonical_bar_source": canonical_source(),
            "config": str(REPO / "configs" / "mt5_data_plane.json"),
            "mt5_export_dir": str(DATA / "mt5"),
            "audit_script": "research/mt5_data_plane_audit.py",
        },
        "data_ingested": {
            "h1_bars": str(DATA / "mt5/*_H1.csv (canonical)"),
            "h1_histdata_fallback": str(DATA / "histdata/*_H1.csv"),
            "fred_rates": str(RATES),
            "vix": str(RATES / "VIX.csv"),
            "cot": str(COT_DIR / "fut_fin_*.csv"),
            "fxmacro": str(FXMACRO),
            "dxy_optional": str(DATA / "DXYUSD_M5.csv"),
            "xau_optional": str(DATA / "XAUUSD_M5.csv"),
            "learned_manifests": ["fxmopol_learned_filters.json", "fxmopol_trade_learn.json"],
        },
        "global": {
            "halt_new_risk": halt,
            "global_risk_mult": round(global_mult, 3),
            "vix_level": vix_global,
            "risk_regime": dom_risk,
            "professor_headline": headline,
            "usd_event_risk": event_risk_usd(pd.Timestamp(now.replace(tzinfo=None))),
            "fxmacro_risk": fxmacro_risk_sentiment(pd.Timestamp(now.replace(tzinfo=None))),
        },
        "symbol_gates": symbol_gates,
        "symbols": per_sym,
        "microstructure": microstructure,
        "prophet_forecasts": prophet or {},
        "prophet_note": "next-bar probabilities from research/prophet_multitf.py; promote on OOS monthly",
        "policy": policy,
    }
