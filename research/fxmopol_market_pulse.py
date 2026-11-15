#!/usr/bin/env python3
"""One-pass market condition readout — best action on the last closed H1 bar.

This is a **full checklist**, not a guarantee of profit. Markets are not 100% predictable.
The EA mirror is `PublishMarketPulse()` in SovereignFxmopol.mqh.

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_market_pulse.py --symbol GBPUSD
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fxmopol_chart_core import (
    build_stack,
    chart_state_at,
    ema21_cross_close_long,
    ema21_cross_close_short,
    passes_entry_filters,
    pullback_long,
    regime_streak,
    retest_short,
    rsi_bearish_divergence,
    rsi_bullish_divergence,
    stack_long,
    stack_short,
    stop_hunt_reclaim,
)
from fxmopol_playbook import load_h1
from fxmopol_signal_layers import fund_sentiment_allows, prep_macro_series, prep_sr_columns, sr_long_ok, sr_short_ok
from fxmopol_symbol_train import load_symbol

REGIME_BARS = 5

STATE_PLAIN = {
    "STACK_LONG": "Bull EMA fan — price riding trend above SMMA",
    "STACK_SHORT": "Bear EMA fan — price pressing down under SMMA",
    "CHOP_SMMA": "Chop — price tangled on white SMMA (sit on hands)",
    "CHOP_MIXED": "Mixed chop — no clean stack",
    "TRANSITION_BULL": "Bull transition — stack forming, not confirmed",
    "TRANSITION_BEAR": "Bear transition — stack forming, not confirmed",
    "FLAT_NEUTRAL": "Neutral — no trend edge",
    "WARMUP": "Warm-up — indicators not ready",
}


def _market_doing_now(state: str, reg: int, grade_long: str, grade_short: str, rsi_v: float | None) -> str:
    base = STATE_PLAIN.get(state, state)
    if rsi_v is not None:
        base += f" | RSI {rsi_v}"
    if grade_long == "GOOD":
        base += " | Long grade GOOD"
    elif grade_short == "GOOD":
        base += " | Short grade GOOD"
    elif grade_long == "WAIT" or grade_short == "WAIT":
        base += " | Grade WAIT (RSI/timing wrong)"
    if reg == 0 and state not in ("CHOP_SMMA", "CHOP_MIXED"):
        base += " | Regime filter: sideways"
    return base


def _quick_in_out(
    action: str,
    open_side: int,
    triggers: list[tuple[str, int]],
    atr_i: float,
) -> tuple[str, list[str]]:
    """Q2: how to enter/exit fast with playbook risk rules."""
    issues: list[str] = []
    stop = f"hard stop ~{1.0 * atr_i:.5f} (1×ATR) on entry bar" if atr_i > 0 else "stop 1×ATR from entry"
    if open_side > 0:
        return (
            "Stay long while close > EMA21 and RSI < 80; exit on break, RSI 80+, or extension.",
            issues,
        )
    if open_side < 0:
        return (
            "Stay short while close < EMA21 and RSI > 20; exit on break, RSI 20-, or extension.",
            issues,
        )
    if action.startswith("ENTER_LONG"):
        return (
            f"IN: market buy next H1 open if TrafficLight allows; {stop}. "
            "OUT: close < EMA21, RSI ≥ 80, or extension exit.",
            issues,
        )
    if action.startswith("ENTER_SHORT"):
        return (
            f"IN: market sell next H1 open if TrafficLight allows; {stop}. "
            "OUT: close > EMA21, RSI ≤ 20, or extension exit.",
            issues,
        )
    if triggers:
        names = ", ".join(t[0] for t in triggers[:3])
        side = "LONG" if triggers[0][1] > 0 else "SHORT"
        return (
            f"No fill yet — watch for {names} ({side}). Until trigger: no entry. "
            f"When fired: {stop}, same EMA21/RSI exits.",
            issues,
        )
    if "CHOP" in action or "WAIT_FLAT" in action:
        issues.append("chop or no trigger — skipping avoids most bad fills")
        return ("IN: none. OUT: n/a. Preserve capital until STACK_* + GOOD grade.", issues)
    if "WATCH_LONG" in action:
        return (
            "IN: wait pullback to EMA21 or hunt reclaim (RSI 50–80). "
            "OUT: EMA21 break or RSI 80.",
            issues,
        )
    if "WATCH_SHORT" in action:
        return (
            "IN: wait EMA21 retest reject (RSI 20–50). "
            "OUT: EMA21 break or RSI 20.",
            issues,
        )
    return ("IN: wait for playbook trigger. OUT: rule-based on entry side.", issues)


def _entry_triggers(i: int, st: dict, long_flags: list[bool], short_flags: list[bool]) -> list[tuple[str, int]]:
    o, h, l, c = st["open"], st["high"], st["low"], st["close"]
    e21, e50, s100, r, atr = st["ema21"], st["ema50"], st["smma100"], st["rsi"], st["atr"]
    rv = r.iloc[i]
    out: list[tuple[str, int]] = []
    if pd.isna(rv):
        return out
    rv = float(rv)
    if regime_streak(long_flags, i, REGIME_BARS) and 50 < rv < 80:
        if pullback_long(l, c, e21, atr, i):
            out.append(("entry_pullback_ema21", 1))
        if stop_hunt_reclaim(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], 1):
            out.append(("entry_stop_hunt_reclaim", 1))
    if regime_streak(short_flags, i, REGIME_BARS) and 20 < rv < 50:
        if retest_short(h, c, e21, atr, i):
            out.append(("entry_retest_ema21", -1))
        if stop_hunt_reclaim(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], -1):
            out.append(("entry_stop_hunt_reject", -1))
    if ema21_cross_close_long(c, e21, i) and 45 < rv < 78:
        out.append(("entry_ema21_close_cross_long", 1))
    if ema21_cross_close_short(c, e21, i) and 22 < rv < 55:
        out.append(("entry_ema21_close_cross_short", -1))
    if rsi_bullish_divergence(l, r, i) and c.iloc[i] > e21.iloc[i] and 22 < rv < 58:
        out.append(("entry_rsi_bull_div", 1))
    if rsi_bearish_divergence(h, r, i) and c.iloc[i] < e21.iloc[i] and 42 < rv < 78:
        out.append(("entry_rsi_bear_div", -1))
    return out


def _exit_hint(open_side: int, i: int, st: dict) -> str | None:
    if open_side == 0:
        return None
    c, e21, r = st["close"], st["ema21"], st["rsi"]
    rv = float(r.iloc[i]) if pd.notna(r.iloc[i]) else 50.0
    if open_side > 0:
        if c.iloc[i] < e21.iloc[i]:
            return "EXIT_LONG_NOW (below EMA21)"
        if rv >= 80:
            return "EXIT_LONG_SOON (RSI stretched)"
        if rsi_bearish_divergence(st["high"], r, i):
            return "EXIT_LONG_SOON (bearish RSI div)"
        return "HOLD_LONG (no exit signal)"
    if c.iloc[i] > e21.iloc[i]:
        return "EXIT_SHORT_NOW (above EMA21)"
    if rv <= 20:
        return "EXIT_SHORT_SOON (RSI stretched)"
    if rsi_bullish_divergence(st["low"], r, i):
        return "EXIT_SHORT_SOON (bullish RSI div)"
    return "HOLD_SHORT (no exit signal)"


def analyze_market_pulse(
    h1: pd.DataFrame,
    symbol: str = "",
    ema_mid: int = 50,
    open_side: int = 0,
    use_proximity: bool = True,
    use_sr: bool = False,
    use_fund: bool = False,
) -> dict:
    """Return structured readout for the last **closed** bar (index -2)."""
    if len(h1) < 120:
        return {"action": "WARMUP", "detail": "Not enough bars", "checklist_pct": 0.0}
    st = build_stack(h1, ema_mid)
    i = len(h1) - 2
    c, h, l, e21, e50, s100, r, atr = (
        st["close"],
        st["high"],
        st["low"],
        st["ema21"],
        st["ema50"],
        st["smma100"],
        st["rsi"],
        st["atr"],
    )
    long_flags = [stack_long(c, e21, e50, s100, j) for j in range(i + 1)]
    short_flags = [stack_short(c, e21, e50, s100, j) for j in range(i + 1)]
    state, grade_long, grade_short = chart_state_at(
        i, c, e21, e50, s100, r, atr, long_flags, short_flags, REGIME_BARS
    )
    reg = 0
    if stack_long(c, e21, e50, s100, i) and not stack_short(c, e21, e50, s100, i):
        reg = 1
    elif stack_short(c, e21, e50, s100, i) and not stack_long(c, e21, e50, s100, i):
        reg = -1

    bar_time = str(h1.index[i])
    rsi_v = round(float(r.iloc[i]), 1) if pd.notna(r.iloc[i]) else None

    atr_i = float(atr.iloc[i]) if pd.notna(atr.iloc[i]) else 0.0
    exit_hint = _exit_hint(open_side, i, st)
    if exit_hint:
        q1 = _market_doing_now(state, reg, grade_long, grade_short, rsi_v)
        q2, issues = _quick_in_out(exit_hint.split()[0], open_side, [], atr_i)
        checks = {"has_position": True, "exit_defined": True}
        return {
            "time": bar_time,
            "symbol": symbol,
            "chart_state": state,
            "grade_long": grade_long,
            "grade_short": grade_short,
            "regime": reg,
            "rsi": rsi_v,
            "action": exit_hint.split()[0],
            "best_move": exit_hint,
            "q1_market_now": q1,
            "q2_quick_in_out": q2,
            "issues": issues,
            "triggers": [],
            "checklist_pct": 100.0,
            "checks": checks,
            "disclaimer": "No system guarantees profit; issues list helps avoid bad fills.",
        }

    triggers = _entry_triggers(i, st, long_flags, short_flags)
    sr_cols = prep_sr_columns(h1)
    carry_s, vix_chg_s = prep_macro_series(h1.index, symbol) if symbol and use_fund else (None, None)
    t = h1.index[i]

    scored: list[tuple[float, str, int, str]] = []
    for rule, side in triggers:
        grade = grade_long if side > 0 else grade_short
        if grade != "GOOD":
            continue
        cross = rule.startswith("entry_ema21_close_cross") or rule.startswith("entry_rsi_")
        if use_proximity and not cross and not passes_entry_filters(
            c, h, l, e21, e50, s100, atr, i, side
        ):
            scored.append((0.55, "WATCH", side, f"{rule} blocked by proximity"))
            continue
        if state in ("CHOP_SMMA", "FLAT_NEUTRAL", "CHOP_MIXED") and not cross:
            scored.append((0.35, "WAIT", side, f"{rule} but {state}"))
            continue
        if use_sr:
            atr_i = float(atr.iloc[i])
            if side > 0:
                ok_sr = sr_long_ok(
                    i,
                    float(l.iloc[i]),
                    float(c.iloc[i]),
                    atr_i,
                    float(sr_cols["sh"].iloc[i]),
                    float(sr_cols["sl"].iloc[i]),
                    float(sr_cols["fib618"].iloc[i]),
                    float(sr_cols["s1"].iloc[i]),
                )
            else:
                ok_sr = sr_short_ok(
                    i,
                    float(h.iloc[i]),
                    float(c.iloc[i]),
                    atr_i,
                    float(sr_cols["sh"].iloc[i]),
                    float(sr_cols["sl"].iloc[i]),
                    float(sr_cols["fib50"].iloc[i]),
                    float(sr_cols["r1"].iloc[i]),
                )
            if not ok_sr:
                scored.append((0.5, "WATCH", side, f"{rule} lacks S/R confluence"))
                continue
        if use_fund and carry_s is not None:
            if not fund_sentiment_allows(
                side,
                float(carry_s.loc[t]) if t in carry_s.index else np.nan,
                float(vix_chg_s.loc[t]) if t in vix_chg_s.index else np.nan,
            ):
                scored.append((0.4, "WAIT", side, f"{rule} blocked by carry/VIX"))
                continue
        scored.append((0.95, "ENTER", side, rule))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        rank, kind, side, detail = scored[0]
        action = f"{kind}_{'LONG' if side > 0 else 'SHORT'}_NOW" if kind == "ENTER" else f"{kind}_{'LONG' if side > 0 else 'SHORT'}"
        best = f"{action}: {detail}"
    elif state in ("CHOP_SMMA", "CHOP_MIXED"):
        action, best = "WAIT_FLAT", "Chop on SMMA — no new risk"
    elif reg == 1 and grade_long == "WAIT":
        action, best = "WAIT_LONG", "Bull stack but RSI/transition not ready"
    elif reg == -1 and grade_short == "WAIT":
        action, best = "WAIT_SHORT", "Bear stack but RSI/transition not ready"
    elif reg == 1:
        action, best = "WATCH_LONG", "Uptrend regime — wait for playbook trigger"
    elif reg == -1:
        action, best = "WATCH_SHORT", "Downtrend regime — wait for playbook trigger"
    else:
        action, best = "WAIT_FLAT", "No clean edge on closed bar"

    checks = {
        "state_not_chop": state not in ("CHOP_SMMA", "CHOP_MIXED"),
        "long_grade_good": grade_long == "GOOD",
        "short_grade_good": grade_short == "GOOD",
        "has_trigger": len(triggers) > 0,
        "fund_ok": True,
    }
    checklist_pct = round(100.0 * sum(1 for v in checks.values() if v) / len(checks), 1)
    q1 = _market_doing_now(state, reg, grade_long, grade_short, rsi_v)
    q2, issues = _quick_in_out(action, open_side, triggers, atr_i)
    if state in ("CHOP_SMMA", "CHOP_MIXED"):
        issues.append("chop on SMMA")
    if grade_long != "GOOD" and grade_short != "GOOD" and open_side == 0:
        issues.append("no GOOD grade entry")
    if not triggers and open_side == 0 and "ENTER" not in action:
        issues.append("no trigger on closed bar")

    return {
        "time": bar_time,
        "symbol": symbol,
        "chart_state": state,
        "grade_long": grade_long,
        "grade_short": grade_short,
        "regime": reg,
        "rsi": rsi_v,
        "action": action,
        "best_move": best,
        "q1_market_now": q1,
        "q2_quick_in_out": q2,
        "issues": issues,
        "triggers": [f"{r} side={'LONG' if s > 0 else 'SHORT'}" for r, s in triggers],
        "checklist_pct": checklist_pct,
        "checks": checks,
        "disclaimer": "No system guarantees profit; issues list helps avoid bad fills.",
    }


def main() -> int:
    p = argparse.ArgumentParser(description="FXMOPOL instant market pulse")
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--ema-mid", type=int, default=50)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    sym = args.symbol.upper()
    try:
        h1 = load_symbol(sym)
    except FileNotFoundError:
        h1 = load_h1(sym)
    pulse = analyze_market_pulse(h1, symbol=sym, ema_mid=args.ema_mid)
    if args.json:
        print(json.dumps(pulse, indent=2))
    else:
        print(f"=== FXMOPOL @ {pulse.get('time')} {sym} ===")
        print(f"Q1 - What is the market doing right now?\n  {pulse.get('q1_market_now')}")
        print(f"Q2 - Quick in/out without issue?\n  {pulse.get('q2_quick_in_out')}")
        print(f"Action: {pulse.get('action')} ({pulse.get('best_move')})")
        iss = pulse.get("issues") or []
        print(f"Issues to respect: {', '.join(iss) if iss else 'none flagged'}")
        if pulse.get("triggers"):
            print("Triggers:", "; ".join(pulse["triggers"]))
        print(pulse.get("disclaimer"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
