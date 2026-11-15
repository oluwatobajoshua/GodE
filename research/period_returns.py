#!/usr/bin/env python3
"""Aggregate strategy returns to daily / monthly tables (user-facing proof format).

All research scripts should call ``publish_results()`` — stdout is monthly/daily only.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

RESULTS = Path(__file__).resolve().parent / "results"
MONTHLY_OUT = RESULTS / "monthly_returns.csv"
DAILY_OUT = RESULTS / "daily_returns.csv"


def slug(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name).strip("_")[:96]


def to_daily(trade_returns: pd.Series) -> pd.DataFrame:
    """Sum per-trade (or intrabar) returns into calendar days."""
    r = trade_returns.dropna()
    if r.empty:
        return pd.DataFrame(columns=["date", "return_pct", "trades", "equity"])
    idx = r.index.normalize()
    day = pd.DataFrame(
        {
            "return_sum": r.groupby(idx).sum(),
            "trades": r.groupby(idx).count(),
        }
    )
    day["return_pct"] = day["return_sum"] * 100.0
    day["equity"] = (1 + day["return_sum"]).cumprod()
    day = day.reset_index()
    if "date" not in day.columns:
        day = day.rename(columns={day.columns[0]: "date"})
    return day[["date", "return_pct", "trades", "equity"]]


def to_monthly(returns: pd.Series, already_monthly: bool = False) -> pd.DataFrame:
    """Sum to calendar months (or pass through if index is month-end)."""
    r = returns.dropna()
    if r.empty:
        return pd.DataFrame(columns=["date", "return_pct", "trades", "equity", "win_month"])
    if already_monthly:
        g = pd.DataFrame({"return_sum": r.values, "trades": 1}, index=pd.to_datetime(r.index))
    else:
        g = pd.DataFrame(
            {
                "return_sum": r.groupby(r.index.to_period("M")).sum(),
                "trades": r.groupby(r.index.to_period("M")).count(),
            }
        )
        g.index = g.index.to_timestamp("M")
    g["return_pct"] = g["return_sum"] * 100.0
    g["equity"] = (1 + g["return_sum"]).cumprod()
    g["win_month"] = g["return_sum"] > 0
    g = g.reset_index().rename(columns={"index": "date"})
    if "date" not in g.columns and g.columns[0] != "date":
        g = g.rename(columns={g.columns[0]: "date"})
    return g[["date", "return_pct", "trades", "equity", "win_month"]]


def write_monthly_bundle(rows: list[dict]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(MONTHLY_OUT, index=False)
    return MONTHLY_OUT


def write_daily_bundle(rows: list[dict]) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(DAILY_OUT, index=False)
    return DAILY_OUT


def frame_to_monthly_rows(strategy: str, returns: pd.Series, already_monthly: bool = False) -> list[dict]:
    tab = to_monthly(returns, already_monthly=already_monthly)
    rows = []
    for _, row in tab.iterrows():
        rows.append(
            {
                "strategy": strategy,
                "period": "month",
                "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else row["date"],
                "return_pct": round(float(row["return_pct"]), 4),
                "trades": int(row["trades"]),
                "equity": round(float(row["equity"]), 6),
                "win_period": bool(row["win_month"]),
            }
        )
    return rows


def frame_to_daily_rows(strategy: str, returns: pd.Series) -> list[dict]:
    tab = to_daily(returns)
    rows = []
    for _, row in tab.iterrows():
        rows.append(
            {
                "strategy": strategy,
                "period": "day",
                "date": row["date"].strftime("%Y-%m-%d") if hasattr(row["date"], "strftime") else row["date"],
                "return_pct": round(float(row["return_pct"]), 4),
                "trades": int(row["trades"]),
                "equity": round(float(row["equity"]), 6),
            }
        )
    return rows


def _print_table(title: str, df: pd.DataFrame, cols: list[str]) -> None:
    if df.empty:
        print(f"{title}\n  (no data)\n")
        return
    view = df[cols].copy()
    if "date" in view.columns:
        view["date"] = pd.to_datetime(view["date"]).dt.strftime("%Y-%m-%d")
    print(title)
    print(view.to_string(index=False))
    print()


def publish_results(
    strategy: str,
    returns: pd.Series,
    *,
    already_monthly: bool = False,
    out_dir: Path | None = None,
    last_months: int = 12,
    last_days: int = 21,
    append_to_bundle: bool = False,
) -> tuple[Path | None, Path | None]:
    """Write CSVs and print human summary (monthly always; daily if intraday)."""
    out = out_dir or RESULTS
    out.mkdir(parents=True, exist_ok=True)
    s = slug(strategy)
    monthly_path = out / f"{s}_monthly.csv"
    daily_path: Path | None = None

    mrows = frame_to_monthly_rows(strategy, returns, already_monthly=already_monthly)
    mdf = pd.DataFrame(mrows)
    mdf.to_csv(monthly_path, index=False)

    if append_to_bundle:
        if MONTHLY_OUT.exists():
            old = pd.read_csv(MONTHLY_OUT)
            mdf = pd.concat([old[old.strategy != strategy], mdf], ignore_index=True)
        mdf.to_csv(MONTHLY_OUT, index=False)

    if not already_monthly and len(returns.dropna()) > 0:
        drows = frame_to_daily_rows(strategy, returns)
        daily_path = out / f"{s}_daily.csv"
        ddf = pd.DataFrame(drows)
        ddf.to_csv(daily_path, index=False)
        if append_to_bundle:
            if DAILY_OUT.exists():
                old = pd.read_csv(DAILY_OUT)
                ddf = pd.concat([old[old.strategy != strategy], ddf], ignore_index=True)
            ddf.to_csv(DAILY_OUT, index=False)

    _print_table(
        f"=== {strategy} - last {last_months} months ===",
        mdf.tail(last_months),
        ["date", "return_pct", "trades", "equity", "win_period"],
    )
    if daily_path is not None:
        ddf = pd.read_csv(daily_path)
        _print_table(
            f"=== {strategy} - last {last_days} days ===",
            ddf.tail(last_days),
            ["date", "return_pct", "trades", "equity"],
        )

    tot = mdf["return_pct"].sum() if len(mdf) else 0.0
    win_m = int(mdf["win_period"].sum()) if "win_period" in mdf.columns else 0
    print(f"({strategy}) all months: n={len(mdf)} sum_return%={tot:.2f} win_months={win_m}\n")
    return monthly_path, daily_path


def print_split_monthly(
    strategy: str,
    returns: pd.Series,
    splits: dict[str, tuple[str, str]],
    *,
    already_monthly: bool = False,
) -> None:
    """Print calendar-month P&L subtotals per chronological split."""
    mrows = frame_to_monthly_rows(strategy, returns, already_monthly=already_monthly)
    mdf = pd.DataFrame(mrows)
    mdf["date"] = pd.to_datetime(mdf["date"])
    print(f"=== {strategy} - monthly P&L by split ===")
    for name, (a, b) in splits.items():
        part = mdf[(mdf["date"] >= a) & (mdf["date"] < b)]
        if part.empty:
            print(f"  {name}: (no months)")
            continue
        print(
            f"  {name}: months={len(part)} sum%={part['return_pct'].sum():.2f} "
            f"win={int(part['win_period'].sum())}/{len(part)}"
        )
    print()
