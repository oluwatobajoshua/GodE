#!/usr/bin/env python3
"""Daily cross-sectional mean reversion + carry* for G10 FX.

Pairs: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD, EURJPY,
GBPJPY, AUDJPY.  At each day rank pairs by 20-day z-score; long the 3 most
negative (undervalued) and short the 3 most positive (overvalued).  Exit when
z-score crosses zero or after 5 days.  Carry* overlay tilts toward pairs with
high positive interest-rate differential.

Run:
    .\\.venv\\Scripts\\python.exe research/xsec_meanrev.py
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

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
         "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY"]
# sign: convert pair spot change to USD-base? We keep raw pair returns.
TOP_N = 3
HOLD_DAYS = 5
Z_WINDOW = 20
SPLITS = {
    "design_2010_2017": ("2010-01-01", "2018-01-01"),
    "validation_2018_2020": ("2018-01-01", "2021-01-01"),
    "OOS_2021_2026": ("2021-01-01", "2027-01-01"),
    "recent_2024_2026": ("2024-01-01", "2027-01-01"),
    "full": ("2005-01-01", "2027-01-01"),
}


def load_prices() -> pd.DataFrame:
    frames = {}
    for p in PAIRS:
        path = DATA / f"{p}_D1.csv"
        if not path.exists():
            continue
        d = pd.read_csv(path)
        d.columns = [c.lower() for c in d.columns]
        # time column may be 'time' or 'date'
        tcol = "time" if "time" in d.columns else "date"
        d[tcol] = pd.to_datetime(d[tcol], utc=True, errors="coerce").dt.tz_convert(None)
        d = d.dropna(subset=[tcol]).drop_duplicates(tcol).set_index(tcol).sort_index()
        frames[p] = d["close"].astype(float)
    if not frames:
        return synthetic_prices()
    out = pd.DataFrame(frames).sort_index()
    # forward fill small gaps
    out = out.ffill(limit=3)
    # ensure all requested pairs present
    for p in PAIRS:
        if p not in out.columns:
            rng = np.random.default_rng(abs(hash(p)) % (2**32))
            out[p] = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.007, size=len(out))))
    return out.sort_index()


def synthetic_prices() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2008-01-01", "2026-10-26")
    data = {}
    for p in PAIRS:
        r = rng.normal(0, 0.007, size=len(idx))
        data[p] = 100.0 * np.exp(np.cumsum(r))
    return pd.DataFrame(data, index=idx)


def load_rates() -> pd.DataFrame:
    """Load real policy-rate series from research/data/rates/*.csv.

    Returns a DataFrame indexed by date with one column per currency code
    (EUR, GBP, USD, JPY, CHF, AUD, CAD, NZD). Missing months are forward-filled.
    Returns None if no rate files are present.
    """
    code = {"EUR": "EZ", "GBP": "GB", "USD": "US", "JPY": "JP", "CHF": "CH",
            "AUD": "AU", "CAD": "CA", "NZD": "NZ"}
    rframes = {}
    for c, cd in code.items():
        path = RATES / f"{cd}.csv"
        if not path.exists():
            continue
        d = pd.read_csv(path)
        d.columns = ["date", "value"]
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d["value"] = pd.to_numeric(d["value"], errors="coerce")
        d = d.dropna().drop_duplicates("date").set_index("date")["value"].sort_index()
        rframes[c] = d
    if not rframes:
        return None
    rates = pd.DataFrame(rframes).sort_index().ffill()
    return rates


def rate_diff_series(prices: pd.DataFrame, rates: pd.DataFrame | None) -> pd.DataFrame:
    # returns daily interest-rate differential (base - quote) in annualized pct /100
    if rates is None:
        # synthetic: random walk differentials
        rng = np.random.default_rng(7)
        out = pd.DataFrame(index=prices.index, columns=PAIRS, dtype=float)
        for p in PAIRS:
            out[p] = rng.normal(0, 0.01, size=len(prices))
        return out
    # map pair to base/quote
    base = {"EURUSD": "EUR", "GBPUSD": "GBP", "USDJPY": "USD", "USDCHF": "USD",
            "AUDUSD": "AUD", "USDCAD": "USD", "NZDUSD": "NZD", "EURJPY": "EUR",
            "GBPJPY": "GBP", "AUDJPY": "AUD"}
    quote = {"EURUSD": "USD", "GBPUSD": "USD", "USDJPY": "JPY", "USDCHF": "CHF",
             "AUDUSD": "USD", "USDCAD": "CAD", "NZDUSD": "USD", "EURJPY": "JPY",
             "GBPJPY": "JPY", "AUDJPY": "JPY"}
    # reindex rates to daily
    r = rates.reindex(prices.index, method="ffill")
    out = pd.DataFrame(index=prices.index, columns=PAIRS, dtype=float)
    for p in PAIRS:
        out[p] = (r[base[p]] - r[quote[p]]) / 100.0
    return out


def compute_z(prices: pd.DataFrame, window: int = Z_WINDOW) -> pd.DataFrame:
    sma = prices.rolling(window=window).mean()
    sd = prices.rolling(window=window).std(ddof=1)
    return (prices - sma) / sd


def _carry_weight(carry_row: pd.Series, pair: str, side: int, carry_tilt: float) -> float:
    """Match EA carry tilt: weight = 1 + tilt * clip(carry)."""
    c = float(carry_row.get(pair, 0.0))
    c = max(-1.0, min(1.0, c))
    return 1.0 + carry_tilt * c


def backtest(
    prices: pd.DataFrame,
    z: pd.DataFrame,
    carry: pd.DataFrame,
    top_n: int = TOP_N,
    hold_days: int = HOLD_DAYS,
    entry_z: float = 1.5,
    cost_bps: float = 2.0,
    carry_tilt: float = 0.5,
) -> pd.Series:
    """Daily cross-sectional mean reversion with carry* weights."""
    rets = prices.pct_change()
    cols = list(prices.columns)
    pos = {p: {"side": 0, "days": 0} for p in cols}
    out: list[tuple[pd.Timestamp, float]] = []

    for dt in z.index:
        if dt not in rets.index:
            continue
        zrow = z.loc[dt]
        carry_row = carry.loc[dt] if dt in carry.index else pd.Series(0.0, index=cols)

        held = {}
        gross = 0.0
        for p in cols:
            side = pos[p]["side"]
            if side == 0:
                held[p] = 0.0
                continue
            w = abs(_carry_weight(carry_row, p, side, carry_tilt))
            held[p] = side * w
            gross += w
        if gross > 0:
            held = {p: held[p] / gross for p in cols}

        r_today = rets.loc[dt].fillna(0.0)
        port = sum(held.get(p, 0.0) * r_today.get(p, 0.0) for p in cols)
        out.append((dt, port))

        zvalid = zrow.dropna()
        if not zvalid.empty:
            valid = zvalid.sort_values()
            longs = [p for p in valid.head(top_n).index if valid[p] <= -entry_z]
            shorts = [p for p in valid.tail(top_n).index if valid[p] >= entry_z]
        else:
            longs, shorts = [], []

        for p in cols:
            if pos[p]["side"] == 0:
                continue
            pos[p]["days"] += 1
            zval = zrow.get(p, np.nan)
            exit_now = pos[p]["days"] >= hold_days
            if pos[p]["side"] > 0 and pd.notna(zval) and zval >= 0:
                exit_now = True
            if pos[p]["side"] < 0 and pd.notna(zval) and zval <= 0:
                exit_now = True
            if exit_now:
                pos[p] = {"side": 0, "days": 0}

        new_side = {p: 0 for p in cols}
        for p in longs:
            if pos[p]["side"] == 0:
                new_side[p] = 1
        for p in shorts:
            if pos[p]["side"] == 0:
                new_side[p] = -1

        new_w = {}
        new_gross = 0.0
        for p in cols:
            side = new_side[p] if pos[p]["side"] == 0 else pos[p]["side"]
            if side == 0:
                new_w[p] = 0.0
                continue
            w = abs(_carry_weight(carry_row, p, side, carry_tilt))
            new_w[p] = side * w
            new_gross += w
        if new_gross > 0:
            new_w = {p: new_w[p] / new_gross for p in cols}

        turnover = sum(abs(new_w.get(p, 0.0) - held.get(p, 0.0)) for p in cols) / 2.0
        if turnover > 0 and out:
            out[-1] = (dt, out[-1][1] - turnover * cost_bps / 10000.0)

        for p in cols:
            if new_side[p] != 0 and pos[p]["side"] == 0:
                pos[p] = {"side": new_side[p], "days": 0}

    if not out:
        return pd.Series(dtype=float, name="return")
    idx, vals = zip(*out)
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name="return")


def split_returns(r: pd.Series) -> dict[str, pd.Series]:
    out = {}
    for name, (start, end) in SPLITS.items():
        mask = (r.index >= start) & (r.index < end)
        out[name] = r.loc[mask]
    return out


def cointegration_pairs(prices: pd.DataFrame, train_window: int = 252,
                        test_window: int = 21, entry_z: float = 2.0,
                        exit_z: float = 0.5, cost_bps: float = 2.0) -> pd.Series:
    """Cointegration-based pairs trading (walk-forward).

    For each rolling window:
      1. Scan all pairs for cointegration on the training window.
      2. Estimate hedge ratio via OLS on log prices.
      3. Trade the spread on the test window using z-score signals.

    Returns daily portfolio return series.
    """
    logp = np.log(prices)
    cols = list(prices.columns)
    n = len(cols)
    daily = []
    prev_pos = {p: 0.0 for p in cols}

    i = train_window
    while i < len(prices) - test_window:
        train = logp.iloc[i - train_window:i]
        test = logp.iloc[i:i + test_window]

        # Scan for cointegrated pairs
        best_pairs = []
        for a in range(n):
            for b in range(a + 1, n):
                y, x = train.iloc[:, a], train.iloc[:, b]
                try:
                    from statsmodels.tsa.stattools import coint
                    score, pval, _ = coint(y, x)
                    if pval < 0.05:
                        best_pairs.append((pval, a, b))
                except Exception:
                    continue

        best_pairs.sort()
        used = set()
        spread_weights = {}
        for pval, a, b in best_pairs[:8]:
            if a in used or b in used:
                continue
            used.add(a)
            used.add(b)
            y, x = train.iloc[:, a], train.iloc[:, b]
            beta = np.polyfit(x, y, 1)[0]
            spread = y - beta * x
            sma = spread.rolling(20).mean()
            sd = spread.rolling(20).std()
            z = (spread - sma) / sd
            spread_weights[(a, b)] = (beta, z)

        # Trade the test window
        for j, dt in enumerate(test.index):
            zrow = {}
            for (a, b), (beta, zhist) in spread_weights.items():
                if dt in zhist.index:
                    zrow[(a, b)] = zhist.loc[dt]

            cur = {p: 0.0 for p in cols}
            for (a, b), zval in zrow.items():
                if pd.isna(zval):
                    continue
                if zval <= -entry_z:
                    cur[cols[a]] += 1.0
                    cur[cols[b]] -= 1.0
                elif zval >= entry_z:
                    cur[cols[a]] -= 1.0
                    cur[cols[b]] += 1.0

            tot = sum(abs(v) for v in cur.values())
            if tot > 0:
                for p in cols:
                    cur[p] /= tot

            turnover = sum(abs(cur[p] - prev_pos[p]) for p in cols) / 2.0
            cost = turnover * cost_bps / 10000.0
            r_today = prices.pct_change().loc[dt]
            port = sum(cur[p] * (r_today[p] if pd.notna(r_today[p]) else 0.0) for p in cols)
            daily.append((dt, port - cost))

            for p in cols:
                prev_pos[p] = cur[p]

        i += test_window

    if not daily:
        return pd.Series(dtype=float)
    res = pd.Series([d[1] for d in daily], index=[d[0] for d in daily], name="return")
    return res


def metrics(r: pd.Series, label: str) -> dict:
    r = r.dropna()
    if r.empty or r.std() == 0:
        return {"strategy": label, "cagr": 0, "sharpe": 0, "maxdd": 0,
                "winrate": 0, "profitfactor": 0, "n": 0}
    eq = (1 + r).cumprod()
    years = (r.index[-1] - r.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else 0
    sharpe = r.mean() / r.std() * np.sqrt(252)
    dd = (eq / eq.cummax() - 1).min()
    wins = r[r > 0]
    losses = r[r < 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.nan
    return {
        "strategy": label,
        "start": str(r.index[0].date()),
        "end": str(r.index[-1].date()),
        "years": round(years, 1),
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "maxdd": round(dd * 100, 2),
        "winrate": round((r > 0).mean() * 100, 2),
        "profitfactor": round(pf, 2) if pd.notna(pf) else None,
        "n": len(r),
    }


def yearly_returns(r: pd.Series) -> pd.Series:
    return r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1) * 100


def main() -> int:
    prices = load_prices()
    z = compute_z(prices, Z_WINDOW)
    rates = load_rates()
    carry = rate_diff_series(prices, rates)
    OUT.mkdir(parents=True, exist_ok=True)

    baseline = backtest(prices, z, carry, top_n=TOP_N, hold_days=HOLD_DAYS,
                        entry_z=1.5, cost_bps=2.0, carry_tilt=0.5)

    rows = []
    for split, part in split_returns(baseline).items():
        rows.append({"split": split, **metrics(part, "xsec_meanrev_carry")})
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "xsec_meanrev_summary.csv", index=False)
    yearly_returns(baseline).to_csv(OUT / "xsec_meanrev_yearly.csv", header=["return_pct"])

    print(summary.to_string(index=False))

    sweep_rows = []
    for zw in [10, 20, 40]:
        for top_n in [2, 3]:
            for hold_days in [5, 10]:
                for entry_z in [1.0, 1.5]:
                    for cost_bps in [0.0, 2.0]:
                        z_s = compute_z(prices, zw)
                        r = backtest(prices, z_s, carry, top_n=top_n,
                                     hold_days=hold_days, entry_z=entry_z,
                                     cost_bps=cost_bps)
                        m = metrics(r, "sweep")
                        sweep_rows.append({
                            "z_window": zw, "top_n": top_n,
                            "hold_days": hold_days, "entry_z": entry_z,
                            "cost_bps": cost_bps,
                            **m,
                        })
    pd.DataFrame(sweep_rows).to_csv(OUT / "xsec_meanrev_sweep.csv", index=False)
    print(f"\nWrote {OUT / 'xsec_meanrev_summary.csv'} and sweep.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
