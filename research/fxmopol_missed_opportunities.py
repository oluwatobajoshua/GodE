#!/usr/bin/env python3
"""Find clean playbook wins the Strategy Tester did not take (missed rides).

Compares ``fxmopol_playbook`` replay vs tester deal CSV/HTML on the same symbol.
Use after a tester run to see where on-time EMA21 entries would have paid.

    .\\.venv\\Scripts\\python.exe research\\fxmopol_missed_opportunities.py \\
        --report backtest\\reports\\fxmopol_gbp_playbook_GBPUSD_H1.htm \\
        --from 2024-01-01 --to 2026-01-01
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
from fxmopol_playbook import replay_trades, load_h1  # noqa: E402
from fxmopol_tester_review import parse_deals, pair_round_trips  # noqa: E402

OUT = HERE / "results"


def load_tester_trades(report: Path) -> pd.DataFrame:
    html = report.read_text(encoding="utf-16", errors="ignore")
    if "Deals" not in html:
        html = report.read_text(encoding="utf-8", errors="ignore")
    return pair_round_trips(parse_deals(html))


def miss_window(t0: pd.Timestamp, tester: pd.DataFrame, hours: int = 6) -> bool:
    if tester.empty:
        return True
    delta = pd.Timedelta(hours=hours)
    for _, t in tester.iterrows():
        if abs(t["entry_time"] - t0) <= delta:
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--from", dest="from_d", default="2024-01-01")
    ap.add_argument("--to", default="2026-01-01")
    ap.add_argument("--min-pips", type=float, default=15.0)
    ap.add_argument("--miss-hours", type=int, default=6)
    args = ap.parse_args()

    tester = load_tester_trades(args.report)
    sym = tester["symbol"].iloc[0] if len(tester) else "GBPUSD"
    h1 = load_h1(sym)
    mask = (h1.index >= args.from_d) & (h1.index < args.to)
    h1 = h1.loc[mask]
    ema_mid = 100 if "JPY" in sym else 50
    playbook = replay_trades(h1, ema_mid=ema_mid, good_sessions_only=True)
    if playbook.empty:
        print("No playbook trades in window.")
        return 0

    wins = playbook[playbook["return_pct"] > 0].copy()
    wins = wins[wins["pips"].abs() >= args.min_pips]
    missed = []
    for _, p in wins.iterrows():
        t0 = pd.Timestamp(p["entry_time"])
        if miss_window(t0, tester, args.miss_hours):
            missed.append(
                {
                    "entry_time": str(t0),
                    "side": p["side"],
                    "entry_reason": p["entry_reason"],
                    "exit_reason": p["exit_reason"],
                    "pips": p["pips"],
                    "return_pct": p["return_pct"],
                }
            )

    OUT.mkdir(parents=True, exist_ok=True)
    stem = args.report.stem
    out_csv = OUT / f"missed_opportunities_{stem}.csv"
    out_json = OUT / f"missed_opportunities_{stem}.json"
    df = pd.DataFrame(missed)
    df.to_csv(out_csv, index=False)
    summary = {
        "symbol": sym,
        "window": [args.from_d, args.to],
        "tester_trades": len(tester),
        "playbook_wins": int(len(wins)),
        "missed_clean_wins": len(missed),
        "missed_pips_sum": round(float(df["pips"].sum()), 1) if len(df) else 0.0,
        "top_reasons": df["entry_reason"].value_counts().head(5).to_dict() if len(df) else {},
    }
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("=== Missed opportunities (playbook win, no tester entry nearby) ===")
    print(json.dumps(summary, indent=2))
    if len(df):
        print("\n--- Last 12 missed rides ---")
        for row in df.tail(12).to_dict(orient="records"):
            print(
                f"  {row['entry_time']} {row['side']} {row['pips']:+.1f}p "
                f"| {row['entry_reason']} -> {row['exit_reason']} | {row['return_pct']:+.3f}%"
            )
    print(f"\nWrote {out_csv}, {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
