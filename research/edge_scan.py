#!/usr/bin/env python3
"""Scan the M5 history for repeatable conditional edges.

This is a research tool, not a live strategy. It looks for conditions where
the average move after a signal is larger than the round-trip spread, then
checks whether the same condition held in the untouched July-October sample.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402

RESULTS_DIR = HERE / "results"
TRAIN_END = pd.Timestamp("2026-07-01", tz="UTC")


def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    h1 = df[["h1_close", "h1_sma20", "h1_sma50", "h1_sma200", "h1_adx", "h1_plus_di", "h1_minus_di"]]
    bull = (
        (h1["h1_close"] > h1["h1_sma20"])
        & (h1["h1_sma20"] > h1["h1_sma50"])
        & (h1["h1_close"] > h1["h1_sma200"])
        & (h1["h1_adx"] >= 24)
        & (h1["h1_plus_di"] > h1["h1_minus_di"])
    )
    bear = (
        (h1["h1_close"] < h1["h1_sma20"])
        & (h1["h1_sma20"] < h1["h1_sma50"])
        & (h1["h1_close"] < h1["h1_sma200"])
        & (h1["h1_adx"] >= 24)
        & (h1["h1_minus_di"] > h1["h1_plus_di"])
    )
    df["regime"] = np.select([bull, bear, h1["h1_adx"] <= 18], ["bull", "bear", "range"], default="chop")
    df["session"] = np.select(
        [df["hour"] < 7, df["hour"] < 12, df["hour"] < 17],
        ["asian", "london", "ny"],
        default="late",
    )
    return df


def add_conditions(df: pd.DataFrame) -> dict[str, pd.Series]:
    c: dict[str, pd.Series] = {}
    c["regime=bull"] = df["regime"] == "bull"
    c["regime=bear"] = df["regime"] == "bear"
    c["regime=range"] = df["regime"] == "range"
    c["regime=chop"] = df["regime"] == "chop"
    c["session=asian"] = df["session"] == "asian"
    c["session=london"] = df["session"] == "london"
    c["session=ny"] = df["session"] == "ny"
    c["session=late"] = df["session"] == "late"

    for h in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]:
        c[f"hour={h}"] = df["hour"] == h

    for lo, hi in [(0.0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]:
        c[f"rangepos={lo:.1f}-{hi:.1f}"] = (df["range_pos"] >= lo) & (df["range_pos"] < hi)

    for lo, hi in [(0, 5), (5, 10), (10, 20), (20, 30), (30, 50), (50, 70), (70, 80), (80, 90), (90, 95), (95, 101)]:
        c[f"rsi2={lo}-{hi}"] = (df["rsi2"] >= lo) & (df["rsi2"] < hi)

    for lo, hi in [(0.0, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 2.0), (2.0, 10.0)]:
        c[f"atrratio={lo:.1f}-{hi:.1f}"] = (df["atr_ratio"] >= lo) & (df["atr_ratio"] < hi)

    for lo, hi in [(0, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, 100)]:
        c[f"adx={lo}-{hi}"] = (df["h1_adx"] >= lo) & (df["h1_adx"] < hi)

    for lo, hi in [(-10, -2), (-2, -1), (-1, -0.5), (-0.5, 0), (0, 0.5), (0.5, 1), (1, 2), (2, 10)]:
        c[f"mom6={lo}-{hi}"] = (df["mom6_atr"] >= lo) & (df["mom6_atr"] < hi)

    for lo, hi in [(0.0, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 1.5), (1.5, 10.0)]:
        c[f"body={lo:.1f}-{hi:.1f}"] = (df["body_atr"] >= lo) & (df["body_atr"] < hi)

    for lo, hi in [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]:
        c[f"atrpct={lo:.1f}-{hi:.1f}"] = (df["atr_pct"] >= lo) & (df["atr_pct"] < hi)

    # Composite conditions that resemble candidate entries.
    c["trend_pullback_long"] = (
        (df["regime"] == "bull")
        & (df["low"] <= df["ema20"])
        & (df["high"] >= df["ema20"])
        & (df["close"] > df["ema20"])
        & (df["close"] > df["open"])
    )
    c["trend_pullback_short"] = (
        (df["regime"] == "bear")
        & (df["low"] <= df["ema20"])
        & (df["high"] >= df["ema20"])
        & (df["close"] < df["ema20"])
        & (df["close"] < df["open"])
    )
    c["breakout_long"] = (df["regime"] == "bull") & (df["close"] > df["range_high"])
    c["breakout_short"] = (df["regime"] == "bear") & (df["close"] < df["range_low"])
    c["range_low_long"] = (df["regime"] == "range") & (df["range_pos"] < 0.15) & (df["rsi2"] < 15)
    c["range_high_short"] = (df["regime"] == "range") & (df["range_pos"] > 0.85) & (df["rsi2"] > 85)
    c["strong_body_long"] = (df["regime"] == "bull") & (df["body_atr"] > 1.0) & (df["close"] > df["open"])
    c["strong_body_short"] = (df["regime"] == "bear") & (df["body_atr"] > 1.0) & (df["close"] < df["open"])

    # Cross conditions.
    for regime in ["bull", "bear", "range", "chop"]:
        for session in ["asian", "london", "ny", "late"]:
            c[f"{regime}+{session}"] = (df["regime"] == regime) & (df["session"] == session)
    for regime in ["bull", "bear", "range"]:
        for lo, hi in [(0.0, 0.3), (0.3, 0.7), (0.7, 1.0)]:
            c[f"{regime}+rangepos{lo:.1f}-{hi:.1f}"] = (df["regime"] == regime) & (
                df["range_pos"] >= lo
            ) & (df["range_pos"] < hi)
    return c


def summarize(
    df: pd.DataFrame,
    mask: pd.Series,
    horizon: int,
    train_mask: pd.Series,
) -> dict | None:
    atr_pts = df["atr"] / core.POINT
    cost = 11.0 / atr_pts
    entry = df["open"].shift(-1)
    exit_ = df["close"].shift(-horizon)
    long_ret = (exit_ - entry) / df["atr"]
    short_ret = (entry - exit_) / df["atr"]
    long_edge = long_ret - cost
    short_edge = short_ret - cost

    valid = mask & long_ret.notna() & (atr_pts > 0)
    train = valid & train_mask
    test = valid & ~train_mask
    n_train = int(train.sum())
    n_test = int(test.sum())
    if n_train < 80 or n_test < 20:
        return None

    def stats(series: pd.Series, selected: pd.Series) -> tuple[float, float, float, float]:
        x = series[selected]
        if len(x) == 0:
            return 0.0, 0.0, 0.0, 0.0
        mean = float(x.mean())
        std = float(x.std(ddof=1))
        t = mean / (std / np.sqrt(len(x))) if std > 0 else 0.0
        win = float((x > 0).mean() * 100.0)
        return mean, t, win, float(x.abs().mean())

    ltr, ltt, ltw, lta = stats(long_edge, train)
    lte, ltt2, ltw2, lta2 = stats(long_edge, test)
    str_, stt, stw, sta = stats(short_edge, train)
    ste, stt2, stw2, sta2 = stats(short_edge, test)
    return {
        "horizon": horizon,
        "n_train": n_train,
        "n_test": n_test,
        "long_train_edge": ltr,
        "long_train_t": ltt,
        "long_train_win": ltw,
        "long_test_edge": lte,
        "long_test_t": ltt2,
        "long_test_win": ltw2,
        "short_train_edge": str_,
        "short_train_t": stt,
        "short_train_win": stw,
        "short_test_edge": ste,
        "short_test_t": stt2,
        "short_test_win": stw2,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Conditional edge scan")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--horizons", default="12,24,48,96")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    df = core.load_data(args.timeframe)
    df = add_regime(df)
    conditions = add_conditions(df)
    train_mask = df["time"] < TRAIN_END
    horizons = [int(x) for x in args.horizons.split(",")]

    rows = []
    for name, mask in conditions.items():
        for horizon in horizons:
            row = summarize(df, mask, horizon, train_mask)
            if row is not None:
                row["condition"] = name
                rows.append(row)

    table = pd.DataFrame(rows)
    if table.empty:
        print("No conditions met the sample-size threshold.")
        return 1

    table["long_confirm"] = (table["long_train_edge"] > 0.05) & (table["long_test_edge"] > 0.02)
    table["short_confirm"] = (table["short_train_edge"] > 0.05) & (table["short_test_edge"] > 0.02)
    table["long_score"] = table["long_train_edge"] * 0.5 + table["long_test_edge"] * 0.5
    table["short_score"] = table["short_train_edge"] * 0.5 + table["short_test_edge"] * 0.5

    out = RESULTS_DIR / f"edge_scan_{args.timeframe}.csv"
    table.to_csv(out, index=False)
    print(f"Saved {len(table)} condition/horizon rows to {out}")

    cols = [
        "condition", "horizon", "n_train", "n_test",
        "long_train_edge", "long_test_edge", "long_train_t", "long_test_t",
        "long_train_win", "long_test_win",
        "short_train_edge", "short_test_edge", "short_train_t", "short_test_t",
        "short_train_win", "short_test_win",
    ]
    print("\nBest confirmed LONG conditions:")
    print(
        table[table["long_confirm"]]
        .sort_values("long_score", ascending=False)
        .head(args.top)[cols]
        .to_string(index=False)
    )
    print("\nBest confirmed SHORT conditions:")
    print(
        table[table["short_confirm"]]
        .sort_values("short_score", ascending=False)
        .head(args.top)[cols]
        .to_string(index=False)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
