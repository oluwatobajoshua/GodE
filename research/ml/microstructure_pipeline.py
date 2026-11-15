#!/usr/bin/env python3
"""Microstructure + Neural ODE research pipeline (IT-safe: NumPy only).

Prefers MT5 tick export in research/data/l2/{SYM}_l2_ticks.csv.
Falls back to M5 synthetic book when ticks are missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from hawkes_intensity import events_from_returns, hawkes_intensity_series  # noqa: E402
from l2_loader import (  # noqa: E402
    has_l2_ticks,
    l2_tick_path,
    load_ticks,
    resample_ticks_1s,
    tick_dt_seconds,
    ticks_to_book_arrays,
)
from microstructure_ofi import (  # noqa: E402
    ofi_multilevel,
    synthetic_book_from_bars,
    vpin_proxy,
)
from neural_ode_euler import NeuralOdeEuler, huber_loss  # noqa: E402

DATA = ROOT / "research" / "data"
RESULTS = ROOT / "research" / "results"


def load_m5(symbol: str) -> pd.DataFrame:
    for p in (DATA / f"{symbol}_M5.csv", DATA / "histdata" / f"{symbol}_M5.csv"):
        if p.exists():
            df = pd.read_csv(p, parse_dates=["time"])
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce").dt.tz_convert(None)
            return df.sort_values("time").drop_duplicates("time")
    raise FileNotFoundError(f"{symbol}_M5")


def _build_from_book(
    book: dict[str, np.ndarray],
    times: np.ndarray,
    dt: np.ndarray,
    ret: np.ndarray,
    buy_v: np.ndarray,
    sell_v: np.ndarray,
    spread_col: np.ndarray,
    horizon: int,
) -> tuple[pd.DataFrame, str]:
    ofi = ofi_multilevel(book["bid_p"], book["bid_v"], book["ask_p"], book["ask_v"])
    ofi = np.concatenate([[0.0], ofi])
    mid = book["mid"]
    evt_idx = events_from_returns(ret, threshold=2.5)
    t_grid = np.arange(len(mid), dtype=np.float64)
    hawkes = hawkes_intensity_series(evt_idx, t_grid, mu=0.02, alpha=0.35, beta=1.2)
    toxic = vpin_proxy(buy_v, sell_v, window=min(120, max(20, len(mid) // 100)))

    feats = np.column_stack([ofi, hawkes, toxic, ret, spread_col]).astype(np.float64)
    mu, sd = np.nanmean(feats, axis=0), np.nanstd(feats, axis=0) + 1e-9
    feats = np.nan_to_num((feats - mu) / sd, nan=0.0, posinf=0.0, neginf=0.0)
    dt_n = dt / np.maximum(np.median(dt[1:]) if len(dt) > 1 else 1.0, 1e-6)

    ode = NeuralOdeEuler(hidden_dim=16, feat_dim=feats.shape[1])
    h_path = ode.integrate(feats, dt_n)
    alpha = np.array([ode.head_alpha(h_path[i]) for i in range(len(h_path))])
    tox_h = np.array([ode.head_toxicity(h_path[i]) for i in range(len(h_path))])

    fwd_ret = np.zeros_like(mid)
    if horizon < len(mid):
        fwd_ret[:-horizon] = np.log(mid[horizon:] / mid[:-horizon])
        fwd_ret[-horizon:] = np.nan

    out = pd.DataFrame(
        {
            "time": times,
            "ofi": ofi,
            "hawkes_lambda": hawkes,
            "vpin_proxy": toxic,
            "alpha_head": alpha,
            "toxicity_head": tox_h,
            "fwd_ret": fwd_ret,
        }
    )
    return out.dropna(subset=["fwd_ret"]).reset_index(drop=True), book.get("source", "unknown")


def build_microstructure_frame(symbol: str, depth: int = 3) -> tuple[pd.DataFrame, dict]:
    sym = symbol.upper()
    meta = {"symbol": sym, "book_source": "m5_synthetic", "l2_path": None}

    if has_l2_ticks(DATA, sym):
        path = l2_tick_path(DATA, sym)
        meta["l2_path"] = str(path.relative_to(ROOT))
        ticks = load_ticks(path)
        sec = resample_ticks_1s(ticks)
        book = ticks_to_book_arrays(sec, depth=depth)
        book["source"] = "mt5_ticks_1s"
        meta["book_source"] = book["source"]
        meta["raw_ticks"] = len(ticks)
        meta["seconds_bars"] = len(sec)

        mid = book["mid"]
        ret = np.zeros_like(mid)
        ret[1:] = np.log(mid[1:] / mid[:-1])
        buy_v = np.where(ret >= 0, sec["volume"].to_numpy(), 0.0)
        sell_v = np.where(ret < 0, sec["volume"].to_numpy(), 0.0)
        dt = tick_dt_seconds(sec)
        spread_col = (sec["ask"] - sec["bid"]).to_numpy()
        times = sec["time"].to_numpy()
        frame, _ = _build_from_book(book, times, dt, ret, buy_v, sell_v, spread_col, horizon=5)
        frame.attrs["huber_hindsight"] = huber_loss(frame["alpha_head"].to_numpy(), frame["fwd_ret"].to_numpy())
        return frame, meta

    bars = load_m5(sym)
    book = synthetic_book_from_bars(bars, depth=depth)
    book["source"] = "m5_synthetic"
    mid = book["mid"]
    ret = np.zeros_like(mid)
    ret[1:] = np.log(mid[1:] / mid[:-1])
    buy_v = np.where(ret >= 0, bars["tick_volume"].to_numpy(), 0.0)
    sell_v = np.where(ret < 0, bars["tick_volume"].to_numpy(), 0.0)
    times = bars["time"].to_numpy()
    dt = np.ones(len(mid), dtype=np.float64)
    dt[1:] = np.maximum((times[1:] - times[:-1]) / np.timedelta64(1, "s"), 1.0)
    spread_col = bars["spread"].fillna(1).to_numpy()
    frame, _ = _build_from_book(book, times, dt, ret, buy_v, sell_v, spread_col, horizon=5)
    frame.attrs["huber_hindsight"] = huber_loss(frame["alpha_head"].to_numpy(), frame["fwd_ret"].to_numpy())
    return frame, meta


def try_torch_neural_ode_note() -> str:
    try:
        import torch  # noqa: F401
        import torchdiffeq  # noqa: F401
        return "torchdiffeq_available"
    except ImportError:
        return "use_euler_numpy_or_ask_it_whitelist_python_exe_only"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--depth", type=int, default=3)
    args = p.parse_args()

    frame, meta = build_microstructure_frame(args.symbol, depth=args.depth)
    RESULTS.mkdir(parents=True, exist_ok=True)
    tag = "ticks" if meta["book_source"].startswith("mt5") else "M5"
    csv_path = RESULTS / f"microstructure_{args.symbol}_{tag}.csv"
    frame.to_csv(csv_path, index=False)

    last = frame.iloc[-1]
    manifest = {
        "schema_version": 2,
        "symbol": args.symbol,
        "backend": "numpy_ofi_hawkes_euler_ode",
        "book_source": meta["book_source"],
        "torch_status": try_torch_neural_ode_note(),
        "rows": len(frame),
        "meta": meta,
        "last": {
            "ofi": float(last["ofi"]),
            "hawkes_lambda": float(last["hawkes_lambda"]),
            "vpin_proxy": float(last["vpin_proxy"]),
            "alpha_head": float(last["alpha_head"]),
            "toxicity_head": float(last["toxicity_head"]),
            "throttle_execution": bool(last["toxicity_head"] > 0.65),
        },
        "data_gap": None if meta["book_source"].startswith("mt5") else "export_l2_ticks_for_real_bbo",
        "csv": str(csv_path.relative_to(ROOT)),
    }
    man_path = RESULTS / f"microstructure_{args.symbol}_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    mt5 = ROOT / "backtest" / "mt5_files" / "Sovereign" / "microstructure_manifest.json"
    mt5.parent.mkdir(parents=True, exist_ok=True)
    mt5.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
