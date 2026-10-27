#!/usr/bin/env python3
"""Traditional price-action features for the Sovereign research pipeline.

Everything here is computed from completed bars only.  The output is a frame
keyed by M5 bar time so it can be merged onto the labelled candidate set.

Included:
  * candle anatomy and classic reversal / continuation patterns
  * prior 50/200-bar highs and lows
  * previous-day high/low/close, pivot, R1, S1
  * 5-day and 20-day highs/lows
  * H1 50-bar highs/lows
  * round-number levels (50 and 100 pip)
  * support/resistance confluence, room to the next opposing level,
    breakouts and false breakouts
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import core  # noqa: E402

POINT = core.POINT
DATA_DIR = ROOT / "research" / "data"


def _binary(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(int)


def add_candle_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    o, h, l, c = out["open"], out["high"], out["low"], out["close"]
    out["bar_range"] = (h - l).replace(0, np.nan)
    out["bar_body"] = (c - o).abs()
    out["bar_body_frac"] = out["bar_body"] / out["bar_range"]
    out["upper_wick"] = h - np.maximum(o, c)
    out["lower_wick"] = np.minimum(o, c) - l
    out["close_pos"] = (c - l) / out["bar_range"]
    out["bull_bar"] = (c > o).astype(int)
    out["bear_bar"] = (c < o).astype(int)
    out["body_atr2"] = out["bar_body"] / out["atr"].replace(0, np.nan)
    out["range_atr"] = out["bar_range"] / out["atr"].replace(0, np.nan)

    prev_o = o.shift(1)
    prev_h = h.shift(1)
    prev_l = l.shift(1)
    prev_c = c.shift(1)
    prev_body = out["bar_body"].shift(1)
    prev_range = out["bar_range"].shift(1)
    prev2_o = o.shift(2)
    prev2_c = c.shift(2)
    prev2_body = out["bar_body"].shift(2)

    # Engulfing
    out["bull_engulf"] = _binary(
        (prev_c < prev_o) & (c > o) & (c >= prev_o) & (o <= prev_c) & (out["bar_body"] > prev_body)
    )
    out["bear_engulf"] = _binary(
        (prev_c > prev_o) & (c < o) & (c <= prev_o) & (o >= prev_c) & (out["bar_body"] > prev_body)
    )

    # Pin bars / hammers / shooting stars
    out["hammer"] = _binary(
        (out["lower_wick"] >= 2.0 * out["bar_body"]) & (out["upper_wick"] <= 0.35 * out["bar_range"]) & (out["close_pos"] >= 0.6)
    )
    out["shooting_star"] = _binary(
        (out["upper_wick"] >= 2.0 * out["bar_body"]) & (out["lower_wick"] <= 0.35 * out["bar_range"]) & (out["close_pos"] <= 0.4)
    )

    # Doji / indecision
    out["doji"] = _binary(out["bar_body_frac"] <= 0.10)
    out["long_legged_doji"] = _binary((out["bar_body_frac"] <= 0.10) & (out["range_atr"] >= 0.8))

    # Inside / outside bars
    out["inside_bar"] = _binary((h < prev_h) & (l > prev_l))
    out["outside_bar"] = _binary((h > prev_h) & (l < prev_l))
    out["outside_bull"] = _binary((out["outside_bar"] == 1) & (c > o))
    out["outside_bear"] = _binary((out["outside_bar"] == 1) & (c < o))

    # Harami
    out["bull_harami"] = _binary(
        (prev_c < prev_o) & (c > o) & (o >= prev_c) & (c <= prev_o) & (out["bar_body"] < prev_body)
    )
    out["bear_harami"] = _binary(
        (prev_c > prev_o) & (c < o) & (o <= prev_c) & (c >= prev_o) & (out["bar_body"] < prev_body)
    )

    # Piercing / dark cloud
    prev_mid = (prev_o + prev_c) / 2.0
    out["piercing_line"] = _binary((prev_c < prev_o) & (c > o) & (o < prev_c) & (c > prev_mid))
    out["dark_cloud"] = _binary((prev_c > prev_o) & (c < o) & (o > prev_c) & (c < prev_mid))

    # Morning / evening star
    out["morning_star"] = _binary(
        (prev2_c < prev2_o)
        & (prev_body < prev2_body * 0.75)
        & (c > o)
        & (c > (prev2_o + prev2_c) / 2.0)
    )
    out["evening_star"] = _binary(
        (prev2_c > prev2_o)
        & (prev_body < prev2_body * 0.75)
        & (c < o)
        & (c < (prev2_o + prev2_c) / 2.0)
    )

    # Tweezers
    out["tweezer_bottom"] = _binary(
        ((l - prev_l).abs() <= 0.10 * out["atr"]) & (c > o)
    )
    out["tweezer_top"] = _binary(
        ((h - prev_h).abs() <= 0.10 * out["atr"]) & (c < o)
    )

    # Strong close / wide range
    out["strong_bull_close"] = _binary((out["close_pos"] >= 0.75) & (out["body_atr2"] >= 0.4))
    out["strong_bear_close"] = _binary((out["close_pos"] <= 0.25) & (out["body_atr2"] >= 0.4))
    out["wide_range"] = _binary(out["range_atr"] >= 1.5)
    out["nr7"] = _binary(out["bar_range"] <= out["bar_range"].rolling(7).min().shift(1))

    # Three soldiers / crows
    out["three_white_soldiers"] = _binary(
        (c > o) & (c.shift(1) > o.shift(1)) & (c.shift(2) > o.shift(2))
        & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
        & (out["body_atr2"] >= 0.3) & (out["body_atr2"].shift(1) >= 0.3) & (out["body_atr2"].shift(2) >= 0.3)
    )
    out["three_black_crows"] = _binary(
        (c < o) & (c.shift(1) < o.shift(1)) & (c.shift(2) < o.shift(2))
        & (c < c.shift(1)) & (c.shift(1) < c.shift(2))
        & (out["body_atr2"] >= 0.3) & (out["body_atr2"].shift(1) >= 0.3) & (out["body_atr2"].shift(2) >= 0.3)
    )

    # Composite reversal scores
    bull_rev = (
        out["bull_engulf"] + out["hammer"] + out["morning_star"] + out["tweezer_bottom"]
        + out["piercing_line"] + out["bull_harami"] + out["long_legged_doji"]
    )
    bear_rev = (
        out["bear_engulf"] + out["shooting_star"] + out["evening_star"] + out["tweezer_top"]
        + out["dark_cloud"] + out["bear_harami"] + out["long_legged_doji"]
    )
    out["bull_reversal_score"] = bull_rev
    out["bear_reversal_score"] = bear_rev
    out["bull_continuation_score"] = (
        out["strong_bull_close"] + out["outside_bull"] + out["three_white_soldiers"]
    )
    out["bear_continuation_score"] = (
        out["strong_bear_close"] + out["outside_bear"] + out["three_black_crows"]
    )
    return out


def add_daily_levels(df: pd.DataFrame) -> pd.DataFrame:
    d1 = pd.read_csv(DATA_DIR / "EURUSD_D1.csv", parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    d1["available"] = d1["time"] + pd.Timedelta(days=1)
    d1["pivot"] = (d1["high"] + d1["low"] + d1["close"]) / 3.0
    d1["r1"] = 2.0 * d1["pivot"] - d1["low"]
    d1["s1"] = 2.0 * d1["pivot"] - d1["high"]
    d1["high5"] = d1["high"].rolling(5).max()
    d1["low5"] = d1["low"].rolling(5).min()
    d1["high20"] = d1["high"].rolling(20).max()
    d1["low20"] = d1["low"].rolling(20).min()
    d1_cols = [
        "available", "high", "low", "close", "pivot", "r1", "s1",
        "high5", "low5", "high20", "low20",
    ]
    daily = d1[d1_cols].rename(
        columns={
            "high": "pdh", "low": "pdl", "close": "pdc",
            "high5": "ph5", "low5": "pl5", "high20": "ph20", "low20": "pl20",
        }
    )
    return pd.merge_asof(
        df.sort_values("time"),
        daily,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])


def add_h1_levels(df: pd.DataFrame) -> pd.DataFrame:
    h1 = pd.read_csv(DATA_DIR / "EURUSD_H1.csv", parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    h1["h1_high50"] = h1["high"].rolling(50).max()
    h1["h1_low50"] = h1["low"].rolling(50).min()
    h1["available"] = h1["time"] + pd.Timedelta(hours=1)
    h1_cols = h1[["available", "h1_high50", "h1_low50"]]
    return pd.merge_asof(
        df.sort_values("time"),
        h1_cols,
        left_on="time",
        right_on="available",
        direction="backward",
    ).drop(columns=["available"])


def build_ta_features() -> pd.DataFrame:
    df = core.load_data("M5")
    df = add_candle_features(df)
    df = add_daily_levels(df)
    df = add_h1_levels(df)
    df = df.sort_values("time").reset_index(drop=True)

    df["high200"] = df["high"].rolling(200).max().shift(1)
    df["low200"] = df["low"].rolling(200).min().shift(1)
    # Previous completed range levels.
    df["high50_prev"] = df["range_high"]
    df["low50_prev"] = df["range_low"]

    # Round-number levels.
    df["round50"] = (df["close"] / 0.0050).round() * 0.0050
    df["round100"] = (df["close"] / 0.0100).round() * 0.0100
    df["round50_prev"] = (df["close"].shift(1) / 0.0050).round() * 0.0050
    df["round100_prev"] = (df["close"].shift(1) / 0.0100).round() * 0.0100

    atr = df["atr"].replace(0, np.nan)
    tol = 0.35 * atr

    support_cols = ["low50_prev", "low200", "pdl", "s1", "pl5", "h1_low50", "round50", "round100"]
    resistance_cols = ["high50_prev", "high200", "pdh", "r1", "ph5", "h1_high50", "round50", "round100"]

    close = df["close"]
    low = df["low"]
    high = df["high"]

    support_hits = []
    resistance_hits = []
    for col in support_cols:
        level = df[col]
        # Level was tested by this bar or sits immediately below the close.
        support_hits.append(((level <= close + tol) & (level >= low - tol)).fillna(False))
    for col in resistance_cols:
        level = df[col]
        resistance_hits.append(((level >= close - tol) & (level <= high + tol)).fillna(False))
    support_matrix = np.vstack([s.to_numpy() for s in support_hits])
    resistance_matrix = np.vstack([r.to_numpy() for r in resistance_hits])
    df["support_confluence"] = support_matrix.sum(axis=0).astype(int)
    df["resistance_confluence"] = resistance_matrix.sum(axis=0).astype(int)

    # Room to the next opposing level, measured from the close.
    room_res = []
    room_sup = []
    for col in resistance_cols:
        level = df[col]
        room_res.append(np.where(level > close, (level - close) / atr, np.nan))
    for col in support_cols:
        level = df[col]
        room_sup.append(np.where(level < close, (close - level) / atr, np.nan))
    res_mat = np.vstack(room_res)
    sup_mat = np.vstack(room_sup)
    df["room_to_resistance_atr"] = np.nanmin(res_mat, axis=0)
    df["room_to_support_atr"] = np.nanmin(sup_mat, axis=0)
    df["room_to_resistance_atr"] = df["room_to_resistance_atr"].fillna(10.0).clip(upper=10.0)
    df["room_to_support_atr"] = df["room_to_support_atr"].fillna(10.0).clip(upper=10.0)

    # Breakouts and failed breakouts of prior levels.
    prev_close = df["close"].shift(1)
    break_up = []
    break_down = []
    false_up = []
    false_down = []
    for col in ["high50_prev", "high200", "pdh", "ph5", "h1_high50", "round50_prev", "round100_prev"]:
        level = df[col]
        break_up.append(((close > level) & (prev_close <= level)).fillna(False))
        false_up.append(((high > level) & (close < level)).fillna(False))
    for col in ["low50_prev", "low200", "pdl", "pl5", "h1_low50", "round50_prev", "round100_prev"]:
        level = df[col]
        break_down.append(((close < level) & (prev_close >= level)).fillna(False))
        false_down.append(((low < level) & (close > level)).fillna(False))
    df["break_up_count"] = np.vstack([x.to_numpy() for x in break_up]).sum(axis=0).astype(int)
    df["break_down_count"] = np.vstack([x.to_numpy() for x in break_down]).sum(axis=0).astype(int)
    df["false_break_up_count"] = np.vstack([x.to_numpy() for x in false_up]).sum(axis=0).astype(int)
    df["false_break_down_count"] = np.vstack([x.to_numpy() for x in false_down]).sum(axis=0).astype(int)

    # Compact directional scores.  These are deliberately simple so they can be
    # reproduced in MQL5 without a library.
    df["bull_ta_score"] = (
        df["bull_reversal_score"]
        + df["bull_continuation_score"]
        + (df["support_confluence"] > 0).astype(int)
        + (df["break_up_count"] > 0).astype(int)
        + (df["room_to_resistance_atr"] >= 2.0).astype(int)
        - (df["resistance_confluence"] > 0).astype(int)
        - (df["false_break_up_count"] > 0).astype(int)
    )
    df["bear_ta_score"] = (
        df["bear_reversal_score"]
        + df["bear_continuation_score"]
        + (df["resistance_confluence"] > 0).astype(int)
        + (df["break_down_count"] > 0).astype(int)
        + (df["room_to_support_atr"] >= 2.0).astype(int)
        - (df["support_confluence"] > 0).astype(int)
        - (df["false_break_down_count"] > 0).astype(int)
    )

    keep = [
        "time", "atr",
        "bar_range", "bar_body", "bar_body_frac", "upper_wick", "lower_wick", "close_pos",
        "bull_bar", "bear_bar", "body_atr2", "range_atr",
        "bull_engulf", "bear_engulf", "hammer", "shooting_star", "doji", "long_legged_doji",
        "inside_bar", "outside_bar", "outside_bull", "outside_bear",
        "bull_harami", "bear_harami", "piercing_line", "dark_cloud",
        "morning_star", "evening_star", "tweezer_bottom", "tweezer_top",
        "strong_bull_close", "strong_bear_close", "wide_range", "nr7",
        "three_white_soldiers", "three_black_crows",
        "bull_reversal_score", "bear_reversal_score", "bull_continuation_score", "bear_continuation_score",
        "pdh", "pdl", "pdc", "pivot", "r1", "s1", "ph5", "pl5", "ph20", "pl20",
        "high50_prev", "low50_prev", "high200", "low200", "h1_high50", "h1_low50",
        "round50", "round100", "round50_prev", "round100_prev",
        "support_confluence", "resistance_confluence",
        "room_to_resistance_atr", "room_to_support_atr",
        "break_up_count", "break_down_count", "false_break_up_count", "false_break_down_count",
        "bull_ta_score", "bear_ta_score",
    ]
    return df[keep].copy()


def main() -> int:
    out = build_ta_features()
    path = HERE / "models" / "ta_features_M5.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    print(f"rows={len(out)} cols={len(out.columns)} -> {path}")
    print(out[["time", "support_confluence", "resistance_confluence", "room_to_resistance_atr", "room_to_support_atr", "bull_ta_score", "bear_ta_score"]].tail(5).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
