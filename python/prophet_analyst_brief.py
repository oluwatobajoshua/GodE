#!/usr/bin/env python3
"""Senior-analyst style brief from market_snapshot + prophet_forecasts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def brief(symbol: str) -> str:
    sym = symbol.upper()
    mt5 = Path(os.environ.get("SOVEREIGN_MT5_FILES", REPO / "backtest" / "mt5_files" / "Sovereign"))
    snap = _load(mt5 / "market_snapshot.json")
    if not snap:
        snap = _load(REPO / "research" / "results" / "market_snapshot.json")
    prophet = _load(mt5 / "prophet_forecasts.json") or _load(REPO / "research" / "results" / "prophet_forecasts.json")

    g = snap.get("global") or {}
    block = (snap.get("symbols") or {}).get(sym) or {}
    gates = block.get("gates") or snap.get("symbol_gates", {}).get(sym, {})
    pf = prophet.get(sym, {})

    lines = [
        f"=== Sovereign Analyst Brief — {sym} ===",
        f"Executive: {g.get('professor_headline', 'no snapshot')}",
        "",
        "1) What is the market doing?",
        f"   Technical: {block.get('technical', {}).get('chart_state')} "
        f"(D1 {block.get('technical', {}).get('d1', {}).get('bias')})",
        f"   Fundamental carry: {block.get('fundamental', {}).get('carry_diff_pct')}",
        f"   Sentiment: VIX={block.get('sentiment', {}).get('vix')} "
        f"COT={block.get('positioning', {}).get('crowding')}",
        f"   Events (USD): next hi-impact in "
        f"{block.get('events', {}).get('hours_to_next_high_impact_usd')}h",
        "",
        "2) Next-move probabilities (Prophet research, not live guarantee):",
    ]
    if not pf:
        lines.append("   (run .\\scripts\\run_prophet_research.ps1 to populate)")
    else:
        for tf, row in sorted(pf.items()):
            lines.append(
                f"   {tf}: P(up)={row.get('p_up')} → {row.get('next_move')} "
                f"(conf {row.get('confidence')}) @ {row.get('bar_time')}"
            )
    lines.extend(
        [
            "",
            "3) Risk desk gate (EA):",
            f"   verdict={gates.get('verdict')} mult={gates.get('risk_mult')} "
            f"block_L={gates.get('block_long')} block_S={gates.get('block_short')}",
            f"   {gates.get('professor_line', '')}",
            "",
            "4) What would change the call?",
            f"   Gaps to close: {(snap.get('knowledge_coverage') or {}).get('gaps_to_close', [])}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="GBPUSD")
    args = p.parse_args()
    print(brief(args.symbol))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
