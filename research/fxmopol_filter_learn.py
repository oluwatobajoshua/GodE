#!/usr/bin/env python3
"""Learn timing filters from baseline playbook trades (design only), score val/OOS.

Workflow (user rule):
  1. Run chart stack with **no** session/weekday gates → many trades.
  2. Drop buckets that lose on DESIGN (hour + weekday).
  3. Re-score validation + OOS with frozen filters (no peeking).

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_filter_learn.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fxmopol_chart_core import DEFAULT_WEEKDAY_MASK
from fxmopol_playbook import SPLITS, load_h1, metrics, replay_trades

HERE = Path(__file__).resolve().parent
OUT_JSON = HERE / "results" / "fxmopol_learned_filters.json"
OUT_CSV = HERE / "results" / "fxmopol_filter_learn_summary.csv"
SYMS = ("GBPUSD", "EURUSD", "USDJPY")
MIN_BUCKET = 4  # min design trades per hour/dow to consider


def pf(s: pd.Series) -> float:
    w, l = s[s > 0], s[s < 0]
    if len(l) == 0 or l.sum() == 0:
        return 0.0
    return float(w.sum() / abs(l.sum()))


def profit_kpis(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"n": 0, "loss_rate": 0, "payoff_ratio": 0, "exp_bps": 0, "pf": 0}
    r = trades["return_pct"] / 100.0
    wins = r[r > 0]
    losses = r[r < 0]
    payoff = float(wins.mean() / abs(losses.mean())) if len(losses) and len(wins) else 0.0
    return {
        "n": len(trades),
        "loss_rate": round(100 * (r < 0).mean(), 1),
        "payoff_ratio": round(payoff, 2),
        "exp_bps": round(r.mean() * 10000, 2),
        "pf": round(pf(r), 2),
    }


def learn_from_design(trades: pd.DataFrame) -> tuple[int, list[int], list[str]]:
    """Returns (weekday_mask, allowed_hours) from design split only."""
    et = pd.to_datetime(trades["entry_time"])
    design = trades[(et >= SPLITS["design"][0]) & (et < SPLITS["design"][1])].copy()
    if design.empty:
        return 0, [], []

    design["dow"] = pd.to_datetime(design["entry_time"]).dt.dayofweek
    design["hour"] = pd.to_datetime(design["entry_time"]).dt.hour

    mask = 0
    for dow in range(5):
        g = design[design["dow"] == dow]
        if len(g) < MIN_BUCKET:
            continue
        r = g["return_pct"] / 100.0
        if r.mean() > 0 and pf(r) >= 1.0:
            mask |= 1 << dow

    hours: list[int] = []
    for hour, g in design.groupby("hour"):
        if len(g) < MIN_BUCKET:
            continue
        r = g["return_pct"] / 100.0
        if r.mean() > 0 and pf(r) >= 1.0:
            hours.append(int(hour))
    hours.sort()

    reasons: list[str] = []
    if "entry_reason" in design.columns:
        for reason, g in design.groupby("entry_reason"):
            if len(g) < MIN_BUCKET:
                continue
            r = g["return_pct"] / 100.0
            if r.mean() > 0 and pf(r) >= 1.05:
                reasons.append(str(reason))

    return mask, hours, reasons


def series_from_trades(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    return pd.Series(trades["return_pct"].values / 100.0, index=pd.to_datetime(trades["exit_time"]))


def baseline_trades(sym: str, mid: int, *, prime: bool) -> pd.DataFrame:
    h1 = load_h1(sym)
    return replay_trades(
        h1,
        ema_mid=mid,
        symbol=sym,
        good_sessions_only=prime,
        weekday_mask=DEFAULT_WEEKDAY_MASK if prime else 0,
        allowed_hours=None,
    )


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--baseline",
        choices=("loose", "prime"),
        default="prime",
        help="prime = London/overlap + Tue-Thu (default for promotion)",
    )
    args = ap.parse_args()
    use_prime = args.baseline == "prime"

    manifest: dict = {
        "method": "design bucket mean>0 & PF>=1",
        "min_bucket": MIN_BUCKET,
        "baseline": args.baseline,
        "symbols": {},
    }
    rows = []

    for sym in SYMS:
        try:
            mid = 100 if sym == "USDJPY" else 50
            baseline = baseline_trades(sym, mid, prime=use_prime)
            mask, hours, reasons = learn_from_design(baseline)

            filtered = replay_trades(
                load_h1(sym),
                ema_mid=mid,
                symbol=sym,
                good_sessions_only=use_prime,
                weekday_mask=mask if mask else (DEFAULT_WEEKDAY_MASK if use_prime else 0),
                allowed_hours=hours if hours else None,
                allowed_entry_reasons=reasons if reasons else None,
            )

            manifest["symbols"][sym] = {
                "weekday_mask": mask,
                "allowed_hours_utc": hours,
                "allowed_entry_reasons": reasons,
                "baseline_kpis": profit_kpis(baseline),
                "filtered_kpis_all": profit_kpis(filtered),
                "baseline_trades": len(baseline),
                "filtered_trades": len(filtered),
            }

            for label, tr in (("baseline", baseline), ("learned_filter", filtered)):
                r = series_from_trades(tr)
                for split, (a, b) in SPLITS.items():
                    if len(r) == 0:
                        part = r
                    else:
                        a_ts, b_ts = pd.Timestamp(a), pd.Timestamp(b)
                        part = r[(r.index >= a_ts) & (r.index < b_ts)]
                    rows.append(
                        {
                            "symbol": sym,
                            "mode": label,
                            "split": split,
                            **metrics(part),
                        }
                    )
        except Exception as exc:
            import traceback

            manifest["symbols"][sym] = {"error": str(exc), "traceback": traceback.format_exc()}
            print(f"[filter_learn] {sym} ERROR: {exc}", flush=True)

    OUT_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)

    print("=== Learn from baseline (design) -> apply frozen to val/OOS ===")
    for sym in SYMS:
        m = manifest["symbols"][sym]
        print(sym, "mask", m["weekday_mask"], "hours", m["allowed_hours_utc"], "| trades", m["baseline_trades"], "->", m["filtered_trades"])
    print(f"\nWrote {OUT_JSON.name}, {OUT_CSV.name}")
    df = pd.DataFrame(rows)
    for sym in SYMS:
        print(f"\n--- {sym} OOS ---")
        for mode in ("baseline", "learned_filter"):
            row = df[(df.symbol == sym) & (df.mode == mode) & (df.split == "OOS")]
            if len(row):
                print(mode, row.iloc[0].to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
