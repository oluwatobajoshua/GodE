#!/usr/bin/env python3
r"""The combined book: every sleeve that survived its own proof, on one equity curve.

Sleeves, each certified separately before this script runs:

  carry_mom_filter      D1 monthly carry + 12m momentum   KEEP
  rsi_band_ens_GBPUSD   RSI band family, GBPUSD H1          KEEP (ensemble)
  rsi_band_ens_EURUSD   RSI band family, EURUSD H1          CONDITIONAL (broker tape only; fails HistData independent 2005-2025)

The RSI sleeves are the **equal-weight ensemble of all 100 parameter configs**,
not the single best-in-sample config. That matters: on the extended tape the
design+validation winner for EURUSD has an OOS of -1.35% while the family average
is +0.75%, because ranking 100 configs on the worst of design+validation selects
the extreme of that surface, and the extreme does not generalise. Averaging
removes the knob-selection step entirely and is the honest version of the rule.

The GBPUSD M30 pullback is NOT in the main book. The broker's M30 tape stops at
2018-10-28 while both H1 tapes start 2018-01, so using M30 forces the whole book
to start 9 months late - which is what made an earlier version of this script
report a negative design split. GBPUSD H1 has the same edge with pair-specific
parameters and a full 2018-01 history, so the book uses it instead.

IMPORTANT: The EURUSD sleeve passes all 3 splits on the broker tape (2018-2026)
but FAILS all 3 splits on independent HistData (2005-2025): design -28.95%,
validation +6.74%, OOS -5.11%. The broker-tape pass is a data-window artifact.
The main book therefore uses carry + GBPUSD only. EURUSD is kept as a
CONDITIONAL sleeve reported separately, not folded into the main book.

Rules this script obeys:

* Weights are inverse-volatility measured on the **design+validation** window
  only. OOS is never used to pick a weight.
* The M15 sleeve only has ~3 years of history. Including it would shrink the
  whole book's proof window, so it is reported as a separate line on its own
  overlap rather than folded into the main book.
* Returns are monthly arithmetic sums of per-bar returns. Summed vs compounded
  was checked at < 0.4pp over 8 years (see research/backtest_core.py).

Run:
    .\.venv\Scripts\python.exe research/portfolio_combine.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
OUT_MONTHLY = RESULTS / "portfolio_combined_monthly.csv"
OUT_MANIFEST = RESULTS / "portfolio_combined_manifest.json"

# chronological splits used across the whole project
SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-01-01"),
    "OOS": ("2024-01-01", "2031-01-01"),
}
DV_END = pd.Timestamp("2024-01-01")
WEIGHT_CAP = 0.50

M15M30_SWEEP = RESULTS / "rsi_band_m15m30_sweep.csv"
BARS_PER_YEAR = {"M15": 24 * 4 * 252, "M30": 24 * 2 * 252}


# --------------------------------------------------------------------------- #
# sleeve construction
# --------------------------------------------------------------------------- #
def monthly_from_bars(ret, times) -> pd.Series:
    """Sum per-bar returns into calendar months, indexed by period."""
    s = pd.Series(np.asarray(ret, dtype=float), index=pd.DatetimeIndex(times))
    g = s.groupby(s.index.to_period("M")).sum()
    g.index.name = "period"
    return g


def to_period_series(s: pd.Series) -> pd.Series:
    """Normalise any datetime-indexed monthly series to a PeriodIndex."""
    s = s.copy()
    if not isinstance(s.index, pd.PeriodIndex):
        s.index = pd.DatetimeIndex(s.index).to_period("M")
    s.index.name = "period"
    return s.sort_index()


def read_monthly_csv(name: str, strategy: str) -> pd.Series:
    path = RESULTS / name
    df = pd.read_csv(path, parse_dates=["date"])
    if "strategy" in df.columns:
        df = df[df["strategy"] == strategy]
    return to_period_series(pd.Series(df["return_pct"].to_numpy(float) / 100.0,
                                      index=df["date"]))


def m15m30_sleeve(sym: str, tf: str) -> pd.Series:
    """Rebuild the exact config that cleared all three splits, from its own sweep."""
    from rsi_band_m15m30_sweep import (  # noqa: PLC0415
        TEMPLATE, ema, load_csv, run_config, wilder_atr, wilder_rsi,
    )

    tbl = pd.read_csv(M15M30_SWEEP)
    sub = tbl[(tbl["sym"] == sym) & (tbl["tf"] == tf)
              & (tbl["all3_pos"].astype(str) == "True")].copy()
    if sub.empty:
        raise RuntimeError(f"no all-3-split config for {sym} {tf}")
    sub["worst_dv"] = sub[["net_design", "net_validation"]].min(axis=1)
    row = sub.sort_values("worst_dv", ascending=False).iloc[0]

    df = load_csv(sym, tf)
    close = df["close"].to_numpy(float)
    atr = wilder_atr(df, 14).to_numpy(float)
    rsi = wilder_rsi(df["close"], TEMPLATE["rsi_period"]).to_numpy(float)
    spread = df["spread"].astype(float).to_numpy()
    time = df["time"]

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_side = (np.maximum(spread, 1.0) / 2.0 + 1.0 + 7.0 / 2.0) * point
    q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
    spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())

    e21 = ema(df["close"], TEMPLATE["ema_fast"]).to_numpy(float)
    e50 = ema(df["close"], 50).to_numpy(float)
    n = len(df)
    slope3 = np.zeros(n)
    slope3[3:] = (e21[3:] - e21[:-3]) / np.maximum(atr[3:], 1e-12)
    trend = np.where(slope3 > 0.10, 1, np.where(slope3 < -0.10, -1, 0))
    aligned = trend.copy()
    aligned[(trend > 0) & (e21 <= e50)] = 0
    aligned[(trend < 0) & (e21 >= e50)] = 0

    _st, _rt, ret = run_config(
        df, close, atr, rsi, aligned if bool(row["aligned"]) else trend,
        spread_ok, cost_side, row["entry"], TEMPLATE["rsi_hi"],
        TEMPLATE["rsi_lo"], TEMPLATE["rsi_mid"], int(row["min_hold"]),
        int(row["cooldown"]), BARS_PER_YEAR[tf], time)
    return monthly_from_bars(ret, time)


def carry_sleeve() -> pd.Series:
    from carry import CURRENCIES  # noqa: PLC0415
    from export_period_results import (  # noqa: PLC0415
        carry_mom_filter_series, load_monthly_prices, load_rates,
    )

    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
    return to_period_series(carry_mom_filter_series(spot, diff, price))


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def metrics(r: pd.Series) -> dict:
    r = r.dropna()
    n = len(r)
    if n == 0:
        return dict(months=0, total=0.0, cagr=0.0, sharpe=0.0, dd=0.0)
    eq = (1 + r).cumprod()
    yrs = n / 12.0
    total = float(eq.iloc[-1] - 1.0)
    cagr = float(eq.iloc[-1] ** (1.0 / yrs) - 1.0) if eq.iloc[-1] > 0 else -1.0
    peak = np.maximum.accumulate(eq.to_numpy())
    dd = float((eq.to_numpy() / peak - 1.0).min())
    sd = float(r.std())
    sharpe = float(r.mean() / sd * np.sqrt(12)) if sd > 0 else 0.0
    return dict(months=n, total=total * 100, cagr=cagr * 100,
                sharpe=sharpe, dd=dd * 100)


def inverse_vol_weights(frame: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Inverse-vol weights on the design+validation window, capped, sum to 1."""
    dv = frame.loc[frame.index < DV_END.to_period("M"), cols]
    vol = dv.std().replace(0.0, np.nan).dropna()
    if vol.empty:
        return pd.Series(1.0 / len(cols), index=cols)
    w = (1.0 / vol)
    w = w / w.sum()
    w = w.clip(upper=WEIGHT_CAP)
    return w / w.sum()


