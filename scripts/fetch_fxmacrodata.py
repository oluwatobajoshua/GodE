#!/usr/bin/env python3
"""Fetch real macro/event data from FXMacroData into local CSVs.

The free tier is enough to prototype:
  * USD release calendar for the most recent ~90 days
  * USD announcements for the most recent ~90 days
  * global risk-on/risk-off composite (daily)
  * the USD data catalogue

A key in ``FXMACRODATA_API_KEY`` unlocks full history and the other 21
currencies.  The output is intentionally raw-ish so the feature builder can
decide how to merge it causally.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research.fxmacrodata import (  # noqa: E402
    FXMacroData,
    FXMacroDataError,
    calendar_for_pair,
)

OUT_DIR = ROOT / "research" / "data" / "fxmacrodata"

DEFAULT_INDICATORS = [
    "inflation",
    "core_inflation",
    "non_farm_payrolls",
    "unemployment",
    "policy_rate",
    "core_pce",
    "pce",
    "gdp",
    "retail_sales",
]


def save_meta(path: Path, attrs: dict) -> None:
    path.write_text(json.dumps(attrs, indent=2, default=str), encoding="utf-8")


def report_frame(label: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        print(f"[WARN] {label}: empty")
        return
    if "event_time" in frame:
        start = frame["event_time"].min()
        end = frame["event_time"].max()
        print(f"[OK] {label}: {len(frame):,} rows  {start} -> {end}")
        if frame.attrs.get("history_truncated"):
            print(
                "     free-tier window starts "
                f"{frame.attrs.get('history_start_date')} "
                f"(requested {frame.attrs.get('start_date')})"
            )
        if frame.attrs.get("data_quality", {}).get("point_in_time_safe") is not None:
            print(
                "     point_in_time_safe="
                f"{frame.attrs['data_quality']['point_in_time_safe']}"
            )
    elif "date" in frame:
        print(f"[OK] {label}: {len(frame):,} rows  {frame['date'].min()} -> {frame['date'].max()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--pair", default="", help="e.g. EURUSD; fetches both legs when permitted")
    parser.add_argument("--from-date", default="2026-01-01")
    parser.add_argument("--to-date", default="2026-10-26")
    parser.add_argument("--indicators", nargs="*", default=DEFAULT_INDICATORS)
    parser.add_argument("--skip-announcements", action="store_true")
    parser.add_argument("--force", action="store_true", help="ignore the local JSON cache")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    api = FXMacroData(force_refresh=args.force)
    if not api.api_key:
        print("[INFO] FXMACRODATA_API_KEY not set; using the free USD window.")
    else:
        print("[INFO] FXMACRODATA_API_KEY is set; requesting full history.")

    try:
        cal = api.calendar(args.currency, args.from_date, args.to_date)
        report_frame(f"calendar/{args.currency}", cal)
        if not cal.empty:
            cal.to_csv(OUT_DIR / f"calendar_{args.currency.upper()}.csv", index=False)
            save_meta(OUT_DIR / f"calendar_{args.currency.upper()}_meta.json", cal.attrs)

        if args.pair:
            pair_cal = calendar_for_pair(api, args.pair, args.from_date, args.to_date)
            report_frame(f"calendar/{args.pair.upper()}", pair_cal)
            if not pair_cal.empty:
                pair_cal.to_csv(OUT_DIR / f"calendar_{args.pair.upper()}.csv", index=False)

        risk = api.risk_sentiment(args.from_date, args.to_date)
        report_frame("risk_sentiment", risk)
        if not risk.empty:
            risk.to_csv(OUT_DIR / "risk_sentiment.csv", index=False)
            save_meta(OUT_DIR / "risk_sentiment_meta.json", risk.attrs)
            print(
                "     note: risk_sentiment is derived/backfilled; "
                f"point_in_time_safe={risk.attrs.get('point_in_time_safe')}"
            )

        if not args.skip_announcements:
            for indicator in args.indicators:
                try:
                    frame = api.announcements(
                        args.currency, indicator, args.from_date, args.to_date
                    )
                except FXMacroDataError as exc:
                    print(f"[WARN] announcements/{indicator}: {exc}")
                    continue
                report_frame(f"announcements/{indicator}", frame)
                if not frame.empty:
                    frame.to_csv(
                        OUT_DIR / f"announcements_{args.currency.upper()}_{indicator}.csv",
                        index=False,
                    )
                    save_meta(
                        OUT_DIR / f"announcements_{args.currency.upper()}_{indicator}_meta.json",
                        frame.attrs,
                    )
    except FXMacroDataError as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[DONE] wrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
