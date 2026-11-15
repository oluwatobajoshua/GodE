#!/usr/bin/env python3
"""FXMOPOL full-stack P&L — chart + sessions + fund + professor + governor (EA parity).

Hedge-fund workflow:
  1. Generate trades (same code path as EA playbook research).
  2. Apply professor blocks at entry (causal).
  3. Walk-forward performance governor (size / 14d blocks).
  4. Publish monthly/daily proof.

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_stack_pnl.py --symbol GBPUSD
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ecosystem_historical import CHOP_STATES, gate_at_bar, prep_liquidity_series, prep_vol_elevated
from fxmopol_chart_core import DEFAULT_WEEKDAY_MASK, session_allows_new_entry, weekday_allows_new_entry
from fxmopol_playbook import SPLITS, load_h1, metrics, replay_trades
from performance_governor import apply_governor
from period_returns import print_split_monthly, publish_results

HERE = Path(__file__).resolve().parent
OUT_MANIFEST = HERE / "results" / "fxmopol_stack_pnl_manifest.json"
LEARNED = HERE / "results" / "fxmopol_learned_filters.json"


def series_from_trades(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    return pd.Series(trades["return_pct"].values / 100.0, index=pd.to_datetime(trades["exit_time"]))


def baseline_trades(sym: str, ema_mid: int, *, prime_session: bool) -> pd.DataFrame:
    h1 = load_h1(sym)
    return replay_trades(
        h1,
        ema_mid=ema_mid,
        symbol=sym,
        good_sessions_only=prime_session,
        weekday_mask=DEFAULT_WEEKDAY_MASK if prime_session else 0,
    )


def apply_professor_filter(trades: pd.DataFrame, h1: pd.DataFrame, sym: str, ema_mid: int) -> pd.DataFrame:
    if trades.empty:
        return trades
    carry_s, vix_chg_s = __import__("fxmopol_signal_layers", fromlist=["prep_macro_series"]).prep_macro_series(
        h1.index, sym
    )
    spread_pct = prep_liquidity_series(h1)
    vol_elev = prep_vol_elevated(h1)
    kept = []
    for _, row in trades.iterrows():
        bar_i = max(0, int(row.get("entry_bar_i") or 1) - 1)
        side = 1 if row["side"] == "LONG" else -1
        if row.get("entry_grade") not in (None, "", "GOOD"):
            if str(row.get("entry_grade")) != "GOOD":
                continue
        if str(row.get("chart_state")) in CHOP_STATES:
            continue
        g = gate_at_bar(
            h1,
            bar_i,
            sym,
            side,
            ema_mid=ema_mid,
            carry_s=carry_s,
            vix_chg_s=vix_chg_s,
            spread_pctile=spread_pct,
            vol_elevated=vol_elev,
            chart_state=str(row.get("chart_state") or ""),
        )
        if g["block"]:
            continue
        scaled = row.copy()
        scaled["return_pct"] = float(row["return_pct"]) * float(g["risk_mult"])
        scaled["professor_mult"] = g["risk_mult"]
        kept.append(scaled)
    return pd.DataFrame(kept) if kept else pd.DataFrame(columns=trades.columns)


def apply_learned_timing(trades: pd.DataFrame, sym: str) -> pd.DataFrame:
    if not LEARNED.exists() or trades.empty:
        return trades
    man = json.loads(LEARNED.read_text(encoding="utf-8"))
    cfg = man.get("symbols", {}).get(sym.upper(), {})
    mask = int(cfg.get("weekday_mask") or 0)
    hours = cfg.get("allowed_hours_utc") or []
    reasons = cfg.get("allowed_entry_reasons") or []
    if not mask and not hours and not reasons:
        return trades
    et = pd.to_datetime(trades["entry_time"])
    out = trades.copy()
    if mask:
        out = out[et.apply(lambda t: weekday_allows_new_entry(t.dayofweek, mask))]
    if hours:
        out = out[pd.to_datetime(out["entry_time"]).apply(lambda t: t.hour in hours)]
    if reasons:
        out = out[out["entry_reason"].isin(reasons)]
    return out


def run_modes(sym: str, ema_mid: int) -> dict:
    h1 = load_h1(sym)
    modes: dict[str, pd.DataFrame] = {}
    modes["chart_loose"] = replay_trades(h1, ema_mid=ema_mid, symbol=sym, good_sessions_only=False, weekday_mask=0)
    modes["chart_prime"] = baseline_trades(sym, ema_mid, prime_session=True)
    modes["professor_stack"] = apply_professor_filter(modes["chart_prime"], h1, sym, ema_mid)
    modes["professor_governor"] = apply_governor(modes["professor_stack"], sym)
    modes["professor_governor_learned"] = apply_learned_timing(modes["professor_governor"], sym)

    summary = {}
    for name, tr in modes.items():
        r = series_from_trades(tr)
        tag = f"stack_{sym}_mid{ema_mid}_{name}"
        if len(r) > 0:
            publish_results(tag, r)
            print_split_monthly(tag, r, SPLITS)
        row = {"trades": len(tr), "splits": {}}
        for split, (a, b) in SPLITS.items():
            part = r[(r.index >= pd.Timestamp(a)) & (r.index < pd.Timestamp(b))] if len(r) else r
            row["splits"][split] = metrics(part)
        if len(r) > 0:
            row["full"] = {
                "exp_bps": round(float(r.mean() * 10000), 2),
                "total_pct": round(float(((1 + r).prod() - 1) * 100), 2),
                "pf": metrics(r).get("pf"),
            }
        summary[name] = row
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--ema-mid", type=int, default=50)
    args = p.parse_args()
    sym = args.symbol.upper()
    ema = args.ema_mid if sym != "USDJPY" else 100

    print(f"=== FXMOPOL stack P&L {sym} (EMA{ema}) ===", flush=True)
    summary = run_modes(sym, ema)

    manifest = {
        "symbol": sym,
        "ema_mid": ema,
        "modes": summary,
        "note": "professor_stack = prime sessions + professor blocks; not live-certified",
        "certified_alternate": "carry_mom_filter monthly sleeve (D1) — see lanes_summary.json",
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("\n=== Stack comparison (full sample) ===")
    for name, row in summary.items():
        full = row.get("full", {})
        print(f"  {name}: n={row['trades']} total%={full.get('total_pct', 0)} exp_bps={full.get('exp_bps', 0)}")
        oos = row["splits"].get("OOS", {})
        print(f"    OOS: n={oos.get('n')} total%={oos.get('total_pct')} pf={oos.get('pf')}")
    print(f"\nWrote {OUT_MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
