#!/usr/bin/env python3
"""History replay: map playbook entries/exits on each bar (EURUSD H1 + example week).

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_playbook_replay.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT_TRADES = HERE / "results" / "fxmopol_playbook_trades_EURUSD.csv"
OUT_WEEK = HERE / "results" / "fxmopol_playbook_week_2025-11-02_EURUSD.csv"
OUT_MAP = HERE.parent / "docs" / "FXMOPOL_TRADE_MAP.md"
EXAMPLE_START = "2025-11-02"
EXAMPLE_END = "2025-11-08"

from fxmopol_playbook import load_h1, replay_trades  # noqa: E402
from period_returns import publish_results  # noqa: E402


def manual_chart_calls(week: pd.DataFrame) -> list[dict]:
    """Discretionary map from user EUR chart (docs/FXMOPOL_CHART_SNAPSHOT.md) for same week."""
    return [
        {
            "when": "Nov 2-3",
            "action": "FLAT",
            "zone": "~1.1180 chop through SMMA100",
            "note": "No stack — skip",
        },
        {
            "when": "Late Nov 3 / early Nov 4",
            "action": "ENTER LONG",
            "zone": "1.1195-1.1210",
            "note": "Stack bullish, RSI>50, pullback EMA21",
        },
        {
            "when": "Near 1.1266 peak",
            "action": "EXIT LONG",
            "zone": "1.124-1.126",
            "note": "Take profit or close below EMA21",
        },
        {
            "when": "Mid Nov 5",
            "action": "ENTER SHORT",
            "zone": "1.120-1.121",
            "note": "Below all MAs, retest EMA21 from below",
        },
        {
            "when": "Flush ~1.1136, RSI~26",
            "action": "EXIT SHORT",
            "zone": "1.114-1.115",
            "note": "RSI oversold / cover",
        },
    ]


def write_map_md(full: pd.DataFrame, week: pd.DataFrame, week_pips: float) -> None:
    manual = manual_chart_calls(week)
    lines = [
        "# FXMOPOL trade map — EURUSD H1 playbook replay",
        "",
        "Rules: EMA21 + EMA50 + SMMA100 stack, RSI(9) levels 20/50/80.",
        "Full trade log: `research/results/fxmopol_playbook_trades_EURUSD.csv`.",
        "",
        f"## Example week {EXAMPLE_START} → {EXAMPLE_END} (automated playbook)",
        "",
        f"**Week total (automated):** {week_pips:.1f} pips on {len(week)} round-trips.",
        "",
    ]
    if len(week):
        lines.append("| Entry (UTC) | Exit (UTC) | Side | Entry | Exit | Pips | Entry why | Exit why |")
        lines.append("|-------------|------------|------|-------|------|------|-----------|----------|")
        for _, t in week.iterrows():
            lines.append(
                f"| {t['entry_time']} | {t['exit_time']} | {t['side']} | {t['entry_price']} | "
                f"{t['exit_price']} | {t['pips']} | {t['entry_reason']} | {t['exit_reason']} |"
            )
    else:
        lines.append("_No automated trades in this window (regime/RSI/pullback filters)._")
    lines.extend(["", "## Your chart — discretionary map (same week)", ""])
    for m in manual:
        lines.append(f"- **{m['when']}** — **{m['action']}** @ {m['zone']}: {m['note']}")
    lines.extend(
        [
            "",
            "## Full history summary",
            "",
            f"- Total trades: **{len(full)}**",
            f"- Win rate: **{100 * (full['pips'] > 0).mean():.1f}%**",
            f"- Sum pips (no compounding): **{full['pips'].sum():.1f}**",
            "",
            "Monthly P&L: run `publish_results` output in `playbook_EURUSD_mid50_monthly.csv`.",
        ]
    )
    OUT_MAP.parent.mkdir(parents=True, exist_ok=True)
    OUT_MAP.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    sym = "EURUSD"
    h1 = load_h1(sym)
    trades = replay_trades(h1, ema_mid=50)
    trades.to_csv(OUT_TRADES, index=False)

    ex0, ex1 = pd.Timestamp(EXAMPLE_START), pd.Timestamp(EXAMPLE_END)
    week = trades[(pd.to_datetime(trades["entry_time"]) >= ex0) & (pd.to_datetime(trades["entry_time"]) < ex1)].copy()
    week.to_csv(OUT_WEEK, index=False)
    week_pips = float(week["pips"].sum()) if len(week) else 0.0

    r = pd.Series(trades["return_pct"].values / 100.0, index=pd.to_datetime(trades["exit_time"]))
    publish_results("playbook_EURUSD_replay", r)

    write_map_md(trades, week, week_pips)

    print(f"=== EURUSD playbook history: {len(trades)} trades ===")
    print(f"Win rate {100*(trades['pips']>0).mean():.1f}%  Sum pips {trades['pips'].sum():.1f}")
    print(f"\n=== Example week {EXAMPLE_START} .. {EXAMPLE_END} ===")
    if len(week):
        print(week.to_string(index=False))
    else:
        print("(no trades — see discretionary map in FXMOPOL_TRADE_MAP.md)")
    print(f"\nWrote {OUT_TRADES}, {OUT_WEEK}, {OUT_MAP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
