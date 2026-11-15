#!/usr/bin/env python3
"""G10 FX carry research using FRED short-term interest rates.

Carry is the last major FX premium not yet tested.  At each month-end we rank
the non-USD currencies by their short-rate differential versus USD, go long the
top three and short the bottom three, and hold for the next month.  The return
includes both spot and the interest differential.

The rate used for a month is the previous month's average, so the signal is
known before the holding period starts.  Costs are the monthly turnover times
the spread.

Variants:
* pure carry
* carry with a 12-month momentum filter (only long positive-momentum
  currencies and short negative-momentum currencies)
* carry with a 12-month trend filter on USD (long carry only when the dollar is
  not in a strong uptrend)

Splits:
* design:      2005-01-01 .. 2014-12-31
* validation:  2015-01-01 .. 2019-12-31
* OOS:         2020-01-01 .. 2026-10-26
* recent:      2025-01-01 .. 2026-10-26

Run:
    .\\.venv\\Scripts\\python.exe research/carry.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
RATES = HERE / "data" / "rates"
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))

TARGET_VOL = 0.10
COST_PTS = 12.0

CURRENCIES = {
    "EUR": ("EURUSD", 1.0, 0.00001, "EZ"),
    "GBP": ("GBPUSD", 1.0, 0.00001, "GB"),
    "JPY": ("USDJPY", -1.0, 0.001, "JP"),
    "AUD": ("AUDUSD", 1.0, 0.00001, "AU"),
    "NZD": ("NZDUSD", 1.0, 0.00001, "NZ"),
    "CAD": ("USDCAD", -1.0, 0.00001, "CA"),
    "CHF": ("USDCHF", -1.0, 0.00001, "CH"),
}
USD_RATE = "US"


def load_rates() -> pd.DataFrame:
    frames = {}
    for ccy, (_pair, _sign, _point, code) in CURRENCIES.items():
        path = RATES / f"{code}.csv"
        d = pd.read_csv(path)
        d.columns = ["date", "value"]
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d["value"] = pd.to_numeric(d["value"], errors="coerce")
        d = d.dropna().drop_duplicates("date").set_index("date")["value"].sort_index()
        frames[ccy] = d
    us = pd.read_csv(RATES / f"{USD_RATE}.csv")
    us.columns = ["date", "value"]
    us["date"] = pd.to_datetime(us["date"], errors="coerce")
    us["value"] = pd.to_numeric(us["value"], errors="coerce")
    us = us.dropna().drop_duplicates("date").set_index("date")["value"].sort_index()
    frames["USD"] = us
    rates = pd.DataFrame(frames).sort_index()
    # Data are monthly observations dated the first of the month.  Keep that
    # convention; a month-t signal uses the prior month's observation.
    rates.index = rates.index.to_period("M").to_timestamp()
    return rates


def load_monthly_prices() -> tuple[pd.DataFrame, pd.DataFrame]:
    spot = {}
    price = {}
    for ccy, (pair, sign, _point, _code) in CURRENCIES.items():
        d = pd.read_csv(DATA / f"{pair}_D1.csv", usecols=["time", "close"], parse_dates=["time"])
        d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
        d = d.drop_duplicates("time").set_index("time")["close"].sort_index()
        m = d.resample("ME").last()
        m.index = m.index.to_period("M").to_timestamp()
        price[ccy] = m
        spot[ccy] = m.pct_change() * sign
    return pd.DataFrame(spot), pd.DataFrame(price)


def metrics(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    if r.empty:
        return {"strategy": label, "start": "", "end": "", "years": 0, "cagr": 0, "vol": 0, "sharpe": 0, "maxdd": 0, "total": 0}
    eq = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    vol = r.std() * np.sqrt(12)
    sharpe = r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return {
        "strategy": label,
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "years": round(years, 1),
        "cagr": round(cagr * 100, 2),
        "vol": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(dd * 100, 2),
        "total": round((eq.iloc[-1] - 1) * 100, 2),
    }


def split_returns(r: pd.Series) -> dict[str, pd.Series]:
    return {
        "design_2005_2014": r[(r.index >= "2005-01-01") & (r.index < "2015-01-01")],
        "validation_2015_2019": r[(r.index >= "2015-01-01") & (r.index < "2020-01-01")],
        "OOS_2020_2026": r[r.index >= "2020-01-01"],
        "recent_2025_2026": r[r.index >= "2025-01-01"],
        "full_2005_2026": r[r.index >= "2005-01-01"],
    }


def carry_weights(diff: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    rank = diff.rank(axis=1, ascending=False)
    n = diff.notna().sum(axis=1)
    long = rank.le(top_n)
    short = rank.gt(n - top_n, axis=0)
    n_long = long.sum(axis=1).replace(0, np.nan)
    n_short = short.sum(axis=1).replace(0, np.nan)
    return long.div(n_long, axis=0).fillna(0) - short.div(n_short, axis=0).fillna(0)


def run(
    weights: pd.DataFrame,
    spot: pd.DataFrame,
    diff: pd.DataFrame,
    price: pd.DataFrame,
    vol_target: float = TARGET_VOL,
    cost_pts: float = COST_PTS,
) -> pd.Series:
    # Weights are already based on the prior month's rate differential, so they
    # apply to the current month directly.
    w = weights.fillna(0.0)
    carry = diff.shift(1) / 1200.0
    raw = (w * (spot + carry)).sum(axis=1, min_count=1).fillna(0.0)
    pvol = raw.rolling(12).std() * np.sqrt(12)
    scalar = (vol_target / pvol).clip(upper=2.0).shift(1).fillna(1.0)
    w = w.mul(scalar, axis=0)
    points = pd.Series({ccy: meta[2] for ccy, meta in CURRENCIES.items()})
    per_unit = (cost_pts * points) / price.shift(1)
    cost = (w.diff().abs() * per_unit).sum(axis=1, min_count=1).fillna(0.0)
    return ((w * (spot + carry)).sum(axis=1, min_count=1) - cost).dropna()


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]

    # Prior-month rate differential versus USD.  Positive = carry advantage.
    diff = rates[CURRENCIES.keys()].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)

    # 12-month spot momentum, expressed as currency return vs USD.
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1

    strategies = {}
    base_w = carry_weights(diff_prev, top_n=3)
    strategies["carry_pure"] = run(base_w, spot, diff, price)

    # Momentum filter: only long currencies with positive momentum, only short
    # currencies with negative momentum.  Use momentum known at the start of
    # the month.
    mom_prev = mom.shift(1)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.astype(float) - short_ok.astype(float)
    n_long = long_ok.sum(axis=1).replace(0, np.nan)
    n_short = short_ok.sum(axis=1).replace(0, np.nan)
    filtered = long_ok.div(n_long, axis=0).fillna(0) - short_ok.div(n_short, axis=0).fillna(0)
    strategies["carry_mom_filter"] = run(filtered, spot, diff, price)

    # Dollar trend filter: if the USD is in a strong 12-month uptrend, cut gross
    # carry exposure.  The dollar trend is measured from the prior month's data.
    usd_trend = (1 + spot[["EUR", "GBP", "AUD", "NZD"]].shift(1).mean(axis=1)).rolling(12).apply(np.prod, raw=True) - 1
    risk_on = usd_trend.le(0)
    strategies["carry_usd_filter"] = run(base_w.mul(risk_on.astype(float), axis=0), spot, diff, price)

    rows = []
    curves = {}
    for name, r in strategies.items():
        curves[name] = r
        for split, part in split_returns(r).items():
            rows.append({"name": name, "split": split, **metrics(part, name)})
    summary = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "carry_summary.csv", index=False)
    pd.DataFrame(curves).to_csv(OUT / "carry_returns.csv")

    print("=== FX carry: full period ===")
    print(summary[summary["split"] == "full_2005_2026"].sort_values("sharpe", ascending=False).to_string(index=False))
    print("\n=== FX carry: CAGR by split ===")
    piv = summary.pivot(index="name", columns="split", values="cagr")
    print(piv.round(2).to_string())
    good = piv[(piv["design_2005_2014"] > 0) & (piv["validation_2015_2019"] > 0)]
    print("\n=== positive in design AND validation ===")
    print(good.round(2).to_string() if len(good) else "none")
    if len(good):
        print("\n=== OOS and recent for those ===")
        print(piv.loc[good.index, ["OOS_2020_2026", "recent_2025_2026"]].round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
