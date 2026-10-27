#!/usr/bin/env python3
"""CFTC Commitments of Traders currency-positioning research.

This is a genuine sentiment dataset, not another price-derived indicator.  The
financial-futures COT report is published Friday for Tuesday positions, so the
signal is lagged one week before it is applied to spot returns.

Currencies: EUR, GBP, JPY, AUD, CAD, CHF.  The spot return is expressed as the
currency's return versus USD (JPY/CAD/CHF pairs are inverted).

Tests:
* fade extreme leveraged-money positioning (contrarian)
* follow extreme leveraged-money positioning (trend)
* follow the 4-week change in positioning (flow)

Splits:
* design:      2013-01-01 .. 2015-12-31  (after the 3-year z-score warm-up)
* validation:  2016-01-01 .. 2020-12-31
* OOS:         2021-01-01 .. 2026-10-26
* recent:      2025-01-01 .. 2026-10-26

Run:
    .\\.venv\\Scripts\\python.exe research\\cot_sentiment.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
COT = HERE / "data" / "cot"
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))

TARGET_VOL = 0.10
COST_PTS = 12.0

CURRENCIES = {
    "EUR": ("EURO FX - CHICAGO", "EURUSD", 1.0, 0.00001),
    "GBP": ("BRITISH POUND - CHICAGO", "GBPUSD", 1.0, 0.00001),
    "JPY": ("JAPANESE YEN - CHICAGO", "USDJPY", -1.0, 0.001),
    "AUD": ("AUSTRALIAN DOLLAR - CHICAGO", "AUDUSD", 1.0, 0.00001),
    "CAD": ("CANADIAN DOLLAR - CHICAGO", "USDCAD", -1.0, 0.00001),
    "CHF": ("SWISS FRANC - CHICAGO", "USDCHF", -1.0, 0.00001),
}


def load_cot() -> pd.DataFrame:
    frames = []
    for path in sorted(COT.glob("fut_fin_*.csv")):
        d = pd.read_csv(path, low_memory=False)
        date_col = "Report_Date_as_YYYY-MM-DD" if "Report_Date_as_YYYY-MM-DD" in d.columns else "Report_Date_as_MM_DD_YYYY"
        d["report_date"] = pd.to_datetime(d[date_col], errors="coerce", format="mixed")
        if d["report_date"].isna().all():
            d["report_date"] = pd.to_datetime(d[date_col], errors="coerce")
        d = d.dropna(subset=["report_date"])
        for ccy, (name, _pair, _sign, _point) in CURRENCIES.items():
            m = d[d["Market_and_Exchange_Names"].astype(str).str.contains(name, regex=False)].copy()
            if m.empty:
                continue
            m["currency"] = ccy
            m["net_lev"] = m["Lev_Money_Positions_Long_All"] - m["Lev_Money_Positions_Short_All"]
            m["net_asset"] = m["Asset_Mgr_Positions_Long_All"] - m["Asset_Mgr_Positions_Short_All"]
            m["oi"] = m["Open_Interest_All"]
            m["net_pct"] = m["net_lev"] / m["oi"]
            m["asset_pct"] = m["net_asset"] / m["oi"]
            frames.append(m[["report_date", "currency", "net_pct", "asset_pct", "oi"]])
    if not frames:
        raise SystemExit("no COT data")
    cot = pd.concat(frames, ignore_index=True).drop_duplicates(["report_date", "currency"])
    cot = cot.pivot(index="report_date", columns="currency", values="net_pct").sort_index()
    # Report is released Friday for Tuesday positions.
    cot.index = cot.index + pd.Timedelta(days=3)
    return cot.resample("W-FRI").last()


def load_weekly_prices() -> pd.DataFrame:
    frames = {}
    for ccy, (_name, pair, _sign, _point) in CURRENCIES.items():
        path = DATA / f"{pair}_D1.csv"
        d = pd.read_csv(path, usecols=["time", "close"], parse_dates=["time"])
        d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
        d = d.drop_duplicates("time").set_index("time")["close"].sort_index()
        frames[pair] = d
    prices = pd.DataFrame(frames).sort_index()
    return prices.resample("W-FRI").last()


def build_z(cot: pd.DataFrame, window: int) -> pd.DataFrame:
    mean = cot.rolling(window, min_periods=max(26, window // 2)).mean()
    std = cot.rolling(window, min_periods=max(26, window // 2)).std()
    return (cot - mean) / std


def strategy_returns(
    signal: pd.DataFrame,
    ccy_ret: pd.DataFrame,
    pair_price: pd.DataFrame,
    vol_target: float = TARGET_VOL,
    label: str = "",
) -> pd.Series:
    # Signal known after Friday close is applied to the next week.
    pos = signal.shift(1)
    n_active = (pos.abs() > 0).sum(axis=1).replace(0, np.nan)
    weights = pos.div(n_active, axis=0).fillna(0.0)
    raw = (weights * ccy_ret).sum(axis=1, min_count=1).fillna(0.0)
    pvol = raw.rolling(52).std() * np.sqrt(52)
    scalar = (vol_target / pvol).clip(upper=3.0).shift(1).fillna(1.0)
    weights = weights.mul(scalar, axis=0)
    points = pd.Series({ccy: meta[3] for ccy, meta in CURRENCIES.items()})
    per_unit = (COST_PTS * points) / pair_price
    cost = (weights.diff().abs() * per_unit).sum(axis=1, min_count=1).fillna(0.0)
    return ((weights * ccy_ret).sum(axis=1, min_count=1) - cost).dropna()


def metrics(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    if r.empty:
        return {"strategy": label, "start": "", "end": "", "years": 0, "cagr": 0, "vol": 0, "sharpe": 0, "maxdd": 0, "total": 0}
    eq = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    vol = r.std() * np.sqrt(52)
    sharpe = r.mean() / r.std() * np.sqrt(52) if r.std() > 0 else 0
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
        "design_2013_2015": r[(r.index >= "2013-01-01") & (r.index < "2016-01-01")],
        "validation_2016_2020": r[(r.index >= "2016-01-01") & (r.index < "2021-01-01")],
        "OOS_2021_2026": r[r.index >= "2021-01-01"],
        "recent_2025_2026": r[r.index >= "2025-01-01"],
        "full_2013_2026": r[r.index >= "2013-01-01"],
    }


def main() -> int:
    cot = load_cot()
    prices = load_weekly_prices()
    weekly_index = prices.index
    cot = cot.reindex(weekly_index, method="ffill")

    ccy_ret = pd.DataFrame(index=weekly_index)
    pair_price = pd.DataFrame(index=weekly_index)
    for ccy, (_name, pair, sign, _point) in CURRENCIES.items():
        ccy_ret[ccy] = prices[pair].pct_change() * sign
        pair_price[ccy] = prices[pair]

    strategies = {}
    for window in (52, 156):
        z = build_z(cot, window)
        for threshold in (1.5, 2.0):
            fade = -np.sign(z) * (z.abs() >= threshold)
            follow = np.sign(z) * (z.abs() >= threshold)
            strategies[f"fade_z{threshold}_w{window}"] = strategy_returns(fade, ccy_ret, pair_price, label="")
            strategies[f"follow_z{threshold}_w{window}"] = strategy_returns(follow, ccy_ret, pair_price, label="")
        d4 = cot.diff(4)
        mean = d4.rolling(window, min_periods=26).mean()
        std = d4.rolling(window, min_periods=26).std()
        fz = (d4 - mean) / std
        for threshold in (1.5, 2.0):
            flow = np.sign(fz) * (fz.abs() >= threshold)
            strategies[f"flow_z{threshold}_w{window}"] = strategy_returns(flow, ccy_ret, pair_price, label="")

    rows = []
    curves = {}
    for name, r in strategies.items():
        curves[name] = r
        for split, part in split_returns(r).items():
            rows.append({"name": name, "split": split, **metrics(part, name)})
    summary = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "cot_sentiment_summary.csv", index=False)
    pd.DataFrame(curves).to_csv(OUT / "cot_sentiment_returns.csv")

    print("=== COT sentiment: full period ===")
    print(summary[summary["split"] == "full_2013_2026"].sort_values("sharpe", ascending=False).to_string(index=False))
    print("\n=== COT sentiment: CAGR by split ===")
    piv = summary.pivot(index="name", columns="split", values="cagr")
    print(piv.round(2).to_string())
    good = piv[(piv["design_2013_2015"] > 0) & (piv["validation_2016_2020"] > 0)]
    print("\n=== positive in design AND validation ===")
    print(good.round(2).to_string() if len(good) else "none")
    if len(good):
        print("\n=== OOS and recent for those ===")
        print(piv.loc[good.index, ["OOS_2021_2026", "recent_2025_2026"]].round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
