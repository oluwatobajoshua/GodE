#!/usr/bin/env python3
"""How many pips does FX 'run' per day, and how much can strategies capture?

Daily range = high - low (pips). Also reports ATR(D1), M5 session ranges,
and pip P&L from simple rule backtests on the same data.

Run:
    .\\.venv\\Scripts\\python.exe research\\daily_pip_budget.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUT_SUM = HERE / "results" / "daily_pip_budget_summary.csv"
OUT_YEAR = HERE / "results" / "daily_pip_budget_yearly.csv"
PIPSIZE = 0.0001  # EURUSD, GBPUSD majors
SYMS = ("EURUSD", "GBPUSD", "USDJPY")


def load(sym: str, tf: str) -> pd.DataFrame:
    p = DATA / f"{sym}_{tf}.csv"
    d = pd.read_csv(p, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
    return d.sort_values("time").set_index("time")


def pip_size(sym: str) -> float:
    return 0.01 if "JPY" in sym else 0.0001


def daily_range_table(d1: pd.DataFrame, sym: str) -> pd.DataFrame:
    h = d1["high"].astype(float)
    l = d1["low"].astype(float)
    c = d1["close"].astype(float)
    ps = pip_size(sym)
    rng_pips = (h - l) / ps
    body_pips = (c - d1["open"].astype(float)).abs() / ps
    day = pd.DataFrame(
        {
            "range_pips": rng_pips,
            "body_pips": body_pips,
            "up_pips": (h - c.shift(1)).clip(lower=0) / ps,
            "down_pips": (c.shift(1) - l).clip(lower=0) / ps,
        },
        index=d1.index,
    )
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    day["atr14_pips"] = tr.rolling(14).mean() / ps
    return day.dropna()


def m5_session_ranges(m5: pd.DataFrame, sym: str) -> pd.DataFrame:
    ps = pip_size(sym)
    day_index = m5.index.normalize()
    rows = []
    for d, grp in m5.groupby(day_index):
        if len(grp) < 50:
            continue

        def session(h0: int, h1: int) -> float:
            sub = grp[(grp.index.hour >= h0) & (grp.index.hour < h1)]
            if sub.empty:
                return np.nan
            return (sub["high"].max() - sub["low"].min()) / ps

        rows.append(
            {
                "date": d,
                "full_day_pips": (grp["high"].max() - grp["low"].min()) / ps,
                "asia_pips": session(0, 7),
                "london_pips": session(7, 13),
                "ny_pips": session(13, 22),
            }
        )
    return pd.DataFrame(rows).set_index("date")


def summarize_distribution(s: pd.Series, label: str, sym: str) -> dict:
    s = s.dropna()
    return {
        "symbol": sym,
        "metric": label,
        "n_days": len(s),
        "mean_pips": round(s.mean(), 1),
        "median_pips": round(s.median(), 1),
        "p25_pips": round(s.quantile(0.25), 1),
        "p75_pips": round(s.quantile(0.75), 1),
        "p90_pips": round(s.quantile(0.90), 1),
        "max_pips": round(s.max(), 1),
    }


def strategy_pip_capture(sym: str) -> list[dict]:
    """Quick pip accounting on EMA21 pullback (same logic as daily_ohlc)."""
    from daily_ohlc_multi_trade import Rules, load_m5, trades_ema_pullback_m5

    m5 = load_m5(sym)
    ps = pip_size(sym)
    c = m5["close"].astype(float)
    rows = []
    for name, sl, tp in [("ema21_rr_1_2", 1.0, 2.0), ("ema21_rr_125_25", 1.25, 2.5)]:
        rules = Rules(name, sl, tp, 24)
        rets = trades_ema_pullback_m5(m5, rules)
        if rets.empty:
            continue
        # approximate pips from return * price at trade
        pip_list = []
        for t, r in rets.items():
            if t not in c.index:
                loc = c.index.searchsorted(t)
                px = c.iloc[min(loc, len(c) - 1)]
            else:
                px = c.loc[t]
            pip_list.append(r * px / ps)
        p = pd.Series(pip_list, index=rets.index)
        daily_pips = p.groupby(p.index.normalize()).sum()
        for split, start in [("OOS_2024+", "2024-07-01"), ("full", "2019-01-01")]:
            if split != "full":
                sub = p[p.index >= pd.Timestamp(start)]
            else:
                sub = p
            dsub = daily_pips[daily_pips.index >= pd.Timestamp(start)] if split != "full" else daily_pips
            if len(sub) < 30:
                continue
            rows.append(
                {
                    "symbol": sym,
                    "strategy": name,
                    "split": split,
                    "trades": len(sub),
                    "avg_pips_per_trade": round(sub.mean(), 2),
                    "avg_pips_per_day": round(dsub.mean(), 2),
                    "winrate_pct": round(100 * (sub > 0).mean(), 1),
                    "pct_days_positive_pips": round(100 * (dsub > 0).mean(), 1) if len(dsub) > 10 else None,
                }
            )
    return rows


def carry_pip_proxy(sym: str) -> dict | None:
    """Monthly carry sleeve — not daily pips; show monthly 'move' in pips for context."""
    path = HERE / "results" / "carry_returns.csv"
    if not path.exists():
        return None
    cr = pd.read_csv(path, index_col=0, parse_dates=True)
    if "carry_mom_filter" not in cr.columns:
        return None
    d1 = load(sym, "D1")
    ps = pip_size(sym)
    avg_daily_range = ((d1["high"] - d1["low"]) / ps).mean()
    r = cr["carry_mom_filter"].dropna()
    # monthly return ~ pips if we attribute to avg price
    px = d1["close"].astype(float).resample("ME").last().reindex(r.index).ffill()
    monthly_pips = (r * px / ps).dropna()
    return {
        "symbol": sym,
        "strategy": "carry_mom_filter_monthly",
        "split": "full",
        "avg_pips_per_month_proxy": round(monthly_pips.mean(), 1),
        "avg_daily_range_context_pips": round(avg_daily_range, 1),
    }


def main() -> int:
    rows = []
    year_rows = []
    strat_rows = []

    print("=== Daily pip budget (how much the market 'runs') ===\n")
    for sym in SYMS:
        d1 = load(sym, "D1")
        m5 = load(sym, "M5")
        day = daily_range_table(d1, sym)
        sess = m5_session_ranges(m5, sym)

        for label, series in [
            ("D1_high_low_range", day["range_pips"]),
            ("D1_ATR14", day["atr14_pips"]),
            ("D1_body", day["body_pips"]),
            ("M5_full_day_range", sess["full_day_pips"]),
            ("M5_london_range", sess["london_pips"]),
            ("M5_ny_range", sess["ny_pips"]),
        ]:
            rows.append(summarize_distribution(series, label, sym))

        # capture ceiling (theoretical, not tradable 100%)
        d1_rng = day["range_pips"]
        for pct, name in [(0.25, "theoretical_25pct_range"), (0.50, "theoretical_50pct_range")]:
            rows.append(summarize_distribution(d1_rng * pct, name, sym))

        yearly = day["range_pips"].groupby(day.index.year).mean()
        for y, v in yearly.items():
            year_rows.append({"symbol": sym, "year": y, "mean_D1_range_pips": round(v, 1)})

        strat_rows.extend(strategy_pip_capture(sym))
        cap = carry_pip_proxy(sym)
        if cap:
            strat_rows.append(cap)

        print(f"--- {sym} ---")
        sub = pd.DataFrame(rows).query("symbol == @sym & metric in ['D1_high_low_range','M5_full_day_range','M5_london_range']")
        print(sub.to_string(index=False))
        print()

    pd.DataFrame(rows).to_csv(OUT_SUM, index=False)
    pd.DataFrame(year_rows).to_csv(OUT_YEAR, index=False)
    st = pd.DataFrame(strat_rows)
    if not st.empty:
        st.to_csv(HERE / "results" / "daily_pip_capture_by_strategy.csv", index=False)

    print("=== Theoretical vs strategy capture (EURUSD) ===")
    print(
        "If you captured 25% of the average D1 range every day, that is a CEILING, "
        "not a plan — friction and direction errors eat most of it."
    )
    eur = pd.DataFrame(rows).query("symbol == 'EURUSD'")
    d1m = eur[eur.metric == "D1_high_low_range"]["mean_pips"].iloc[0]
    t25 = eur[eur.metric == "theoretical_25pct_range"]["mean_pips"].iloc[0]
    print(f"EURUSD avg daily range ~ {d1m} pips | 25% ceiling ~ {t25} pips/day")
    if not st.empty:
        print("\n", st.to_string(index=False))
    print(f"\nWrote {OUT_SUM}, {OUT_YEAR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
