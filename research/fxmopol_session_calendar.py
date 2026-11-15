#!/usr/bin/env python3
"""FXMOPOL calendar research — sessions and weekdays on playbook trades.

Scores entry-time buckets on DESIGN only; reports VALIDATION + OOS without fitting.
Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_session_calendar.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fxmopol_chart_core import PRIME_SESSION_WINDOWS, session_allows_new_entry
from fxmopol_playbook import SPLITS, load_h1, replay_trades

HERE = Path(__file__).resolve().parent
OUT_JSON = HERE / "results" / "fxmopol_session_calendar.json"
OUT_CSV = HERE / "results" / "fxmopol_session_calendar.csv"
SYMS = ("GBPUSD", "EURUSD", "USDJPY")
DOW = ("Mon", "Tue", "Wed", "Thu", "Fri")


def pf(s: pd.Series) -> float:
    w, l = s[s > 0], s[s < 0]
    if len(l) == 0 or l.sum() == 0:
        return 0.0
    return float(w.sum() / abs(l.sum()))


def bucket_metrics(df: pd.DataFrame, col: str) -> pd.DataFrame:
  rows = []
  for key, g in df.groupby(col):
    r = g["return_pct"] / 100.0
    rows.append(
      {
        "bucket": key,
        "n": len(g),
        "exp_bps": round(r.mean() * 10000, 2) if len(r) else 0,
        "pf": round(pf(r), 2),
        "total_pct": round(((1 + r).prod() - 1) * 100, 2) if len(r) else 0,
      }
    )
  return pd.DataFrame(rows).sort_values("bucket")


def pick_weekdays(design: pd.DataFrame, min_trades: int = 12) -> list[int]:
    """Design-only: weekdays with positive mean return and n >= min_trades."""
    d = design.copy()
    d["dow"] = pd.to_datetime(d["entry_time"]).dt.dayofweek
    good = []
    for dow in range(5):
      g = d[d["dow"] == dow]
      if len(g) < min_trades:
        continue
      if g["return_pct"].mean() > 0 and pf(g["return_pct"] / 100.0) >= 1.0:
        good.append(dow)
    return good


def main() -> int:
  design_a, design_b = SPLITS["design"]
  all_rows = []
  manifest: dict = {
    "prime_session_windows_utc": list(PRIME_SESSION_WINDOWS),
    "note": "HistData H1 times are GMT. MT5 uses broker server time.",
    "symbols": {},
  }

  for sym in SYMS:
    mid = 100 if sym == "USDJPY" else 50
    trades = replay_trades(load_h1(sym), ema_mid=mid, symbol=sym)
    if trades.empty:
      manifest["symbols"][sym] = {"error": "no_trades"}
      continue
    trades = trades.copy()
    et = pd.to_datetime(trades["entry_time"])
    trades["dow"] = et.dt.dayofweek
    trades["hour"] = et.dt.hour
    trades["dow_name"] = trades["dow"].map(dict(enumerate(DOW)))
    trades["in_prime"] = trades["hour"].map(lambda h: session_allows_new_entry(int(h)))
    trades["split"] = "all"
    for split, (a, b) in SPLITS.items():
      m = (et >= a) & (et < b)
      trades.loc[m, "split"] = split

    sym_manifest: dict = {"splits": {}}
    design = trades[trades["split"] == "design"]
    picked = pick_weekdays(design)
    sym_manifest["design_pick_weekdays"] = [DOW[i] for i in picked]
    sym_manifest["design_pick_dow"] = picked

    for split in ("design", "validation", "OOS"):
      part = trades[trades["split"] == split]
      sym_manifest["splits"][split] = {
        "dow": bucket_metrics(part, "dow_name").to_dict(orient="records"),
        "hour": bucket_metrics(part, "hour").to_dict(orient="records"),
        "prime_vs_off": bucket_metrics(part, "in_prime").to_dict(orient="records"),
      }
      if split == "design":
        filt = part[part["dow"].isin(picked)] if picked else part.iloc[0:0]
      else:
        filt = part[part["dow"].isin(picked)] if picked else part.iloc[0:0]
      r = filt["return_pct"] / 100.0
      sym_manifest["splits"][split]["filtered_design_weekdays"] = {
        "weekdays": [DOW[i] for i in picked],
        "n": len(filt),
        "exp_bps": round(r.mean() * 10000, 2) if len(r) else 0,
        "pf": round(pf(r), 2),
        "total_pct": round(((1 + r).prod() - 1) * 100, 2) if len(r) else 0,
      }

    manifest["symbols"][sym] = sym_manifest
    for _, row in trades.iterrows():
      all_rows.append({"symbol": sym, **row.to_dict()})

  pd.DataFrame(all_rows).to_csv(OUT_CSV, index=False)
  OUT_JSON.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

  print("=== FXMOPOL session/calendar (design weekday pick -> val/OOS) ===")
  for sym in SYMS:
    s = manifest["symbols"].get(sym, {})
    print(sym, "design pick:", s.get("design_pick_weekdays"))
    for split in ("validation", "OOS"):
      f = s.get("splits", {}).get(split, {}).get("filtered_design_weekdays", {})
      print(f"  {split} filtered:", f)
  print(f"\nWrote {OUT_JSON.name}, {OUT_CSV.name}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
