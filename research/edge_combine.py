#!/usr/bin/env python3
"""Combine the two edges that survived, and see whether two small edges make one usable one.

Everything measured so far says the same thing: the market hands out many weak
persistence signals and no strong directional one. The consequence is that the only
lever left is not a better model - it is arithmetic. Two edges that are individually
weak and roughly uncorrelated add up in Sharpe even though neither improves.

The two survivors:
  carry_mom_filter     D1, multi-pair, Sharpe ~0.20, maxDD -26%   (research/carry.py)
  rsi_band_net         EURUSD H1, net of real cost, Sharpe ~0.54  (research/rsi_band_net.py)

They trade different frequencies, different instruments and different premises
(a slow interest-rate premium vs a fast RSI band ride), so the prior is that their
monthly returns are close to uncorrelated. If so, a blend can beat both on Sharpe
and cut the drawdown that makes the carry book undeployable.

Discipline
  * the blend weight is chosen on the DESIGN and VALIDATION months only
  * out-of-sample is reported afterwards and never used to pick the weight
  * each leg is also reported alone over the same months, so the blend cannot
    take credit for a good period that one leg owned

Usage
    .\\.venv\\Scripts\\python.exe research/edge_combine.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "research" / "results"


def load(name: str, col: str = "return_pct") -> pd.Series | None:
    p = RESULTS / name
    if not p.exists():
        return None
    d = pd.read_csv(p)
    d["date"] = pd.to_datetime(d["date"])
    s = d.set_index("date")[col].astype(float) / 100.0
    s = s.groupby(level=0).last().sort_index()
    #--- carry publishes month-start stamps and the RSI leg publishes month-end
    #--- stamps, so align on the calendar month itself
    s.index = s.index.to_period("M")
    return s.groupby(level=0).last().sort_index()


def metrics(r: np.ndarray) -> dict:
    eq = np.cumprod(1.0 + r)
    n = len(r)
    cagr = eq[-1] ** (12.0 / n) - 1.0 if n > 0 and eq[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq)
    sd = r.std()
    return dict(cagr=cagr, sharpe=(r.mean() / sd * np.sqrt(12)) if sd > 0 else 0.0,
                dd=float((eq / peak - 1.0).min()), total=float(eq[-1] - 1.0),
                win=float((r > 0).mean()), n=n)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--carry", default="carry_mom_filter_monthly.csv")
    ap.add_argument("--fast", default="rsi_band_net_EURUSD_H1_monthly.csv")
    ap.add_argument("--step", type=float, default=0.05)
    args = ap.parse_args()

    carry = load(args.carry)
    fast = load(args.fast)
    if carry is None or fast is None:
        raise SystemExit("missing monthly series - run the two research scripts first")

    idx = carry.index.intersection(fast.index)
    a = carry.reindex(idx).to_numpy()
    b = fast.reindex(idx).to_numpy()
    n = len(idx)
    if n < 24:
        raise SystemExit(f"only {n} overlapping months - not enough to combine")

    print("=== combining the two surviving edges ===")
    print(f"carry_mom_filter      {carry.index[0]} -> {carry.index[-1]}  "
          f"({len(carry)} months)")
    print(f"rsi_band_net EURUSD   {fast.index[0]} -> {fast.index[-1]}  "
          f"({len(fast)} months)")
    print(f"overlap               {idx[0]} -> {idx[-1]}  ({n} months)")
    print("")

    corr = float(np.corrcoef(a, b)[0, 1])
    print(f"monthly correlation of the two edges: {corr:+.3f}")
    print(f"  (0 = independent, +1 = same trade twice, so a blend is only")
    print(f"   diversification if this is near 0 or negative)")
    print("")

    print("--- each leg alone, over the overlap only (the honest comparison) ---")
    print(f"{'leg':<26}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>9}{'total':>9}{'win':>7}")
    print("-" * 67)
    for name, r in (("carry_mom_filter (D1)", a), ("rsi_band_net (EURUSD H1)", b)):
        m = metrics(r)
        print(f"{name:<26}{m['cagr'] * 100:>7.2f}%{m['sharpe']:>8.2f}"
              f"{m['dd'] * 100:>8.1f}%{m['total'] * 100:>8.1f}%{m['win'] * 100:>6.1f}%")
    print("")

    # ---- choose the weight on design + validation months only
    cut1, cut2 = int(n * 0.60), int(n * 0.80)
    dv = slice(0, cut2)
    oos = slice(cut2, n)

    weights = np.arange(0.0, 1.0001, args.step)
    rows = []
    for w in weights:
        r = w * a + (1.0 - w) * b
        mdv = metrics(r[dv])
        rows.append(dict(w=w, sharpe_dv=mdv["sharpe"], dd_dv=mdv["dd"],
                         cagr_dv=mdv["cagr"]))
    tbl = pd.DataFrame(rows)
    best = tbl.sort_values("sharpe_dv", ascending=False).iloc[0]
    w = float(best.w)

    print(f"--- weight sweep: w = share in carry, chosen on design+validation only ---")
    print(f"{'w carry':>8}{'w rsi':>8}{'Sharpe (D+V)':>15}{'maxDD (D+V)':>14}{'CAGR (D+V)':>13}")
    print("-" * 58)
    show = tbl[(tbl.w * 100).round().astype(int) % 20 == 0]
    for _, r in show.iterrows():
        mark = "  <- chosen" if abs(r.w - w) < 1e-9 else ""
        print(f"{r.w:>8.2f}{1 - r.w:>8.2f}{r.sharpe_dv:>15.2f}"
              f"{r.dd_dv * 100:>13.1f}%{r.cagr_dv * 100:>12.2f}%{mark}")
    print("")

    blend = w * a + (1.0 - w) * b
    print(f"--- chosen blend: {w:.0%} carry / {1 - w:.0%} RSI band ---")
    print(f"{'window':<18}{'CAGR':>8}{'Sharpe':>8}{'maxDD':>9}{'total':>9}{'win':>7}")
    print("-" * 59)
    for label, sl in (("design (60%)", slice(0, cut1)),
                      ("validation (20%)", slice(cut1, cut2)),
                      ("out-of-sample (20%)", oos),
                      ("full overlap", slice(0, n))):
        m = metrics(blend[sl])
        print(f"{label:<18}{m['cagr'] * 100:>7.2f}%{m['sharpe']:>8.2f}"
              f"{m['dd'] * 100:>8.1f}%{m['total'] * 100:>8.1f}%{m['win'] * 100:>6.1f}%")
    print("")

    m_legs = metrics(a[oos]), metrics(b[oos])
    m_blend = metrics(blend[oos])
    print("--- out-of-sample verdict ---")
    print(f"  carry alone      Sharpe {m_legs[0]['sharpe']:>5.2f}  maxDD {m_legs[0]['dd'] * 100:>6.1f}%")
    print(f"  RSI band alone   Sharpe {m_legs[1]['sharpe']:>5.2f}  maxDD {m_legs[1]['dd'] * 100:>6.1f}%")
    print(f"  blend            Sharpe {m_blend['sharpe']:>5.2f}  maxDD {m_blend['dd'] * 100:>6.1f}%")
    better = m_blend["sharpe"] > max(m_legs[0]["sharpe"], m_legs[1]["sharpe"])
    print(f"  -> the blend {'BEATS' if better else 'does not beat'} the better leg on "
          f"out-of-sample Sharpe")
    print("")

    out = pd.DataFrame({"date": idx.to_timestamp(how="end"), "carry": a,
                        "rsi_band": b, "blend": blend})
    out.to_csv(RESULTS / "edge_combine_monthly.csv", index=False)
    print(f"wrote {RESULTS / 'edge_combine_monthly.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