def split_report(frame: pd.DataFrame, cols: list[str], weights: pd.Series,
                 label: str) -> tuple[dict, pd.Series]:
    combined = (frame[cols] * weights[cols]).sum(axis=1)
    out = {"label": label, "months": int(len(frame)),
           "weights": {c: round(float(weights[c]), 4) for c in cols},
           "sleeves": {}, "combined": {}}
    for c in cols:
        out["sleeves"][c] = {sp: round(metrics(_slice(frame[c], sp))["total"], 2)
                             for sp in SPLITS}
    for sp in SPLITS:
        out["combined"][sp] = {k: round(v, 4) if isinstance(v, float) else v
                               for k, v in metrics(_slice(combined, sp)).items()}
    out["combined"]["full"] = {k: round(v, 4) if isinstance(v, float) else v
                               for k, v in metrics(combined).items()}
    return out, combined


def _slice(r: pd.Series, split: str) -> pd.Series:
    a, b = SPLITS[split]
    lo, hi = pd.Timestamp(a).to_period("M"), pd.Timestamp(b).to_period("M")
    return r.loc[(r.index >= lo) & (r.index < hi)]


def print_book(rep: dict) -> None:
    print(f"\n=== {rep['label']}  ({rep['months']} months) ===")
    print("  weights (inverse-vol, design+validation only):")
    for k, v in rep["weights"].items():
        print(f"    {k:<22} {v*100:5.1f}%")
    print(f"\n  {'sleeve':<24} {'design':>9} {'validation':>11} {'OOS':>9}")
    for k, v in rep["sleeves"].items():
        print(f"  {k:<24} {v['design']:>8.2f}% {v['validation']:>10.2f}% {v['OOS']:>8.2f}%")
    c = rep["combined"]
    def _fmt(sp: str) -> str:
        v = c[sp]
        return "      n/a" if v["months"] == 0 else f"{v['total']:>8.2f}%"
    print(f"  {'COMBINED':<24} {_fmt('design')} {_fmt('validation')} {_fmt('OOS')}")
    f = c["full"]
    print(f"\n  full: CAGR {f['cagr']:+.2f}%  Sharpe {f['sharpe']:.2f}  "
          f"maxDD {f['dd']:.2f}%  total {f['total']:+.2f}%")
    for sp in ("design", "validation", "OOS"):
        s = c[sp]
        if s["months"] == 0:
            print(f"  {sp:<11} no data in this window")
            continue
        print(f"  {sp:<11} Sharpe {s['sharpe']:>5.2f}  maxDD {s['dd']:>7.2f}%  "
              f"months {s['months']}")
    ok = all(c[sp]["months"] > 0 and c[sp]["total"] > 0
             for sp in ("design", "validation", "OOS"))
    rep["passes_all_splits"] = ok
    print(f"  positive in all three splits: {'YES' if ok else 'NO'}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    from period_returns import publish_results  # noqa: PLC0415

    sleeves: dict[str, pd.Series] = {}
    sleeves["carry_mom_filter"] = carry_sleeve()
    sleeves["rsi_band_EURUSD_H1"] = read_monthly_csv(
        "rsi_band_ens_EURUSD_H1_monthly.csv", "rsi_band_ens_EURUSD_H1")
    sleeves["rsi_band_GBPUSD_H1"] = read_monthly_csv(
        "rsi_band_ens_GBPUSD_H1_monthly.csv", "rsi_band_ens_GBPUSD_H1")
    sleeves["rsi_band_USDJPY_M15"] = m15m30_sleeve("USDJPY", "M15")

    frame = pd.DataFrame(sleeves).sort_index()

    # --- main book: carry + GBPUSD only. EURUSD excluded because it fails
    #     independent HistData (2005-2025): design -28.95%, OOS -5.11%.
    #     The broker-tape pass (2018-2026) is a data-window artifact.
    main_cols = ["carry_mom_filter", "rsi_band_GBPUSD_H1"]
    main = frame.loc[frame[main_cols].notna().all(axis=1), main_cols]
    w_main = inverse_vol_weights(main, main_cols)
    rep_main, combined_main = split_report(main, main_cols, w_main, "main book")

    # --- secondary: EURUSD sleeve reported separately (broker tape only)
    eur_cols = ["rsi_band_EURUSD_H1"]
    eur = frame.loc[frame[eur_cols].notna().all(axis=1), eur_cols]
    w_eur = inverse_vol_weights(eur, eur_cols)
    rep_eur, combined_eur = split_report(eur, eur_cols, w_eur, "EURUSD sleeve (broker tape only)")

    # --- tertiary: add the 3-year M15 sleeve on its own overlap
    all_cols = main_cols + ["rsi_band_USDJPY_M15"]
    full = frame.loc[frame[all_cols].notna().all(axis=1), all_cols]
    w_all = inverse_vol_weights(full, all_cols)
    rep_all, combined_all = split_report(full, all_cols, w_all, "main + M15")

    print_book(rep_main)
    print_book(rep_eur)
    print_book(rep_all)

    dv = main.loc[main.index < DV_END.to_period("M")]
    corr = dv.corr().round(2)
    print("\n=== monthly correlation, design+validation ===")
    print(corr.to_string())

    publish_results("portfolio_book",
                    pd.Series(combined_main.to_numpy(),
                              index=combined_main.index.to_timestamp("M")),
                    already_monthly=True)

    out = pd.DataFrame({
        "date": combined_main.index.to_timestamp("M"),
        "return_pct": combined_main.to_numpy() * 100.0,
    })
    for c in main_cols:
        out[c] = main[c].to_numpy() * 100.0
    out["equity"] = (1 + combined_main).cumprod().to_numpy()
    out.to_csv(OUT_MONTHLY, index=False)

    manifest = {
        "main_book": rep_main,
        "eurusd_sleeve": rep_eur,
        "main_plus_m15": rep_all,
        "correlation_dv": corr.to_dict(),
        "weight_rule": "inverse-vol on design+validation, cap 50%, OOS never used",
        "csv": str(OUT_MONTHLY.relative_to(HERE.parent)),
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\nWrote {OUT_MONTHLY.name}, {OUT_MANIFEST.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
