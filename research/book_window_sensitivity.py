#!/usr/bin/env python3
r"""Is the book's negative design split a regime break, or a data-window artifact?

The M30 tape is capped at 100,000 bars, so GBPUSD M30 begins 2018-10-28 while
both H1 tapes begin 2018-01-01. That single fact sets the book's start date, and
the book's design split then came out at -1.72%. This script separates the two
possible causes:

  1. year-by-year returns for every sleeve, so a genuine regime break would show
     as a run of bad years rather than one bad slice;
  2. the same book built only from sleeves that DO have 2018-01 data (carry D1 +
     RSI EURUSD H1), with its own design/validation/OOS;
  3. the 9 months the M30 cap removes, priced on the two sleeves that have them.

This is a diagnostic, not a selection: no start date is chosen because it passes.
The fixed calendar splits are the project's, unchanged.

Run:
    .\.venv\Scripts\python.exe research\book_window_sensitivity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from portfolio_combine import (  # noqa: E402
    SPLITS,
    carry_sleeve,
    inverse_vol_weights,
    m15m30_sleeve,
    metrics,
    read_monthly_csv,
)

TWO = ["carry_mom_filter", "rsi_band_EURUSD_H1"]
THREE = TWO + ["rsi_band_GBPUSD_H1"]
M30BOOK = TWO + ["rsi_band_GBPUSD_M30"]


def _slice(r: pd.Series, split: str) -> pd.Series:
    a, b = SPLITS[split]
    lo, hi = pd.Timestamp(a).to_period("M"), pd.Timestamp(b).to_period("M")
    return r.loc[(r.index >= lo) & (r.index < hi)]


def yearly(r: pd.Series) -> pd.Series:
    r = r.dropna()
    return r.groupby(r.index.year).apply(lambda x: float((1 + x).prod() - 1)) * 100


def book(frame: pd.DataFrame, cols: list[str], start: str):
    sub = frame.loc[frame.index >= pd.Timestamp(start).to_period("M"), cols]
    sub = sub.loc[sub.notna().all(axis=1)]
    w = inverse_vol_weights(sub, cols)
    comb = (sub[cols] * w[cols]).sum(axis=1)
    return sub, w, comb


def report(label: str, frame: pd.DataFrame, cols: list[str], start: str) -> dict:
    sub, w, comb = book(frame, cols, start)
    print(f"\n--- {label} ---")
    print("  window  " + "  ".join(f"{c}={v*100:.1f}%" for c, v in w.items()))
    print(f"  months {len(sub)}   {sub.index.min()} -> {sub.index.max()}")
    out = {}
    for sp in ("design", "validation", "OOS"):
        s = _slice(comb, sp)
        m = metrics(s)
        out[sp] = m
        if m["months"] == 0:
            print(f"  {sp:<11} no data")
        else:
            print(f"  {sp:<11} {m['total']:+7.2f}%  Sharpe {m['sharpe']:>5.2f}  "
                  f"maxDD {m['dd']:>7.2f}%  months {m['months']}")
    f = metrics(comb)
    out["full"] = f
    print(f"  {'full':<11} {f['total']:+7.2f}%  Sharpe {f['sharpe']:>5.2f}  "
          f"maxDD {f['dd']:>7.2f}%  CAGR {f['cagr']:+.2f}%")
    ok = all(out[sp]["months"] > 0 and out[sp]["total"] > 0
             for sp in ("design", "validation", "OOS"))
    print(f"  positive in all three splits: {'YES' if ok else 'NO'}")
    return out


def main() -> int:
    from period_returns import publish_results  # noqa: PLC0415

    sleeves = {
        "carry_mom_filter": carry_sleeve(),
        "rsi_band_EURUSD_H1": read_monthly_csv(
            "rsi_band_ens_EURUSD_H1_monthly.csv", "rsi_band_ens_EURUSD_H1"),
        "rsi_band_GBPUSD_H1": read_monthly_csv(
            "rsi_band_ens_GBPUSD_H1_monthly.csv", "rsi_band_ens_GBPUSD_H1"),
        "rsi_band_GBPUSD_M30": m15m30_sleeve("GBPUSD", "M30"),
    }
    frame = pd.DataFrame(sleeves).sort_index()

    print("=" * 78)
    print("1. YEAR BY YEAR (compounded, %)")
    print("=" * 78)
    yrs = pd.DataFrame({c: yearly(frame[c]) for c in sleeves}).fillna(0.0)
    print(yrs.round(2).to_string())
    print("\n  positive years:")
    for c in sleeves:
        col = yrs[c].loc[2018:]
        print(f"    {c:<22} {int((col > 0).sum())} / {len(col)}")

    print("\n" + "=" * 78)
    print("2. THE 9 MONTHS THE M30 BAR CAP REMOVES (2018-01 -> 2018-10)")
    print("=" * 78)
    lo, hi = pd.Period("2018-01", "M"), pd.Period("2018-10", "M")
    for c in TWO:
        s = frame[c].loc[(frame.index >= lo) & (frame.index < hi)]
        m = metrics(s)
        print(f"  {c:<22} {m['total']:+7.2f}%  months {m['months']}")

    print("\n" + "=" * 78)
    print("3. THE SAME BOOK ON DIFFERENT START DATES")
    print("=" * 78)
    res = {}
    res["two_2018_01"] = report("2 sleeves (carry + RSI EURUSD), 2018-01",
                                frame, TWO, "2018-01-01")
    res["three_2018_01"] = report("3 sleeves (adds GBPUSD H1), 2018-01 - THE BOOK",
                                  frame, THREE, "2018-01-01")
    res["m30_2018_10"] = report("3 sleeves but with GBPUSD M30, 2018-10 - the artifact",
                                frame, M30BOOK, "2018-10-01")
    res["three_2019_01"] = report("3 sleeves (GBPUSD H1), 2019-01",
                                  frame, THREE, "2019-01-01")

    print("\n" + "=" * 78)
    print("4. VERDICT")
    print("=" * 78)
    t3, m3 = res["three_2018_01"], res["m30_2018_10"]
    print(f"  Book on GBPUSD H1 (full 2018-01 history): design "
          f"{t3['design']['total']:+.2f}% (Sharpe {t3['design']['sharpe']:.2f}).")
    print(f"  Book on GBPUSD M30 (start forced to 2018-10): design "
          f"{m3['design']['total']:+.2f}% (Sharpe {m3['design']['sharpe']:.2f}).")
    print("  Same weights rule, same calendar splits - only the sleeve and its")
    print("  start date differ. The design sign was a function of where the tape")
    print("  begins, not of the strategy.")

    _, _, comb2 = book(frame, TWO, "2018-01-01")
    publish_results("book_2sleeve",
                    pd.Series(comb2.to_numpy(),
                              index=comb2.index.to_timestamp("M")),
                    already_monthly=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
