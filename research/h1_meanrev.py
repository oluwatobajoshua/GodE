#!/usr/bin/env python3
"""H1 mean-reversion research across the major FX pairs.

The trend tests failed.  This script tests the opposite regime: fade a
stretched H1 move back toward its short-term mean.  Entry is on a completed H1
bar and filled at the next H1 open.  One position per symbol.

Splits:
* design:      2026-01-01 .. 2026-06-30
* validation:  2026-07-01 .. 2026-10-26
* OOS:         2025-01-01 .. 2025-12-31

Run:
    .\\.venv\\Scripts\\python.exe research\\h1_meanrev.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from h1_trend import metrics  # noqa: E402

DATA = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))
DESIGN_YEAR = int(os.environ.get("SOVEREIGN_DESIGN_YEAR", "2026"))
DESIGN_LABEL = f"design_{DESIGN_YEAR}H1"
VAL_LABEL = f"validation_{DESIGN_YEAR}H2"
COST_PTS = 12.0

SYMBOLS = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "AUDUSD": 0.00001,
    "NZDUSD": 0.00001,
    "USDCAD": 0.00001,
    "USDCHF": 0.00001,
    "EURGBP": 0.00001,
    "USDJPY": 0.001,
    "EURJPY": 0.001,
}


@dataclass(frozen=True)
class Config:
    name: str
    z_entry: float = 2.0
    sl_atr: float = 2.0
    tp_r: float = 1.5
    exit_mean: bool = False
    time_bars: int = 48
    trend_mode: str = "none"  # none | with_trend | counter_trend


CONFIGS = [
    Config("z20_sl20_tp15", z_entry=2.0, sl_atr=2.0, tp_r=1.5, time_bars=48),
    Config("z20_sl20_mean", z_entry=2.0, sl_atr=2.0, tp_r=0.0, exit_mean=True, time_bars=48),
    Config("z15_sl20_tp10_trend", z_entry=1.5, sl_atr=2.0, tp_r=1.0, trend_mode="with_trend"),
    Config("z25_sl25_tp20", z_entry=2.5, sl_atr=2.5, tp_r=2.0, time_bars=48),
    Config("z20_sl20_tp15_trend", z_entry=2.0, sl_atr=2.0, tp_r=1.5, trend_mode="with_trend"),
    Config("z20_sl20_mean_trend", z_entry=2.0, sl_atr=2.0, tp_r=0.0, exit_mean=True, trend_mode="with_trend"),
    Config("z20_sl20_tp15_counter", z_entry=2.0, sl_atr=2.0, tp_r=1.5, trend_mode="counter_trend"),
    Config("z15_sl15_tp10", z_entry=1.5, sl_atr=1.5, tp_r=1.0, time_bars=24),
    Config("z25_sl20_tp15", z_entry=2.5, sl_atr=2.0, tp_r=1.5, time_bars=48),
    Config("z20_sl25_tp15", z_entry=2.0, sl_atr=2.5, tp_r=1.5, time_bars=48),
]


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum((df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()),
    )
    df["atr"] = tr.rolling(14).mean()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["std20"] = df["close"].rolling(20).std()
    df["z"] = (df["close"] - df["sma20"]) / df["std20"]
    return df


def simulate(df: pd.DataFrame, cfg: Config, point: float) -> pd.DataFrame:
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    atr = df["atr"].to_numpy()
    sma20 = df["sma20"].to_numpy()
    sma200 = df["sma200"].to_numpy()
    z = df["z"].to_numpy()
    n = len(df)
    trades = []
    i = 250
    while i < n - 1:
        long_sig = z[i] <= -cfg.z_entry
        short_sig = z[i] >= cfg.z_entry
        if cfg.trend_mode == "with_trend":
            long_sig = long_sig and close[i] > sma200[i]
            short_sig = short_sig and close[i] < sma200[i]
        elif cfg.trend_mode == "counter_trend":
            long_sig = long_sig and close[i] < sma200[i]
            short_sig = short_sig and close[i] > sma200[i]
        if not (long_sig or short_sig):
            i += 1
            continue

        side = 1 if long_sig else -1
        entry_i = i + 1
        entry = open_[entry_i]
        sl_dist = cfg.sl_atr * atr[i]
        if not np.isfinite(sl_dist) or sl_dist <= 0:
            i += 1
            continue
        sl = entry - side * sl_dist
        tp = entry + side * cfg.tp_r * sl_dist if cfg.tp_r > 0 else np.nan
        cost_r = COST_PTS / (sl_dist / point)
        exit_i = None
        exit_px = None
        reason = "horizon"
        mfe = 0.0
        mae = 0.0

        for k in range(entry_i, n):
            if side > 0:
                mfe = max(mfe, (high[k] - entry) / sl_dist)
                mae = min(mae, (entry - low[k]) / sl_dist)
                if low[k] <= sl:
                    exit_i, exit_px, reason = k, sl, "sl"
                    break
                if cfg.tp_r > 0 and high[k] >= tp:
                    exit_i, exit_px, reason = k, tp, "tp"
                    break
            else:
                mfe = max(mfe, (entry - low[k]) / sl_dist)
                mae = min(mae, (high[k] - entry) / sl_dist)
                if high[k] >= sl:
                    exit_i, exit_px, reason = k, sl, "sl"
                    break
                if cfg.tp_r > 0 and low[k] <= tp:
                    exit_i, exit_px, reason = k, tp, "tp"
                    break

            if cfg.exit_mean:
                if (side > 0 and close[k] >= sma20[k]) or (side < 0 and close[k] <= sma20[k]):
                    exit_i, exit_px, reason = k, close[k], "mean"
                    break

            if cfg.time_bars > 0 and (k - entry_i) >= cfg.time_bars:
                exit_i, exit_px, reason = k, close[k], "time"
                break

        if exit_i is None:
            exit_i = n - 1
            exit_px = close[exit_i]
            reason = "horizon"

        r = (exit_px - entry) * side / sl_dist - cost_r
        trades.append(
            {
                "signal_time": df["time"].iloc[i],
                "entry_time": df["time"].iloc[entry_i],
                "exit_time": df["time"].iloc[exit_i],
                "side": side,
                "entry": entry,
                "exit": exit_px,
                "sl_dist_pts": sl_dist / point,
                "r_multiple": r,
                "mfe": mfe,
                "mae": mae,
                "bars": exit_i - entry_i,
                "reason": reason,
                "config": cfg.name,
            }
        )
        i = exit_i + 1
    return pd.DataFrame(trades)


def split_trades(trades: pd.DataFrame) -> dict[str, pd.DataFrame]:
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    return {
        DESIGN_LABEL: t[
            (t["entry_time"] >= f"{DESIGN_YEAR}-01-01")
            & (t["entry_time"] < f"{DESIGN_YEAR}-07-01")
        ],
        VAL_LABEL: t[
            (t["entry_time"] >= f"{DESIGN_YEAR}-07-01")
            & (t["entry_time"] < f"{DESIGN_YEAR + 1}-01-01")
        ],
        "OOS_2025": t[
            (t["entry_time"] >= "2025-01-01")
            & (t["entry_time"] < "2026-01-01")
        ],
    }


def main() -> int:
    summaries = []
    per_symbol_rows = []
    all_trades = []
    for cfg in CONFIGS:
        symbol_trades = []
        for symbol, point in SYMBOLS.items():
            path = DATA / f"{symbol}_H1.csv"
            if not path.exists():
                continue
            df = load(path)
            tr = simulate(df, cfg, point)
            if tr.empty:
                continue
            tr["symbol"] = symbol
            symbol_trades.append(tr)
            for split, part in split_trades(tr).items():
                per_symbol_rows.append({"config": cfg.name, "symbol": symbol, "split": split, **metrics(part)})
        if not symbol_trades:
            continue
        all_tr = pd.concat(symbol_trades, ignore_index=True).sort_values("entry_time")
        for split, part in split_trades(all_tr).items():
            summaries.append({"config": cfg.name, "split": split, **metrics(part)})
        all_trades.append(all_tr)

    summary = pd.DataFrame(summaries)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "h1_meanrev_summary.csv", index=False)
    pd.DataFrame(per_symbol_rows).to_csv(OUT / "h1_meanrev_by_symbol.csv", index=False)
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_csv(OUT / "h1_meanrev_trades.csv", index=False)

    print("=== H1 mean reversion portfolio ===")
    print(summary.to_string(index=False))
    piv = summary.pivot(index="config", columns="split", values="total")
    good = piv[(piv[DESIGN_LABEL] > 0) & (piv[VAL_LABEL] > 0)]
    print("\n=== positive in design AND validation ===")
    print(good.round(2).to_string() if len(good) else "none")
    if len(good):
        print("\n=== OOS 2025 ===")
        print(piv.loc[good.index, "OOS_2025"].round(2).to_string())

    if all_trades:
        best = piv[VAL_LABEL].idxmax()
        print(f"\n=== per-symbol total R for best validation config: {best} ===")
        ps = pd.DataFrame(per_symbol_rows)
        ps = ps[ps["config"] == best].pivot(index="symbol", columns="split", values="total")
        print(ps.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
