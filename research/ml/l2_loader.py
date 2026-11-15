#!/usr/bin/env python3
"""Load MT5-exported L2 tick CSV into multilevel book arrays for OFI."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def l2_tick_path(data_dir: Path, symbol: str) -> Path:
    return data_dir / "l2" / f"{symbol.upper()}_l2_ticks.csv"


def has_l2_ticks(data_dir: Path, symbol: str) -> bool:
    p = l2_tick_path(data_dir, symbol)
    return p.exists() and p.stat().st_size > 100


def load_ticks(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.sort_values("time_msc" if "time_msc" in df.columns else "time").drop_duplicates(
        subset=["time_msc"] if "time_msc" in df.columns else ["time"]
    )
    for c in ("bid", "ask", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["bid", "ask"])


def ticks_to_book_arrays(df: pd.DataFrame, depth: int = 3, pip: float = 1e-4) -> dict[str, np.ndarray]:
    """Top level from tick BBO; deeper levels are ladder offsets (research default)."""
    bid1 = df["bid"].to_numpy(dtype=np.float64)
    ask1 = df["ask"].to_numpy(dtype=np.float64)
    vol = df["volume"].fillna(0).to_numpy(dtype=np.float64) if "volume" in df.columns else np.ones(len(df))
    share = np.array([0.55, 0.30, 0.15][:depth], dtype=np.float64)

    bid_p = np.column_stack([bid1 - k * pip for k in range(depth)])
    ask_p = np.column_stack([ask1 + k * pip for k in range(depth)])
    bid_v = np.outer(np.maximum(vol, 1.0), share)
    ask_v = np.outer(np.maximum(vol, 1.0), share)
    mid = (bid1 + ask1) * 0.5
    return {"bid_p": bid_p, "bid_v": bid_v, "ask_p": ask_p, "ask_v": ask_v, "mid": mid, "source": "mt5_ticks"}


def resample_ticks_1s(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse tick firehose to 1s last BBO for ODE training (still async dt)."""
    d = df.set_index(pd.to_datetime(df["time"], utc=True))
    out = d.resample("1s").agg({"bid": "last", "ask": "last", "volume": "sum"})
    out = out.dropna(subset=["bid", "ask"]).reset_index(names="time")
    out["volume"] = out["volume"].fillna(0)
    return out


def tick_dt_seconds(df: pd.DataFrame) -> np.ndarray:
    if "time_msc" in df.columns:
        t = df["time_msc"].to_numpy(dtype=np.float64) / 1000.0
    else:
        t = pd.to_datetime(df["time"], utc=True).astype(np.int64) / 1e9
    dt = np.ones(len(t), dtype=np.float64)
    if len(t) > 1:
        dt[1:] = np.maximum(np.diff(t), 1e-6)
    return dt
