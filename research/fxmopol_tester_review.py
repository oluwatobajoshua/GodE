#!/usr/bin/env python3
"""Parse MT5 Strategy Tester HTML — tag wins/losses with chart context (fxmopol stack).

Run after any tester backtest:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_tester_review.py backtest\\reports\\fxmopol_gbp_playbook_GBPUSD_H1.htm
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results"
PROGRESS = HERE.parent / "backtest" / "tester_progress.jsonl"

from fxmopol_chart_core import (  # noqa: E402
    build_stack,
    chop_near_smma,
    ema_fan_spreads,
    entry_near_ema21,
    passes_entry_filters,
    stack_long,
    stack_short,
)
from fxmopol_chart_core import chart_state_at  # noqa: E402
from fxmopol_playbook import load_h1  # noqa: E402


def parse_deals(html: str) -> pd.DataFrame:
    """Extract deal rows after <b>Deals</b> header."""
    start = html.find("<b>Deals</b>")
    if start < 0:
        return pd.DataFrame()
    chunk = html[start:]
    rows = []
    for m in re.finditer(
        r'<tr[^>]*><td[^>]*>([\d.\s:]+)</td><td>(\d+)</td><td>([^<]*)</td><td>([^<]*)</td>'
        r"<td>([^<]*)</td><td>([\d.\s]+)</td><td>([\d.\s]*)</td><td>(\d*)</td>"
        r"<td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td><td>([^<]*)</td>",
        chunk,
    ):
        rows.append(
            {
                "time": m.group(1).strip(),
                "deal": int(m.group(2)),
                "symbol": m.group(3).strip(),
                "deal_type": m.group(4).strip(),
                "direction": m.group(5).strip(),
                "volume": float(m.group(6).replace(" ", "")) if m.group(6).strip() else 0,
                "price": float(m.group(7).replace(" ", "")) if m.group(7).strip() else None,
                "profit": float(m.group(11).replace(" ", "").replace(",", "")) if m.group(11).strip() else 0,
                "balance": m.group(12).strip(),
                "comment": m.group(13).strip(),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df[df["deal_type"].isin(("buy", "sell"))]


def pair_round_trips(deals: pd.DataFrame) -> pd.DataFrame:
    if deals.empty or "direction" not in deals.columns:
        return pd.DataFrame()
    ins = deals[deals["direction"] == "in"].copy()
    outs = deals[deals["direction"] == "out"].copy()
    rows = []
    for _, inn in ins.iterrows():
        out = outs[(outs["time"] > inn["time"]) & (outs["symbol"] == inn["symbol"])].head(1)
        if out.empty:
            continue
        o = out.iloc[0]
        side = 1 if inn["deal_type"] == "buy" else -1
        exit_reason = classify_exit(o["comment"])
        rows.append(
            {
                "entry_time": inn["time"],
                "exit_time": o["time"],
                "symbol": inn["symbol"],
                "side": "LONG" if side > 0 else "SHORT",
                "entry_price": inn["price"],
                "exit_price": o["price"],
                "profit": o["profit"],
                "win": o["profit"] > 0,
                "exit_reason": exit_reason,
                "bars_held": int((o["time"] - inn["time"]).total_seconds() // 3600),
            }
        )
    return pd.DataFrame(rows)


def classify_exit(comment: str) -> str:
    c = comment.lower()
    if c.startswith("sl"):
        return "stop_loss"
    if c.startswith("tp"):
        return "take_profit"
    if "extension" in c:
        return "ema_extension"
    if not c:
        return "signal_exit"
    return comment or "unknown"


def enrich_chart(trades: pd.DataFrame, sym: str, ema_mid: int = 50) -> pd.DataFrame:
    try:
        h1 = load_h1(sym)
    except FileNotFoundError:
        return trades
    stk = build_stack(h1, ema_mid=ema_mid)
    c, h, l, e21, e50, s100, r, atr = (
        stk["close"],
        stk["high"],
        stk["low"],
        stk["ema21"],
        stk["ema50"],
        stk["smma100"],
        stk["rsi"],
        stk["atr"],
    )
    long_flags = [stack_long(c, e21, e50, s100, i) for i in range(len(h1))]
    short_flags = [stack_short(c, e21, e50, s100, i) for i in range(len(h1))]
    idx = h1.index

    def bar_i(t: pd.Timestamp) -> int:
        pos = idx.searchsorted(t, side="right") - 1
        return max(0, min(pos, len(h1) - 1))

    states, grades, rsi_v, prox, fan, chop_flags, stay = [], [], [], [], [], [], []
    for _, t in trades.iterrows():
        i = bar_i(t["entry_time"])
        side = 1 if t["side"] == "LONG" else -1
        state, gl, gs = chart_state_at(i, c, e21, e50, s100, r, atr, long_flags, short_flags)
        grade = gl if side > 0 else gs
        s1, s2 = ema_fan_spreads(e21, e50, s100, atr, i, side)
        prox_ok = entry_near_ema21(c, h, l, e21, atr, i, side)
        pf = passes_entry_filters(c, h, l, e21, e50, s100, atr, i, side)
        is_chop = chop_near_smma(c, s100, atr, i)
        stay_away = is_chop or grade in ("NO_TRADE", "WAIT") or not pf
        states.append(state)
        grades.append(grade)
        rsi_v.append(float(r.iloc[i]) if pd.notna(r.iloc[i]) else None)
        prox.append(prox_ok)
        fan.append(round(min(s1, s2), 3))
        chop_flags.append(is_chop)
        stay.append(stay_away)
    trades = trades.copy()
    trades["chart_state"] = states
    trades["chart_grade"] = grades
    trades["rsi_at_entry"] = rsi_v
    trades["near_ema21"] = prox
    trades["min_fan_spread_atr"] = fan
    trades["chop_smma"] = chop_flags
    trades["should_have_skipped"] = stay
    return trades


def summarize(trades: pd.DataFrame, report_path: Path) -> dict:
    if trades.empty:
        return {"n": 0}
    wins = trades[trades["win"]]
    losses = trades[~trades["win"]]
    bad = trades[trades["should_have_skipped"]]
    summary = {
        "report": str(report_path),
        "n_trades": len(trades),
        "win_rate": round(100 * trades["win"].mean(), 1),
        "net_profit": round(trades["profit"].sum(), 2),
        "avg_win": round(wins["profit"].mean(), 2) if len(wins) else 0,
        "avg_loss": round(losses["profit"].mean(), 2) if len(losses) else 0,
        "losses_from_bad_entries": int((~trades["win"] & trades["should_have_skipped"]).sum()),
        "profit_given_back_after_3_wins": round(
            trades.iloc[3:]["profit"].clip(upper=0).sum(), 2
        )
        if len(trades) > 3
        else 0,
    }
    by_exit = trades.groupby("exit_reason").agg(n=("profit", "count"), net=("profit", "sum"), wr=("win", "mean"))
    by_state = trades.groupby("chart_state").agg(n=("profit", "count"), net=("profit", "sum"))
    summary["by_exit"] = by_exit.reset_index().to_dict(orient="records")
    summary["by_chart_state"] = by_state.reset_index().to_dict(orient="records")
    return summary


def print_report(trades: pd.DataFrame, summary: dict) -> None:
    print("=== Tester trade review (wins / losses / stay-away) ===")
    print(json.dumps(summary, indent=2)[:2000])
    if trades.empty:
        return
    print("\n--- Losing trades (study these) ---")
    for _, t in trades[~trades["win"]].tail(15).iterrows():
        flag = "SHOULD_SKIP" if t["should_have_skipped"] else "grade_ok"
        print(
            f"  {t['entry_time']} {t['side']} {t['profit']:.2f} | {t['exit_reason']} | "
            f"{t['chart_state']} / {t['chart_grade']} | RSI={t['rsi_at_entry']} | {flag}"
        )
    print("\n--- Winning trades (repeat these conditions) ---")
    for _, t in trades[trades["win"]].tail(10).iterrows():
        print(
            f"  {t['entry_time']} {t['side']} +{t['profit']:.2f} | {t['exit_reason']} | "
            f"{t['chart_state']} fan={t['min_fan_spread_atr']} near21={t['near_ema21']}"
        )
    skip = trades[trades["should_have_skipped"]]
    if len(skip):
        print(f"\nStay-away rule: {len(skip)} entries would be blocked by chart filters "
              f"(saved est. {skip[~skip['win']]['profit'].sum():.2f} on losers).")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: fxmopol_tester_review.py <tester_report.htm>")
        return 1
    report_path = Path(sys.argv[1])
    if not report_path.exists():
        print("Missing", report_path)
        return 1
    html = report_path.read_text(encoding="utf-16", errors="ignore")
    if "Deals" not in html:
        html = report_path.read_text(encoding="utf-8", errors="ignore")
    deals = parse_deals(html)
    trades = pair_round_trips(deals)
    sym = trades["symbol"].iloc[0] if len(trades) else "EURUSD"
    ema_mid = 100 if "JPY" in sym else 50
    trades = enrich_chart(trades, sym, ema_mid=ema_mid)
    stem = report_path.stem
    OUT.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUT / f"tester_trades_{stem}.csv", index=False)
    summary = summarize(trades, report_path)
    summary_path = OUT / f"tester_review_{stem}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with PROGRESS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary) + "\n")
    print_report(trades, summary)
    print(f"\nWrote {OUT / f'tester_trades_{stem}.csv'}, {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
