#!/usr/bin/env python3
"""H1 trend-following research on EURUSD.

The point of this script is to test a low-frequency trend model where the
spread cost is a small fraction of 1R.  Signals are evaluated on completed H1
bars and filled at the next H1 open.  One position at a time.

Splits:
* design/selection: 2026-01-01 .. 2026-06-30
* validation:       2026-07-01 .. 2026-10-26
* out-of-sample:    2025-01-01 .. 2025-12-31

Run:
    .\\.venv\\Scripts\\python.exe research\\h1_trend.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
DATA = DATA_DIR / "EURUSD_H1.csv"
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))
DESIGN_YEAR = int(os.environ.get("SOVEREIGN_DESIGN_YEAR", "2026"))
DESIGN_LABEL = f"design_{DESIGN_YEAR}H1"
VAL_LABEL = f"validation_{DESIGN_YEAR}H2"
POINT = 0.00001
COST_PTS = 12.0


@dataclass(frozen=True)
class Config:
    name: str
    lookback: int = 20
    sl_atr: float = 2.0
    tp_r: float = 3.0
    trail_atr: float | None = None
    trail_start_r: float = 1.0
    trend_filter: bool = True
    exit_opposite: bool = False


CONFIGS = [
    Config("don20_sl20_tp3", lookback=20, sl_atr=2.0, tp_r=3.0),
    Config("don40_sl20_tp4", lookback=40, sl_atr=2.0, tp_r=4.0),
    Config("don55_sl25_tp6", lookback=55, sl_atr=2.5, tp_r=6.0),
    Config("don20_sl20_trail20", lookback=20, sl_atr=2.0, tp_r=0.0, trail_atr=2.0),
    Config("don40_sl20_trail30", lookback=40, sl_atr=2.0, tp_r=0.0, trail_atr=3.0),
    Config("don20_sl15_tp3", lookback=20, sl_atr=1.5, tp_r=3.0),
    Config("don100_sl25_tp4", lookback=100, sl_atr=2.5, tp_r=4.0),
    Config("don40_sl20_tp4_notrend", lookback=40, sl_atr=2.0, tp_r=4.0, trend_filter=False),
    Config("don20_sl20_tp3_reverse", lookback=20, sl_atr=2.0, tp_r=3.0, exit_opposite=True),
]


def load_data(path: Path = DATA) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"]).sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    tr = np.maximum(
        df["high"] - df["low"],
        np.maximum((df["high"] - df["close"].shift()).abs(), (df["low"] - df["close"].shift()).abs()),
    )
    df["atr"] = tr.rolling(14).mean()
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()
    df["sma200"] = df["close"].rolling(200).mean()
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["roll_high"] = df["high"].rolling(200).max().shift(1)
    df["roll_low"] = df["low"].rolling(200).min().shift(1)
    return df


def simulate(df: pd.DataFrame, cfg: Config, point: float = POINT) -> pd.DataFrame:
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    atr = df["atr"].to_numpy()
    sma200 = df["sma200"].to_numpy()
    ch_high = df["high"].rolling(cfg.lookback).max().shift(1).to_numpy()
    ch_low = df["low"].rolling(cfg.lookback).min().shift(1).to_numpy()

    n = len(df)
    trades = []
    i = max(250, cfg.lookback + 5)
    while i < n - 1:
        long_sig = close[i] > ch_high[i]
        short_sig = close[i] < ch_low[i]
        if cfg.trend_filter:
            long_sig = long_sig and close[i] > sma200[i]
            short_sig = short_sig and close[i] < sma200[i]
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
                fav = (high[k] - entry) / sl_dist
                adv = (entry - low[k]) / sl_dist
            else:
                fav = (entry - low[k]) / sl_dist
                adv = (high[k] - entry) / sl_dist
            mfe = max(mfe, fav)
            mae = min(mae, adv)

            # Stop first, then target, conservative.
            if side > 0:
                if low[k] <= sl:
                    exit_i, exit_px, reason = k, sl, "sl"
                    break
                if cfg.tp_r > 0 and high[k] >= tp:
                    exit_i, exit_px, reason = k, tp, "tp"
                    break
            else:
                if high[k] >= sl:
                    exit_i, exit_px, reason = k, sl, "sl"
                    break
                if cfg.tp_r > 0 and low[k] <= tp:
                    exit_i, exit_px, reason = k, tp, "tp"
                    break

            cur_r = (close[k] - entry) * side / sl_dist
            if cfg.trail_atr is not None and cur_r >= cfg.trail_start_r:
                if side > 0:
                    sl = max(sl, close[k] - cfg.trail_atr * atr[k])
                else:
                    sl = min(sl, close[k] + cfg.trail_atr * atr[k])

            if cfg.exit_opposite and k > entry_i:
                opp_long = close[k] > ch_high[k]
                opp_short = close[k] < ch_low[k]
                if cfg.trend_filter:
                    opp_long = opp_long and close[k] > sma200[k]
                    opp_short = opp_short and close[k] < sma200[k]
                if (side > 0 and opp_short) or (side < 0 and opp_long):
                    exit_i, exit_px, reason = k, close[k], "opposite"
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


def metrics(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0, "win": 0.0, "avg": 0.0, "total": 0.0, "pf": 0.0, "dd": 0.0}
    wins = d["r_multiple"] > 0
    gp = float(d.loc[wins, "r_multiple"].sum())
    gl = float(-d.loc[~wins, "r_multiple"].sum())
    eq = d["r_multiple"].cumsum()
    dd = float((eq.cummax() - eq).max()) if len(eq) else 0.0
    return {
        "n": int(len(d)),
        "win": round(float(wins.mean() * 100), 1),
        "avg": round(float(d["r_multiple"].mean()), 3),
        "total": round(float(d["r_multiple"].sum()), 2),
        "pf": round(gp / gl, 2) if gl > 0 else 999.0,
        "dd": round(dd, 2),
    }


def main() -> int:
    df = load_data()

    rows = []
    all_trades = []
    for cfg in CONFIGS:
        trades = simulate(df, cfg)
        if trades.empty:
            continue
        trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
        tr_design = trades[
            (trades["entry_time"] >= f"{DESIGN_YEAR}-01-01")
            & (trades["entry_time"] < f"{DESIGN_YEAR}-07-01")
        ]
        tr_val = trades[
            (trades["entry_time"] >= f"{DESIGN_YEAR}-07-01")
            & (trades["entry_time"] < f"{DESIGN_YEAR + 1}-01-01")
        ]
        tr_oos = trades[
            (trades["entry_time"] >= "2025-01-01")
            & (trades["entry_time"] < "2026-01-01")
        ]
        for split, tr in [(DESIGN_LABEL, tr_design), (VAL_LABEL, tr_val), ("OOS_2025", tr_oos)]:
            m = metrics(tr)
            rows.append({"config": cfg.name, "split": split, **m})
        all_trades.append(trades)

    summary = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "h1_trend_summary.csv", index=False)
    pd.concat(all_trades, ignore_index=True).to_csv(OUT / "h1_trend_trades.csv", index=False)

    print("=== H1 trend configs by split ===")
    print(summary.to_string(index=False))
    print("\n=== design + validation positive ===")
    piv = summary.pivot(index="config", columns="split", values="total")
    good = piv[(piv[DESIGN_LABEL] > 0) & (piv[VAL_LABEL] > 0)]
    print(good.round(2).to_string())
    print("\n=== same configs, OOS 2025 ===")
    print(piv.loc[good.index, "OOS_2025"].round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
