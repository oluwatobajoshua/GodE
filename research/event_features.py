#!/usr/bin/env python3
"""Build causal macro/event features for the M5 candidate set.

The release calendar is the part that is genuinely point-in-time safe: the
schedule is known before the event.  We therefore build the event features from
scheduled ``event_time`` values, not from actual/forecast values, and we never
look at a future release when describing a candidate.

Risk sentiment is also merged, but it is explicitly marked non-PIT-safe by the
provider.  It is useful as live context and as an exploratory feature, not as a
clean backtest input.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODEL = HERE / "models"
DATA = HERE / "data" / "fxmacrodata"
RESULTS = HERE / "results"

EVENT_FLAGS = {
    "is_cpi": r"(?:^|_)inflation(?:$|_)",
    "is_nfp": r"non_farm_payrolls|(?:^|_)employment(?:$|_)",
    "is_unemployment": r"(?:^|_)unemployment(?:$|_)",
    "is_fomc": r"policy_rate(?:$|_)",
    "is_pce": r"(?:^|_)pce(?:$|_)",
    "is_gdp": r"(?:^|_)gdp(?:$|_)",
    "is_retail": r"retail_sales(?:$|_)",
    "is_ppi": r"(?:^|_)ppi(?:$|_)",
    "is_claims": r"jobless_claims",
    "is_confidence": r"consumer_confidence|business_confidence",
}


def _join_unique(values: pd.Series) -> str:
    clean = [str(v) for v in values.dropna().unique() if str(v).strip()]
    return ",".join(sorted(clean))


def _to_utc(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, utc=True, errors="coerce")


def load_calendar(path: Path = DATA / "calendar_USD.csv") -> pd.DataFrame:
    cal = pd.read_csv(path)
    cal["event_time"] = _to_utc(cal["event_time"])
    cal["event_date"] = cal["event_time"].dt.date
    cal["importance"] = cal["importance"].fillna("unknown").astype(str).str.lower()
    cal["release"] = cal["release"].fillna("").astype(str)
    cal["has_time"] = cal["event_time"].notna()
    return cal


def load_risk(path: Path = DATA / "risk_sentiment.csv") -> pd.DataFrame:
    risk = pd.read_csv(path)
    risk["date"] = pd.to_datetime(risk["date"], errors="coerce").dt.date
    risk["risk_score"] = pd.to_numeric(risk["risk_score"], errors="coerce")
    risk = risk.dropna(subset=["date", "risk_score"]).sort_values("date").reset_index(drop=True)
    risk["risk_change_5d"] = risk["risk_score"].diff(5)
    risk["risk_z_20d"] = (
        risk["risk_score"] - risk["risk_score"].rolling(20, min_periods=10).mean()
    ) / risk["risk_score"].rolling(20, min_periods=10).std()
    risk["available_at"] = pd.to_datetime(risk["date"], utc=True) + pd.Timedelta(days=1)
    return risk


def build_event_features(
    candidates: pd.DataFrame,
    calendar: pd.DataFrame,
    risk: pd.DataFrame | None = None,
) -> pd.DataFrame:
    c = candidates.copy()
    c["signal_time"] = _to_utc(c["signal_time"])
    c = c.sort_values("signal_time").reset_index(drop=True)
    c["event_date"] = c["signal_time"].dt.date

    hi = calendar[
        (calendar["importance"] == "high") & calendar["has_time"].fillna(False)
    ].copy()
    hi = hi.dropna(subset=["event_time"]).sort_values("event_time")

    # One row per release timestamp: NFP + unemployment + employment all arrive
    # at 12:30, so they are one event cluster for risk purposes.
    if hi.empty:
        return c

    for flag, pattern in EVENT_FLAGS.items():
        hi[flag] = hi["release"].str.contains(pattern, regex=True, na=False)

    grouped = hi.groupby("event_time", as_index=False).agg(
        next_hi_name=("name", _join_unique),
        next_hi_release=("release", _join_unique),
        next_hi_tier=("market_tier", "min"),
    )
    for flag in EVENT_FLAGS:
        grouped[flag] = (
            hi.groupby("event_time")[flag].max().reindex(grouped["event_time"]).to_numpy()
        )

    # Nearest scheduled high-impact event at/after the signal.
    nxt = pd.merge_asof(
        c[["signal_time"]],
        grouped.sort_values("event_time"),
        left_on="signal_time",
        right_on="event_time",
        direction="forward",
        allow_exact_matches=True,
    )
    nxt = nxt.rename(
        columns={
            "event_time": "next_hi_time",
            "next_hi_name": "next_hi_name",
            "next_hi_release": "next_hi_release",
            "next_hi_tier": "next_hi_tier",
        }
    )
    prefix = {flag: f"next_{flag}" for flag in EVENT_FLAGS}
    nxt = nxt.rename(columns=prefix)
    c = pd.concat(
        [c, nxt.drop(columns=["signal_time"]).reset_index(drop=True)], axis=1
    )

    # Nearest scheduled high-impact event strictly before the signal.
    prv = pd.merge_asof(
        c[["signal_time"]],
        grouped.sort_values("event_time"),
        left_on="signal_time",
        right_on="event_time",
        direction="backward",
        allow_exact_matches=False,
    )
    prv = prv.rename(
        columns={
            "event_time": "prev_hi_time",
            "next_hi_name": "prev_hi_name",
            "next_hi_release": "prev_hi_release",
            "next_hi_tier": "prev_hi_tier",
        }
    )
    prefix = {flag: f"prev_{flag}" for flag in EVENT_FLAGS}
    prv = prv.rename(columns=prefix)
    c = pd.concat(
        [c, prv.drop(columns=["signal_time"]).reset_index(drop=True)], axis=1
    )

    c["next_hi_minutes"] = (
        c["next_hi_time"] - c["signal_time"]
    ).dt.total_seconds() / 60.0
    c["prev_hi_minutes"] = (
        c["signal_time"] - c["prev_hi_time"]
    ).dt.total_seconds() / 60.0

    for minutes in (60, 120, 240):
        c[f"hi_pre_{minutes}m"] = (
            c["prev_hi_minutes"].notna() & (c["prev_hi_minutes"] <= minutes)
        )
        c[f"hi_post_{minutes}m"] = (
            c["next_hi_minutes"].notna() & (c["next_hi_minutes"] <= minutes)
        )

    c["hi_event_phase"] = np.select(
        [
            c["hi_post_60m"],
            c["hi_pre_60m"],
            c["hi_post_240m"],
            c["hi_pre_240m"],
        ],
        ["pre_1h", "post_1h", "pre_4h", "post_4h"],
        default="none",
    )

    # Same-day aggregates.  These answer "is today a CPI/NFP/FOMC day?" which is
    # more relevant to a London-open trade than minute-level proximity.
    day = hi.groupby("event_date", as_index=False).agg(
        hi_count=("release", "size"),
        hi_names=("name", _join_unique),
        hi_releases=("release", _join_unique),
    )
    for flag in EVENT_FLAGS:
        day[f"{flag}_day"] = (
            hi.groupby("event_date")[flag].max().reindex(day["event_date"]).to_numpy()
        )
    day["any_high_impact"] = True
    c = c.merge(day, on="event_date", how="left")
    c["hi_count"] = c["hi_count"].fillna(0).astype(int)
    c["any_high_impact"] = c["any_high_impact"].fillna(False).astype(bool)
    for flag in EVENT_FLAGS:
        c[f"{flag}_day"] = c[f"{flag}_day"].fillna(False).astype(bool)

    # Medium-impact count is useful for a softer risk score, but high impact is
    # the default veto/caution input.
    med = (
        calendar[calendar["importance"] == "medium"]
        .dropna(subset=["event_time"])
        .groupby("event_date")
        .size()
    )
    c["med_count"] = c["event_date"].map(med).fillna(0).astype(int)

    event_dates = calendar["event_date"].dropna()
    if not event_dates.empty:
        c["calendar_covered"] = c["event_date"].between(
            event_dates.min(), event_dates.max()
        )
    else:
        c["calendar_covered"] = False

    if risk is not None and not risk.empty:
        r = risk.copy()
        r = r.sort_values("available_at")
        cols = [
            "available_at",
            "risk_score",
            "risk_regime",
            "risk_change_5d",
            "risk_z_20d",
        ]
        r = r[cols].dropna(subset=["available_at"])
        merged = pd.merge_asof(
            c.sort_values("signal_time"),
            r,
            left_on="signal_time",
            right_on="available_at",
            direction="backward",
        )
        merged = merged.rename(
            columns={
                "risk_score": "risk_score_prev",
                "risk_regime": "risk_regime_prev",
                "risk_change_5d": "risk_change_5d_prev",
                "risk_z_20d": "risk_z_20d_prev",
            }
        )
        c = merged.drop(columns=["available_at"])

    return c


def main() -> int:
    cand_path = MODEL / "candidates_M5.csv"
    if not cand_path.exists():
        print(f"[FAIL] missing {cand_path}")
        return 1
    if not (DATA / "calendar_USD.csv").exists():
        print("[FAIL] run scripts/fetch_fxmacrodata.py first")
        return 1

    cands = pd.read_csv(cand_path)
    cal = load_calendar()
    risk = load_risk() if (DATA / "risk_sentiment.csv").exists() else None
    out = build_event_features(cands, cal, risk)
    out_path = MODEL / "candidates_events_M5.csv"
    out.to_csv(out_path, index=False)

    print(f"[OK] wrote {out_path}  rows={len(out):,}  cols={len(out.columns)}")
    print(
        "     high-impact days in calendar: "
        f"{cal.loc[cal.importance == 'high', 'event_date'].nunique()}"
    )
    print(
        "     candidate rows with scheduled high-impact event: "
        f"{int(out['next_hi_time'].notna().sum()):,}"
    )
    print(
        "     candidates on high-impact days: "
        f"{int(out['any_high_impact'].sum()):,}"
    )
    print(
        "     candidates inside calendar coverage: "
        f"{int(out['calendar_covered'].sum()):,}"
    )
    for col in (
        "is_cpi_day",
        "is_nfp_day",
        "is_fomc_day",
        "is_pce_day",
        "is_gdp_day",
        "is_retail_day",
    ):
        if col in out:
            print(f"     {col}: {int(out[col].sum()):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
