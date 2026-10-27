#!/usr/bin/env python3
"""Generate labelled trade candidates for meta-label training.

Each candidate mirrors one of the EA's two primary engines:
  * mode 0: mean-reversion limit at an outer range extreme
  * mode 1: Donchian breakout on a completed bar

The label is whether the trade was profitable after spread. Features are
captured at signal time only, so the model cannot peek at the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core import POINT

FEATURE_NAMES = [
    "side",
    "mode",
    "h1_trend",
    "h1_adx_scaled",
    "h1_atr_scaled",
    "h1_dist_sma200_atr",
    "h1_di_diff_scaled",
    "m5_atr_scaled",
    "atr_ratio",
    "rsi2_scaled",
    "range_pos",
    "body_atr",
    "ret1_atr",
    "mom6_atr",
    "dist_ema20_atr",
    "dist_range_high_atr",
    "dist_range_low_atr",
    "range_width_atr",
    "atr_pct",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "spread_scaled",
    "d1_dist_sma200_atr",
]


@dataclass
class CandidateConfig:
    lookback: int = 50
    extreme: float = 0.20
    entry_buffer_atr: float = 0.35
    expiry_bars: int = 6
    sl_atr: float = 1.5
    tp_r: float = 3.0
    horizon_bars: int = 288
    spread: float = 10.0
    slippage: float = 1.0
    htf_adx_min: float = 0.0


def _trend(a: dict[str, np.ndarray], i: int) -> int:
    vals = [a["h1_close"][i], a["h1_sma20"][i], a["h1_sma50"][i], a["h1_sma200"][i]]
    if any(np.isnan(v) for v in vals):
        return 0
    close, sma20, sma50, sma200 = vals
    if close > sma20 > sma50 and close > sma200:
        return 1
    if close < sma20 < sma50 and close < sma200:
        return -1
    return 0


def _simulate(
    a: dict[str, np.ndarray],
    entry_i: int,
    entry: float,
    side: int,
    sl_dist_pts: float,
    tp_dist_pts: float,
    horizon: int,
    cost_pts: float,
) -> tuple[int, int, float, float, str]:
    """Return (win, exit_i, exit_price, r_multiple, reason)."""
    n = len(a["close"])
    sl = entry - side * sl_dist_pts * POINT
    tp = entry + side * tp_dist_pts * POINT
    end = min(entry_i + horizon, n - 1)
    for k in range(entry_i, end + 1):
        if side > 0:
            if a["low"][k] <= sl:
                r = -(sl_dist_pts + cost_pts) / sl_dist_pts
                return 0, k, sl, r, "sl"
            if a["high"][k] >= tp:
                r = (tp_dist_pts - cost_pts) / sl_dist_pts
                return 1, k, tp, r, "tp"
        else:
            if a["high"][k] >= sl:
                r = -(sl_dist_pts + cost_pts) / sl_dist_pts
                return 0, k, sl, r, "sl"
            if a["low"][k] <= tp:
                r = (tp_dist_pts - cost_pts) / sl_dist_pts
                return 1, k, tp, r, "tp"
    exit_price = a["close"][end]
    pnl_pts = (exit_price - entry) * side / POINT - cost_pts
    return int(pnl_pts > 0), end, exit_price, pnl_pts / sl_dist_pts, "time"


def generate(df: pd.DataFrame, cfg: CandidateConfig) -> pd.DataFrame:
    a = {
        c: df[c].to_numpy()
        for c in [
            "open",
            "high",
            "low",
            "close",
            "atr",
            "atr_ratio",
            "atr_pct",
            "ema20",
            "rsi2",
            "body_atr",
            "ret1_atr",
            "mom6_atr",
            "h1_close",
            "h1_sma20",
            "h1_sma50",
            "h1_sma200",
            "h1_adx",
            "h1_atr",
            "h1_plus_di",
            "h1_minus_di",
            "d1_close",
            "d1_sma200",
            "spread",
            "hour",
            "weekday",
        ]
    }
    times = df["time"].to_numpy()
    n = len(df)
    warmup = max(cfg.lookback + 12, 250)
    cost_pts = cfg.spread + cfg.slippage
    rows: list[dict] = []

    for i in range(warmup, n - 2):
        if a["weekday"][i] >= 5:
            continue
        atr = a["atr"][i]
        if np.isnan(atr) or atr <= 0:
            continue
        trend = _trend(a, i)
        if trend == 0:
            continue
        if cfg.htf_adx_min > 0 and (np.isnan(a["h1_adx"][i]) or a["h1_adx"][i] < cfg.htf_adx_min):
            continue

        # Mean-reversion range includes the signal bar, matching the EA.
        hh_mr = float(np.max(a["high"][i - cfg.lookback + 1 : i + 1]))
        ll_mr = float(np.min(a["low"][i - cfg.lookback + 1 : i + 1]))
        width = hh_mr - ll_mr
        if width <= 0:
            continue
        rng_pos = (a["close"][i] - ll_mr) / width

        # Breakout range excludes the signal bar, matching ExecuteBreakoutEngine.
        hh_bo = float(np.max(a["high"][i - cfg.lookback : i]))
        ll_bo = float(np.min(a["low"][i - cfg.lookback : i]))

        candidates: list[tuple[int, int, str]] = []
        # mode 0 = mean reversion limit
        if a["close"][i] <= ll_mr + width * cfg.extreme and trend == 1:
            candidates.append((0, 1, "mr"))
        elif a["close"][i] >= hh_mr - width * cfg.extreme and trend == -1:
            candidates.append((0, -1, "mr"))
        # mode 1 = breakout
        bo_buf = atr * 0.05
        if a["close"][i] > hh_bo + bo_buf and trend == 1:
            candidates.append((1, 1, "bo"))
        elif a["close"][i] < ll_bo - bo_buf and trend == -1:
            candidates.append((1, -1, "bo"))

        for mode, side, kind in candidates:
            entry_i = i + 1
            if kind == "mr":
                buf = atr * cfg.entry_buffer_atr
                limit = a["close"][i] - side * buf
                fill_i = None
                fill_price = None
                for j in range(i + 1, min(i + 1 + cfg.expiry_bars, n)):
                    if side > 0 and a["low"][j] <= limit:
                        fill_i = j
                        fill_price = min(a["open"][j], limit)
                        break
                    if side < 0 and a["high"][j] >= limit:
                        fill_i = j
                        fill_price = max(a["open"][j], limit)
                        break
                if fill_i is None:
                    continue
                entry_i = fill_i
                entry = fill_price
            else:
                if entry_i >= n:
                    continue
                entry = a["open"][entry_i]

            sl_dist = cfg.sl_atr * atr / POINT
            tp_dist = sl_dist * cfg.tp_r
            win, exit_i, exit_price, r_multiple, reason = _simulate(
                a, entry_i, entry, side, sl_dist, tp_dist, cfg.horizon_bars, cost_pts
            )

            h1_atr = a["h1_atr"][i]
            d1_dist = (a["d1_close"][i] - a["d1_sma200"][i]) / h1_atr if h1_atr > 0 else 0.0
            hour = int(a["hour"][i])
            weekday = int(a["weekday"][i])
            spread = a["spread"][i]
            f = {
                "side": float(side),
                "mode": float(mode),
                "h1_trend": float(trend),
                "h1_adx_scaled": a["h1_adx"][i] / 50.0 if not np.isnan(a["h1_adx"][i]) else 0.0,
                "h1_atr_scaled": h1_atr / POINT / 100.0 if not np.isnan(h1_atr) else 0.0,
                "h1_dist_sma200_atr": (a["h1_close"][i] - a["h1_sma200"][i]) / h1_atr if h1_atr > 0 else 0.0,
                "h1_di_diff_scaled": (a["h1_plus_di"][i] - a["h1_minus_di"][i]) / 50.0,
                "m5_atr_scaled": atr / POINT / 100.0,
                "atr_ratio": a["atr_ratio"][i],
                "rsi2_scaled": a["rsi2"][i] / 100.0,
                "range_pos": rng_pos,
                "body_atr": a["body_atr"][i],
                "ret1_atr": a["ret1_atr"][i],
                "mom6_atr": a["mom6_atr"][i],
                "dist_ema20_atr": (a["close"][i] - a["ema20"][i]) / atr,
                "dist_range_high_atr": (hh_mr - a["close"][i]) / atr,
                "dist_range_low_atr": (a["close"][i] - ll_mr) / atr,
                "range_width_atr": width / atr,
                "atr_pct": a["atr_pct"][i],
                "hour_sin": np.sin(2 * np.pi * hour / 24.0),
                "hour_cos": np.cos(2 * np.pi * hour / 24.0),
                "weekday_sin": np.sin(2 * np.pi * weekday / 7.0),
                "weekday_cos": np.cos(2 * np.pi * weekday / 7.0),
                "spread_scaled": spread / 10.0,
                "d1_dist_sma200_atr": d1_dist,
            }
            if any(np.isnan(v) for v in f.values()):
                continue
            rows.append(
                {
                    "signal_time": pd.Timestamp(times[i]),
                    "entry_time": pd.Timestamp(times[entry_i]),
                    "exit_time": pd.Timestamp(times[exit_i]),
                    "kind": kind,
                    "side": side,
                    "mode": mode,
                    "label": int(r_multiple > 0),
                    "win_tp": int(win),
                    "r_multiple": r_multiple,
                    "reason": reason,
                    "entry": entry,
                    "exit": exit_price,
                    "sl_dist_pts": sl_dist,
                    "atr_pts": atr / POINT,
                    **f,
                }
            )
    return pd.DataFrame(rows)


def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    return df[FEATURE_NAMES].to_numpy(dtype=np.float64)
