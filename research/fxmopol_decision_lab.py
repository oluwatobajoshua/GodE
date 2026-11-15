#!/usr/bin/env python3
"""History walk with full chart stack — chart good/bad, multi entry/exit, enter/hold Q&A stats.

Indicators: EMA21, EMA50 (or 100 JPY), SMMA100, RSI(9) 20/50/80, fractals, ATR(14).

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_decision_lab.py
    .\\.venv\\Scripts\\python.exe research\\fxmopol_decision_lab.py --window 2020-06-17 2020-07-24
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from fxmopol_chart_core import (
    build_stack,
    chart_state_at,
    chop_near_smma,
    extension_exit_trigger,
    fractal_high,
    fractal_low,
    passes_entry_filters,
    pullback_long,
    regime_streak,
    retest_short,
    stack_long,
    stack_short,
    stop_hunt_reclaim,
)
from fxmopol_playbook import SPLITS, load_h1

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "results"
COST_BPS = 3.0
REGIME_BARS = 5
MAX_HOLD = 72

ENTRY_RULES_DOC = {
    "entry_pullback_ema21": "Long regime: touch/hold EMA21, RSI (50,80).",
    "entry_stop_hunt_reclaim": "Long regime: hunt wick reclaim, RSI (50,80).",
    "entry_fractal_hunt_long": "Long regime: fractal low + hunt reclaim.",
    "entry_rsi50_cross_long": "Long regime: RSI crosses up through 50.",
    "entry_stack_flip_long": "First stacked long bar after chop (transition).",
    "entry_retest_ema21": "Short regime: reject EMA21 from below, RSI (20,50).",
    "entry_stop_hunt_reject": "Short regime: hunt reject, RSI (20,50).",
    "entry_fractal_hunt_short": "Short regime: fractal high + hunt reject.",
    "entry_rsi50_cross_short": "Short regime: RSI crosses down through 50.",
}

LONG_EXITS = (
    "exit_ema21_break",
    "exit_rsi_extreme",
    "exit_rsi_roll_long",
    "exit_regime_lost",
    "exit_time_24bars",
    "exit_atr_trail_1p5",
    "exit_scale_rsi70_long",
    "exit_ema21_extension",
)
SHORT_EXITS = (
    "exit_ema21_break",
    "exit_rsi_extreme",
    "exit_rsi_roll_short",
    "exit_regime_lost",
    "exit_time_24bars",
    "exit_atr_trail_1p5",
    "exit_scale_rsi70_long",
    "exit_ema21_extension",
)

EXIT_RULES_DOC = {
    "exit_ema21_break": "Long: close below EMA21. Short: close above EMA21.",
    "exit_rsi_extreme": "Long: RSI >= 80. Short: RSI <= 20.",
    "exit_rsi_roll_long": "Long: RSI was >72 and now <68.",
    "exit_rsi_roll_short": "Short: RSI was <28 and now >32.",
    "exit_regime_lost": "Stack alignment lost for 2 consecutive bars.",
    "exit_time_24bars": "Time stop at 24 H1 bars.",
    "exit_atr_trail_1p5": "Initial stop 1.5×ATR; trail to breakeven after 1×ATR profit.",
    "exit_scale_rsi70_long": "Long partial logic proxy: exit when RSI >= 75.",
    "exit_ema21_extension": "After min bars + min profit: close when |close-EMA21| >= 1.15 ATR (still on right side).",
}


@dataclass
class SimTrade:
    entry_i: int
    exit_i: int
    side: int
    entry_rule: str
    exit_rule: str
    chart_state: str
    chart_grade: str
    return_pct: float
    pips: float


def detect_entries(
    i: int,
    o,
    h,
    l,
    c,
    e21,
    e50,
    s100,
    r,
    atr,
    long_flags: list[bool],
    short_flags: list[bool],
) -> list[tuple[str, int]]:
    """List of (entry_rule_id, side +1/-1)."""
    out: list[tuple[str, int]] = []
    rv = r.iloc[i]
    if pd.isna(rv):
        return out

    if regime_streak(long_flags, i, REGIME_BARS) and 50 < rv < 80:
        if pullback_long(l, c, e21, atr, i):
            out.append(("entry_pullback_ema21", 1))
        if stop_hunt_reclaim(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], 1):
            out.append(("entry_stop_hunt_reclaim", 1))
        if fractal_low(l, i - 1) and stop_hunt_reclaim(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], 1):
            out.append(("entry_fractal_hunt_long", 1))
        if i >= 1 and pd.notna(r.iloc[i - 1]) and r.iloc[i - 1] <= 50 < rv:
            out.append(("entry_rsi50_cross_long", 1))

    if regime_streak(short_flags, i, REGIME_BARS) and 20 < rv < 50:
        if retest_short(h, c, e21, atr, i):
            out.append(("entry_retest_ema21", -1))
        if stop_hunt_reclaim(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], -1):
            out.append(("entry_stop_hunt_reject", -1))
        if fractal_high(h, i - 1) and stop_hunt_reclaim(o.iloc[i], h.iloc[i], l.iloc[i], c.iloc[i], -1):
            out.append(("entry_fractal_hunt_short", -1))
        if i >= 1 and pd.notna(r.iloc[i - 1]) and r.iloc[i - 1] >= 50 > rv:
            out.append(("entry_rsi50_cross_short", -1))

    # first clean stack bar after chop
    if stack_long(c, e21, e50, s100, i) and not stack_long(c, e21, e50, s100, i - 1):
        if chop_near_smma(c, s100, atr, i - 1) or chop_near_smma(c, s100, atr, i - 2):
            if 50 < rv < 72:
                out.append(("entry_stack_flip_long", 1))
    return [(rule, s) for rule, s in out if passes_entry_filters(c, h, l, e21, e50, s100, atr, i, s)]


def exit_checker(exit_rule: str) -> Callable[..., tuple[bool, str]]:
    def fn(
        side: int,
        i: int,
        entry_i: int,
        entry_px: float,
        c,
        e21,
        s100,
        r,
        atr,
        h,
        l,
        long_flags,
        short_flags,
        peak_favorable: float,
    ) -> tuple[bool, str]:
        if side > 0:
            if exit_rule == "exit_ema21_break" and c.iloc[i] < e21.iloc[i]:
                return True, exit_rule
            if exit_rule == "exit_rsi_extreme" and pd.notna(r.iloc[i]) and r.iloc[i] >= 80:
                return True, exit_rule
            if exit_rule == "exit_rsi_roll_long" and i >= 2:
                if pd.notna(r.iloc[i]) and pd.notna(r.iloc[i - 1]) and r.iloc[i - 1] > 72 and r.iloc[i] < 68:
                    return True, exit_rule
            if exit_rule == "exit_scale_rsi70_long" and pd.notna(r.iloc[i]) and r.iloc[i] >= 75:
                return True, exit_rule
        else:
            if exit_rule == "exit_ema21_break" and c.iloc[i] > e21.iloc[i]:
                return True, exit_rule
            if exit_rule == "exit_rsi_extreme" and pd.notna(r.iloc[i]) and r.iloc[i] <= 20:
                return True, exit_rule
            if exit_rule == "exit_rsi_roll_short" and i >= 2:
                if pd.notna(r.iloc[i]) and pd.notna(r.iloc[i - 1]) and r.iloc[i - 1] < 28 and r.iloc[i] > 32:
                    return True, exit_rule
            if exit_rule == "exit_scale_rsi70_long" and pd.notna(r.iloc[i]) and r.iloc[i] <= 25:
                return True, exit_rule
        if exit_rule == "exit_time_24bars" and i - entry_i >= 24:
            return True, exit_rule
        if exit_rule == "exit_atr_trail_1p5":
            stop = entry_px - 1.5 * atr.iloc[entry_i] if side > 0 else entry_px + 1.5 * atr.iloc[entry_i]
            if side > 0 and l.iloc[i] <= stop:
                return True, exit_rule
            if side < 0 and h.iloc[i] >= stop:
                return True, exit_rule
            if side > 0 and peak_favorable >= atr.iloc[entry_i] and l.iloc[i] <= entry_px:
                return True, exit_rule + "_be"
        if exit_rule == "exit_regime_lost" and i >= entry_i + 2:
            if side > 0 and not long_flags[i] and not long_flags[i - 1]:
                return True, exit_rule
            if side < 0 and not short_flags[i] and not short_flags[i - 1]:
                return True, exit_rule
        if exit_rule == "exit_ema21_extension" and pd.notna(atr.iloc[i]):
            if extension_exit_trigger(
                side, i - entry_i, entry_px, float(c.iloc[i]), float(e21.iloc[i]), float(atr.iloc[i])
            ):
                return True, exit_rule
        return False, ""

    return fn


def simulate_one(
    h1: pd.DataFrame,
    stk: dict,
    entry_i: int,
    side: int,
    entry_rule: str,
    exit_rule: str,
    chart_state: str,
    grade: str,
    long_flags: list[bool],
    short_flags: list[bool],
) -> SimTrade | None:
    c, h, l = stk["close"], stk["high"], stk["low"]
    e21, s100, r, atr = stk["ema21"], stk["smma100"], stk["rsi"], stk["atr"]

    ei = entry_i + 1
    if ei >= len(h1) - 1:
        return None
    entry_px = float(c.iloc[ei])
    pip = 0.01 if "JPY" in str(h1.attrs.get("sym", "")) else 0.0001
    peak = 0.0
    check = exit_checker(exit_rule)

    for j in range(ei + 1, min(len(h1), ei + MAX_HOLD)):
        if side > 0:
            peak = max(peak, float(h.iloc[j] - entry_px))
        else:
            peak = max(peak, float(entry_px - l.iloc[j]))
        hit, _ = check(side, j, ei, entry_px, c, e21, s100, r, atr, h, l, long_flags, short_flags, peak)
        if hit:
            exit_px = float(c.iloc[j])
            if side > 0:
                gross = (exit_px - entry_px) / entry_px - COST_BPS / 10000.0
                pips = (exit_px - entry_px) / pip
            else:
                gross = (entry_px - exit_px) / entry_px - COST_BPS / 10000.0
                pips = (entry_px - exit_px) / pip
            return SimTrade(
                entry_i=ei,
                exit_i=j,
                side=side,
                entry_rule=entry_rule,
                exit_rule=exit_rule,
                chart_state=chart_state,
                chart_grade=grade,
                return_pct=gross * 100,
                pips=float(pips),
            )
    j = min(len(h1) - 1, ei + MAX_HOLD - 1)
    exit_px = float(c.iloc[j])
    gross = (
        (exit_px - entry_px) / entry_px if side > 0 else (entry_px - exit_px) / entry_px
    ) - COST_BPS / 10000.0
    pips = ((exit_px - entry_px) if side > 0 else (entry_px - exit_px)) / pip
    return SimTrade(
        entry_i=ei,
        exit_i=j,
        side=side,
        entry_rule=entry_rule,
        exit_rule=exit_rule,
        chart_state=chart_state,
        chart_grade=grade,
        return_pct=gross * 100,
        pips=float(pips),
    )


def hold_advice_long(i: int, c, e21, r, long_flags, grade: str) -> str:
    if grade == "NO_TRADE":
        return "FLAT — chart not in long regime"
    if pd.notna(r.iloc[i]) and r.iloc[i] >= 78:
        return "CLOSE or trim — RSI extended toward 80"
    if c.iloc[i] < e21.iloc[i]:
        return "CLOSE — close below EMA21 (trail rule)"
    if grade == "GOOD" and long_flags[i] and pd.notna(r.iloc[i]) and 50 < r.iloc[i] < 72:
        return "HOLD — stack intact, RSI in trend zone"
    return "WAIT / reduce — transition or weak RSI"


def hold_advice_short(i: int, c, e21, r, short_flags, grade: str) -> str:
    if grade == "NO_TRADE":
        return "FLAT — chart not in short regime"
    if pd.notna(r.iloc[i]) and r.iloc[i] <= 22:
        return "COVER — RSI oversold (20 zone)"
    if c.iloc[i] > e21.iloc[i]:
        return "COVER — close above EMA21"
    if grade == "GOOD" and short_flags[i] and pd.notna(r.iloc[i]) and 28 < r.iloc[i] < 50:
        return "HOLD — bear stack, RSI below midline"
    return "WAIT / reduce — transition or RSI not confirming"


def enter_advice(
    signals: list[tuple[str, int]],
    grade_long: str,
    grade_short: str,
    win_lookup: dict,
) -> str:
    if not signals:
        if grade_long == "NO_TRADE" and grade_short == "NO_TRADE":
            return "DO NOT ENTER — chop or no stack"
        return "DO NOT ENTER — setup not aligned (RSI or pattern)"
    parts = []
    for rule, side in signals:
        g = grade_long if side > 0 else grade_short
        key = (rule, g)
        wr = win_lookup.get(key)
        wr_txt = f"hist win {wr:.0f}%" if wr is not None else "hist win n/a"
        if g == "GOOD":
            parts.append(f"ENTER OK ({rule}) — chart GOOD; {wr_txt}")
        else:
            parts.append(f"SKIP ({rule}) — chart {g}; {wr_txt}")
    return " | ".join(parts)


def run_lab(sym: str = "EURUSD", ema_mid: int = 50, window: tuple[str, str] | None = None) -> None:
    h1 = load_h1(sym)
    h1.attrs["sym"] = sym
    stk = build_stack(h1, ema_mid=ema_mid)
    c, o, h, l = stk["close"], stk["open"], stk["high"], stk["low"]
    e21, e50, s100, r, atr = stk["ema21"], stk["ema50"], stk["smma100"], stk["rsi"], stk["atr"]

    long_flags = [stack_long(c, e21, e50, s100, i) for i in range(len(h1))]
    short_flags = [stack_short(c, e21, e50, s100, i) for i in range(len(h1))]

    state_rows = []
    trade_rows: list[dict] = []
    day_rule_seen: set[tuple] = set()

    for i in range(110, len(h1) - 2):
        state, gl, gs = chart_state_at(i, c, e21, e50, s100, r, atr, long_flags, short_flags)
        signals = detect_entries(i, o, h, l, c, e21, e50, s100, r, atr, long_flags, short_flags)
        state_rows.append(
            {
                "time": h1.index[i],
                "chart_state": state,
                "grade_long": gl,
                "grade_short": gs,
                "rsi": round(float(r.iloc[i]), 2) if pd.notna(r.iloc[i]) else None,
                "close": float(c.iloc[i]),
                "stack_long": stack_long(c, e21, e50, s100, i),
                "stack_short": stack_short(c, e21, e50, s100, i),
                "chop_smma": chop_near_smma(c, s100, atr, i),
                "n_entry_signals": len(signals),
                "hold_long_advice": hold_advice_long(i, c, e21, r, long_flags, gl),
                "hold_short_advice": hold_advice_short(i, c, e21, r, short_flags, gs),
            }
        )
        dk = h1.index[i].normalize()
        for rule, side in signals:
            grade = gl if side > 0 else gs
            if grade != "GOOD":
                continue
            key = (dk, rule, side)
            if key in day_rule_seen:
                continue
            day_rule_seen.add(key)
            exits = LONG_EXITS if side > 0 else SHORT_EXITS
            for ex_use in exits:
                t = simulate_one(h1, stk, i, side, rule, ex_use, state, grade, long_flags, short_flags)
                if not t:
                    continue
                trade_rows.append(
                    {
                        **t.__dict__,
                        "signal_time": h1.index[i],
                        "entry_time": h1.index[t.entry_i],
                        "exit_time": h1.index[t.exit_i],
                        "split": split_for_time(h1.index[t.exit_i]),
                    }
                )

    states = pd.DataFrame(state_rows)
    tdf = pd.DataFrame(trade_rows)

    design = tdf[tdf["split"] == "design"]
    win_lookup: dict = {}
    for (rule, grade), g in design.groupby(["entry_rule", "chart_grade"]):
        if len(g) < 20:
            continue
        win_lookup[(rule, grade)] = 100 * (g["pips"] > 0).mean()

    # attach enter advice on states (sample every bar with signals only for size)
    adv_rows = []
    for i in range(110, len(h1) - 2):
        state, gl, gs = chart_state_at(i, c, e21, e50, s100, r, atr, long_flags, short_flags)
        signals = detect_entries(i, o, h, l, c, e21, e50, s100, r, atr, long_flags, short_flags)
        if not signals and state not in ("STACK_LONG", "STACK_SHORT", "CHOP_SMMA"):
            continue
        adv_rows.append(
            {
                "time": h1.index[i],
                "chart_state": state,
                "enter_advice": enter_advice(signals, gl, gs, win_lookup),
            }
        )
    advice = pd.DataFrame(adv_rows)

    matrix = (
        tdf.groupby(["entry_rule", "exit_rule", "chart_state", "chart_grade", "split"])
        .agg(
            n=("pips", "count"),
            winrate=("pips", lambda s: round(100 * (s > 0).mean(), 1)),
            exp_bps=("return_pct", lambda s: round(s.mean() * 100, 2)),
            avg_pips=("pips", lambda s: round(s.mean(), 1)),
        )
        .reset_index()
    )

    good_bad = (
        states.groupby("chart_state")
        .agg(bars=("time", "count"), pct_time=("time", lambda s: 0))
        .reset_index()
    )
    good_bad["pct_time"] = (100 * good_bad["bars"] / len(states)).round(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states.to_csv(OUT_DIR / f"fxmopol_decision_states_{sym}.csv", index=False)
    advice.to_csv(OUT_DIR / f"fxmopol_decision_advice_{sym}.csv", index=False)
    matrix.to_csv(OUT_DIR / f"fxmopol_decision_matrix_{sym}.csv", index=False)
    good_bad.to_csv(OUT_DIR / f"fxmopol_chart_quality_mix_{sym}.csv", index=False)

    manifest = {
        "symbol": sym,
        "ema_mid": ema_mid,
        "entry_rules": ENTRY_RULES_DOC,
        "exit_rules": EXIT_RULES_DOC,
        "questions": {
            "why_enter_now": "Use enter_advice: GOOD grade + entry rule + historical win% from design split.",
            "why_hold_or_close": "Use hold_*_advice columns: EMA21 break, RSI 80/20, stack grade.",
            "will_i_win_if_i_enter": "Filter matrix where chart_grade=GOOD and split=validation or OOS; check winrate & exp_bps.",
        },
        "outputs": {
            "states": f"fxmopol_decision_states_{sym}.csv",
            "advice": f"fxmopol_decision_advice_{sym}.csv",
            "matrix": f"fxmopol_decision_matrix_{sym}.csv",
        },
    }
    (OUT_DIR / f"fxmopol_decision_manifest_{sym}.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    write_rules_md(sym, manifest, matrix, good_bad, window)

    if window:
        w0, w1 = window
        sub = states[(states["time"] >= w0) & (states["time"] <= w1)]
        sub.to_csv(OUT_DIR / f"fxmopol_decision_window_{sym}.csv", index=False)
        print(f"\n=== Chart window {w0} -> {w1} ({len(sub)} bars) ===")
        print(sub.groupby("chart_state")["time"].count().to_string())
        print("\nSample bars (state + advice):")
        samp = sub.merge(advice, on=["time", "chart_state"], how="left").dropna(subset=["enter_advice"]).head(12)
        for _, row in samp.iterrows():
            print(f"  {row['time']} | {row['chart_state']} | {row.get('enter_advice', '')[:80]}")

    print(f"\nWrote decision lab CSVs + docs/FXMOPOL_DECISION_RULES.md for {sym}")
    print("=== OOS: entry×exit (chart_grade=GOOD only, top by exp_bps) ===")
    oos = matrix[(matrix["split"] == "OOS") & (matrix["chart_grade"] == "GOOD") & (matrix["n"] >= 15)]
    print(oos.sort_values("exp_bps", ascending=False).head(8).to_string(index=False))


def split_for_time(t: pd.Timestamp) -> str:
    for name, (a, b) in SPLITS.items():
        if t >= pd.Timestamp(a) and t < pd.Timestamp(b):
            return name
    return "design"


def _df_md(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_rules_md(sym: str, manifest: dict, matrix: pd.DataFrame, good_bad: pd.DataFrame, window) -> None:
    md = Path(__file__).resolve().parent.parent / "docs" / "FXMOPOL_DECISION_RULES.md"
    lines = [
        "# FXMOPOL decision rules — history-built Q&A",
        "",
        "Full indicator stack on H1: **EMA21, EMA50, SMMA100, RSI(9) 20/50/80, fractals, ATR(14)**.",
        "",
        "## Your three questions",
        "",
        "| Question | Where answered |",
        "|----------|----------------|",
        f"| **Why enter now?** | `research/results/fxmopol_decision_advice_{sym}.csv` → `enter_advice` |",
        f"| **Why hold or close?** | `fxmopol_decision_states_{sym}.csv` → `hold_long_advice` / `hold_short_advice` |",
        f"| **Will I win if I enter now?** | `fxmopol_decision_matrix_{sym}.csv` → `winrate`, `exp_bps` by `chart_grade` + split |",
        "",
        "**Rule:** Only trust `chart_grade=GOOD` rows for entries. **CHOP_SMMA** = flat (matches screenshots).",
        "",
        "## Chart states (what looks good vs not)",
        "",
        "| State | Meaning | Trade? |",
        "|-------|---------|--------|",
        "| `STACK_LONG` + grade **GOOD** | Fan aligned, RSI 45–80 | Long setups only |",
        "| `STACK_SHORT` + grade **GOOD** | Bear fan, RSI 20–55 | Short setups only |",
        "| `CHOP_SMMA` | Price tangling white SMMA | **No trade** |",
        "| `TRANSITION_*` | Stack forming, not 5-bar streak | Wait |",
        "| grade **WAIT** | Right stack but RSI wrong (e.g. short at RSI 26) | **No new entry** / cover |",
        "",
        "## Entry rules (catalog)",
        "",
    ]
    for k, v in manifest["entry_rules"].items():
        lines.append(f"- **{k}** — {v}")
    lines.extend(["", "## Exit & management rules", ""])
    for k, v in manifest["exit_rules"].items():
        lines.append(f"- **{k}** — {v}")
    lines.extend(
        [
            "",
            "## Time in each state (% of H1 bars)",
            "",
            _df_md(good_bad),
            "",
            "## Best combos (OOS, GOOD grade, n≥15)",
            "",
        ]
    )
    oos = matrix[(matrix["split"] == "OOS") & (matrix["chart_grade"] == "GOOD") & (matrix["n"] >= 15)]
    top = oos.sort_values("exp_bps", ascending=False).head(15)
    lines.append("| entry | exit | state | winrate | exp_bps | n |")
    lines.append("|-------|------|-------|---------|---------|---|")
    for _, r in top.iterrows():
        lines.append(
            f"| {r['entry_rule']} | {r['exit_rule']} | {r['chart_state']} | {r['winrate']} | {r['exp_bps']} | {r['n']} |"
        )
    lines.append("")
    lines.append("Regenerate: `research/fxmopol_decision_lab.py`")
    md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--ema-mid", type=int, default=50)
    ap.add_argument(
        "--window",
        nargs=2,
        metavar=("START", "END"),
        default=("2020-06-17", "2020-07-24"),
        help="Example chart window for stdout (Jul 2020 screenshots).",
    )
    args = ap.parse_args()
    run_lab(args.symbol, args.ema_mid, tuple(args.window))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
