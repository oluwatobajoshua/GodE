#!/usr/bin/env python3
"""Build macro / sentiment features from cross-asset prices.

The inputs are real MT5 bars, not synthetic values:

* DXYUSD  - US dollar index
* VIXUSD  - equity volatility / risk sentiment
* XAUUSD  - gold, a real-yield and safe-haven proxy
* majors  - EUR, GBP, JPY, AUD, CAD, CHF, NZD versus USD

All features at bar ``t`` use only bars that are complete by the close of
``t``.  The EA enters on the next bar, so this is causal.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "research" / "data"
OUT = ROOT / "research" / "models" / "cross_asset_M5.csv"

PAIRS = {
    "EURUSD": 1.0,
    "GBPUSD": 1.0,
    "AUDUSD": 1.0,
    "NZDUSD": 1.0,
    "USDJPY": -1.0,
    "USDCAD": -1.0,
    "USDCHF": -1.0,
}
EUR_PAIRS = {"EURUSD": 1.0, "EURGBP": 1.0, "EURJPY": 1.0}


def _load(symbol: str) -> pd.DataFrame:
    path = DATA / f"{symbol}_M5.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time")
    return df.drop_duplicates("time").set_index("time")


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def _align(master: pd.DatetimeIndex, df: pd.DataFrame, limit: int = 6) -> pd.DataFrame:
    return df.reindex(master).ffill(limit=limit)


def _logret(s: pd.Series, bars: int) -> pd.Series:
    return np.log(s / s.shift(bars))


def _zscore(s: pd.Series, window: int = 200) -> pd.Series:
    mean = s.rolling(window, min_periods=30).mean()
    std = s.rolling(window, min_periods=30).std()
    return (s - mean) / std.replace(0, np.nan)


def build() -> pd.DataFrame:
    eur = _load("EURUSD")
    master = eur.index

    dxy = _align(master, _load("DXYUSD"))
    vix = _align(master, _load("VIXUSD"))
    gold = _align(master, _load("XAUUSD"))

    out = pd.DataFrame(index=master)

    # --- Dollar index -----------------------------------------------------
    dxy_atr = _atr(dxy)
    dxy_atr_pct = dxy_atr / dxy["close"].replace(0, np.nan)
    for n in (1, 3, 6, 12, 24):
        out[f"dxy_ret{n}"] = _logret(dxy["close"], n)
    out["dxy_ret6_atr"] = out["dxy_ret6"] / dxy_atr_pct.replace(0, np.nan)
    out["dxy_ret12_atr"] = out["dxy_ret12"] / dxy_atr_pct.replace(0, np.nan)
    out["dxy_atr_pct"] = dxy_atr_pct
    out["dxy_sma50"] = dxy["close"].rolling(50, min_periods=20).mean()
    out["dxy_dist_sma50_atr"] = (
        (dxy["close"] - out["dxy_sma50"]) / dxy_atr.replace(0, np.nan)
    )
    out["dxy_slope6_atr"] = (
        (out["dxy_sma50"] - out["dxy_sma50"].shift(6)) / dxy_atr.replace(0, np.nan)
    )

    # --- Volatility / risk sentiment -------------------------------------
    vix_chg1 = vix["close"].pct_change()
    vix_chg6 = vix["close"].pct_change(6)
    out["vix"] = vix["close"]
    out["vix_ret1"] = vix_chg1
    out["vix_ret6"] = vix_chg6
    out["vix_z"] = _zscore(vix["close"], 200)
    out["vix_pct"] = vix["close"].rolling(200, min_periods=50).rank(pct=True)
    out["vix_chg6_z"] = _zscore(vix_chg6, 200)
    out["vix_sma50"] = vix["close"].rolling(50, min_periods=20).mean()
    out["vix_dist_sma50_pct"] = (
        (vix["close"] - out["vix_sma50"]) / out["vix_sma50"].replace(0, np.nan)
    )
    out["risk_off"] = (out["vix_z"] > 0.5).astype(float)

    # --- Gold -------------------------------------------------------------
    gold_atr = _atr(gold)
    gold_atr_pct = gold_atr / gold["close"].replace(0, np.nan)
    for n in (1, 6, 12):
        out[f"gold_ret{n}"] = _logret(gold["close"], n)
    out["gold_ret6_atr"] = out["gold_ret6"] / gold_atr_pct.replace(0, np.nan)
    out["gold_sma50"] = gold["close"].rolling(50, min_periods=20).mean()
    out["gold_dist_sma50_atr"] = (
        (gold["close"] - out["gold_sma50"]) / gold_atr.replace(0, np.nan)
    )

    # --- Currency strength from the majors -------------------------------
    # A positive USD-strength number means the dollar is rising against the
    # basket.  A positive EUR-strength number means the euro is rising.
    usd_components: list[pd.Series] = []
    eur_components: list[pd.Series] = []
    for symbol, sign in PAIRS.items():
        px = _align(master, _load(symbol))["close"]
        usd_components.append(sign * _logret(px, 1))
    for symbol, sign in EUR_PAIRS.items():
        px = _align(master, _load(symbol))["close"]
        eur_components.append(sign * _logret(px, 1))

    usd_1 = pd.concat(usd_components, axis=1).mean(axis=1)
    eur_1 = pd.concat(eur_components, axis=1).mean(axis=1)
    out["usd_strength_1"] = usd_1
    out["eur_strength_1"] = eur_1
    for n in (6, 12, 48):
        out[f"usd_strength_{n}"] = usd_1.rolling(n, min_periods=max(2, n // 2)).sum()
        out[f"eur_strength_{n}"] = eur_1.rolling(n, min_periods=max(2, n // 2)).sum()
    for n in (1, 6, 12, 48):
        out[f"flow_{n}"] = out[f"eur_strength_{n}"] - out[f"usd_strength_{n}"]

    # --- One-bar-lagged versions for a strict causality check ------------
    out["dxy_ret6_lag1"] = out["dxy_ret6"].shift(1)
    out["vix_chg6_lag1"] = out["vix_chg6_z"].shift(1)
    out["flow_12_lag1"] = out["flow_12"].shift(1)
    out["flow_48_lag1"] = out["flow_48"].shift(1)

    return out.replace([np.inf, -np.inf], np.nan).reset_index().rename(columns={"index": "time"})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()
    df = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"rows={len(df)} cols={len(df.columns)} -> {out}")
    print(df.tail(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
