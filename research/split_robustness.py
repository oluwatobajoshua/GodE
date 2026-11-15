#!/usr/bin/env python3
r"""How many splits can 104 months actually support?

"Why only three splits?" has a measurable answer, and it is not a matter of taste.

You need three for an honest test: one set to search on, one to confirm, one that
is never touched until the end. That is the minimum, not the maximum. The reason
the project stops at three is the second constraint: every extra split makes each
block shorter, and a Sharpe ratio estimated from a short block is mostly noise.
This script measures where that happens.

Three independent measurements:

  1. K-FOLD BLOCKS. Chop the same months into K chronological blocks and report
     each block's return and Sharpe. As K rises the blocks shorten and the
     per-block Sharpe estimate widens. This finds the K at which "more splits"
     stops buying evidence and starts buying noise.

  2. SHARPE STANDARD ERROR AND t-STAT. Lo (2002), iid case:
         SE(SR_ann) = sqrt((1 + SR_ann^2 / 24) / T_years)
     A split whose Sharpe is inside 1 SE of zero is not evidence of anything, and
     a book whose whole history is 2 SE from zero is a 2-sigma result, not a
     certainty. This is printed for every sleeve and every split.

  3. STATIONARY BOOTSTRAP. Politis-Romano resampling of the monthly series (mean
     block 6 months) gives a CI on Sharpe / CAGR / maxDD without assuming normal
     returns, and the fraction of resamples with a positive mean. Overlapping
     walk-forward windows are NOT used: they do not add independent observations,
     they only re-use the same ones.

The conclusion this produces: the number of splits is capped by the length of the
tape, not by the number of slices. To get more evidence you need more history, not
more cuts. `research/data/histdata/*_H1.csv` holds 21 years of H1 (2005-2025) for
the majors against the broker's 8.7, which is the real way to buy power.

Run:
    .\\.venv\\Scripts\\python.exe research\\split_robustness.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from portfolio_combine import (  # noqa: E402
    RESULTS,
    SPLITS,
    carry_sleeve,
    inverse_vol_weights,
    read_monthly_csv,
)

BOOT = 5000
MEAN_BLOCK = 6.0
SEED = 7


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def ann_sharpe(r: np.ndarray) -> float:
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(12.0)) if sd > 0 else 0.0


def sharpe_se(sr_ann: float, months: int) -> float:
    """Lo (2002) iid standard error of an annualised Sharpe, in Sharpe units."""
    if months <= 1:
        return float("inf")
    return float(np.sqrt((1.0 + sr_ann ** 2 / 24.0) / (months / 12.0)))


def months_for_tstat(sr_ann: float, t: float = 2.0) -> float:
    """Months needed before SR/SE reaches `t` (iid approximation)."""
    if sr_ann <= 0:
        return float("inf")
    return 12.0 * t ** 2 * (1.0 + sr_ann ** 2 / 24.0) / sr_ann ** 2


def block_stats(r: pd.Series, k: int) -> list[dict]:
    """Chop into k contiguous blocks of near-equal length."""
    n = len(r)
    edges = np.linspace(0, n, k + 1).astype(int)
    out = []
    for i in range(k):
        x = r.iloc[edges[i]:edges[i + 1]]
        if len(x) < 2:
            continue
        sr = ann_sharpe(x.to_numpy(float))
        eq = (1.0 + x).cumprod()
        out.append(dict(block=i + 1, months=len(x),
                        total=float(eq.iloc[-1] - 1.0) * 100.0,
                        sharpe=sr, se=sharpe_se(sr, len(x))))
    return out


def stationary_bootstrap(r: np.ndarray, n_boot: int = BOOT,
                         mean_block: float = MEAN_BLOCK,
                         seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(r)
    p = 1.0 / mean_block
    sr = np.empty(n_boot)
    tot = np.empty(n_boot)
    dd = np.empty(n_boot)
    mu = np.empty(n_boot)
    for b in range(n_boot):
        idx = np.empty(n, dtype=np.int64)
        i = int(rng.integers(0, n))
        for t in range(n):
            idx[t] = i
            i = int(rng.integers(0, n)) if rng.random() < p else (i + 1) % n
        x = r[idx]
        sr[b] = ann_sharpe(x)
        eq = np.cumprod(1.0 + x)
        tot[b] = (eq[-1] - 1.0) * 100.0
        dd[b] = (eq / np.maximum.accumulate(eq) - 1.0).min() * 100.0
        mu[b] = x.mean()
    return pd.DataFrame(dict(sharpe=sr, total=tot, dd=dd, mean=mu))


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def split_table(name: str, r: pd.Series) -> dict:
    print(f"\n--- {name}  ({len(r)} months, {r.index.min()} -> {r.index.max()}) ---")
    print(f"  {'split':<11}{'months':>7}{'total':>9}{'Sharpe':>8}{'SE':>7}{'t':>7}"
          f"{'maxDD':>9}")
    out = {}
    for sp in ("design", "validation", "OOS"):
        a, b = SPLITS[sp]
        lo, hi = pd.Timestamp(a).to_period("M"), pd.Timestamp(b).to_period("M")
        x = r.loc[(r.index >= lo) & (r.index < hi)]
        if len(x) < 2:
            continue
        sr = ann_sharpe(x.to_numpy(float))
        se = sharpe_se(sr, len(x))
        eq = (1.0 + x).cumprod()
        dd = float((eq / eq.cummax() - 1.0).min()) * 100.0
        t = sr / se if se > 0 else 0.0
        out[sp] = dict(months=len(x), total=float(eq.iloc[-1] - 1.0) * 100.0,
                       sharpe=sr, se=se, t=t, dd=dd)
        print(f"  {sp:<11}{len(x):>7}{out[sp]['total']:>+9.2f}{sr:>8.2f}{se:>7.2f}"
              f"{t:>7.2f}{dd:>9.2f}")
    sr = ann_sharpe(r.to_numpy(float))
    se = sharpe_se(sr, len(r))
    eq = (1.0 + r).cumprod()
    dd = float((eq / eq.cummax() - 1.0).min()) * 100.0
    out["full"] = dict(months=len(r), total=float(eq.iloc[-1] - 1.0) * 100.0,
                       sharpe=sr, se=se, t=sr / se if se > 0 else 0.0, dd=dd)
    print(f"  {'full':<11}{len(r):>7}{out['full']['total']:>+9.2f}{sr:>8.2f}{se:>7.2f}"
          f"{out['full']['t']:>7.2f}{dd:>9.2f}")
    need = months_for_tstat(sr)
    print(f"  -> a Sharpe of {sr:.2f} needs {need:.0f} months for a t of 2.0; "
          f"this tape has {len(r)}")
    return out


def kfold_table(r: pd.Series) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("1. K-FOLD: THE SAME 104 MONTHS CUT INTO K CHRONOLOGICAL BLOCKS")
    print("=" * 78)
    print(f"  {'K':>3}{'months/blk':>11}{'positive':>10}{'median SR':>11}"
          f"{'median SE':>11}{'SR/SE':>8}")
    rows = []
    for k in (2, 3, 4, 5, 6, 8, 10, 12):
        blocks = block_stats(r, k)
        if len(blocks) < 2:
            continue
        pos = sum(1 for b in blocks if b["total"] > 0)
        msr = float(np.median([b["sharpe"] for b in blocks]))
        mse = float(np.median([b["se"] for b in blocks]))
        m_months = int(np.median([b["months"] for b in blocks]))
        rows.append(dict(k=k, months=m_months, positive=pos, n=len(blocks),
                         median_sharpe=msr, median_se=mse,
                         signal_to_noise=msr / mse if mse > 0 else 0.0))
        print(f"  {k:>3}{m_months:>11}{f'{pos}/{len(blocks)}':>10}{msr:>11.2f}"
              f"{mse:>11.2f}{rows[-1]['signal_to_noise']:>8.2f}")
    df = pd.DataFrame(rows)
    print("\n  Read it as: once SR/SE falls below ~1, a block's Sharpe cannot be")
    print("  told apart from zero, so adding more blocks adds labels, not evidence.")
    return df


def bootstrap_table(name: str, r: pd.Series) -> None:
    x = r.to_numpy(float)
    b = stationary_bootstrap(x)
    lo, hi = np.percentile(b.sharpe, [2.5, 97.5])
    tlo, thi = np.percentile(b.total, [2.5, 97.5])
    dlo, dhi = np.percentile(b.dd, [2.5, 97.5])
    print(f"\n  {name}  ({len(x)} months, stationary bootstrap, "
          f"{BOOT} resamples, mean block {MEAN_BLOCK:.0f}m)")
    print(f"    Sharpe  point {ann_sharpe(x):>5.2f}   95% CI [{lo:>5.2f}, {hi:>5.2f}]"
          f"   P(Sharpe>0) {100 * (b.sharpe > 0).mean():>5.1f}%")
    print(f"    total   point {(np.cumprod(1 + x)[-1] - 1) * 100:>+6.2f}%  "
          f"95% CI [{tlo:>+6.2f}%, {thi:>+6.2f}%]   "
          f"P(total>0) {100 * (b.total > 0).mean():>5.1f}%")
    print(f"    maxDD   point "
          f"{((np.cumprod(1 + x) / np.maximum.accumulate(np.cumprod(1 + x))) - 1).min() * 100:>+6.2f}%"
          f"  95% CI [{dlo:>+6.2f}%, {dhi:>+6.2f}%]")


def main() -> int:
    # the book, rebuilt exactly as portfolio_combine.py builds it
    sleeves = {
        "carry_mom_filter": carry_sleeve(),
        "rsi_band_EURUSD_H1": read_monthly_csv(
            "rsi_band_ens_EURUSD_H1_monthly.csv", "rsi_band_ens_EURUSD_H1"),
        "rsi_band_GBPUSD_H1": read_monthly_csv(
            "rsi_band_ens_GBPUSD_H1_monthly.csv", "rsi_band_ens_GBPUSD_H1"),
    }
    frame = pd.DataFrame(sleeves).sort_index()
    cols = list(sleeves)
    sub = frame[cols].dropna()
    w = inverse_vol_weights(sub, cols)
    book = (sub[cols] * w[cols]).sum(axis=1)

    print("=" * 78)
    print("0. THE CURRENT THREE-SPLIT VIEW, WITH ERROR BARS")
    print("=" * 78)
    print("  weights: " + "  ".join(f"{c}={v * 100:.1f}%" for c, v in w.items()))
    for name, series in [("main book", book)] + list(sleeves.items()):
        split_table(name, series)

    kfold_table(book)

    print("\n" + "=" * 78)
    print("2. STATIONARY BOOTSTRAP (no normality assumption)")
    print("=" * 78)
    bootstrap_table("main book", book)

    print("\n" + "=" * 78)
    print("3. VERDICT")
    print("=" * 78)
    full = ann_sharpe(book.to_numpy(float))
    se = sharpe_se(full, len(book))
    print(f"  The whole 104-month book is Sharpe {full:.2f} +/- {se:.2f} "
          f"(t = {full / se:.2f}).")
    print(f"  Three splits already spend the tape: {len(book)} months / 3 = "
          f"~{len(book) // 3} months each, which is the minimum at which a")
    print("  block Sharpe means anything. More slices of the same 104 months buy")
    print("  labels, not evidence - the bootstrap CI above is the honest width.")
    print("  The way to buy real power is a longer tape: "
          "research/data/histdata/*_H1.csv")
    print("  holds 21 years of H1 (2005-2025) against the broker's 8.7.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
