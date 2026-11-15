#!/usr/bin/env python3
"""Temporal Fusion Transformer pipeline — multi-horizon FX log-return quantiles.

Requires: requirements-ml.txt (torch, pytorch-forecasting, lightning).

Purged splits, TimeSeriesDataSet (encoder=60, pred=5), QuantileLoss [0.1,0.5,0.9].
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from purged_cv import purge_train_mask

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
DATA = RESEARCH / "data"
RESULTS = RESEARCH / "results"
FXMACRO = DATA / "fxmacrodata"


def load_pair_h1(symbol: str = "GBPUSD") -> pd.DataFrame:
    for p in (DATA / "histdata" / f"{symbol}_H1.csv", DATA / f"{symbol}_H1.csv"):
        if p.exists():
            df = pd.read_csv(p, parse_dates=["time"])
            df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce").dt.tz_convert(None)
            df = df.sort_values("time").drop_duplicates("time")
            return df
    raise FileNotFoundError(symbol)


def rolling_true_range(h: pd.Series, l: pd.Series, c: pd.Series, period: int = 14) -> pd.Series:
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def high_impact_news_flag(times: pd.DatetimeIndex) -> np.ndarray:
    """Binary: within 4h after USD high-impact scheduled event (known future in live)."""
    path = FXMACRO / "calendar_USD.csv"
    if not path.exists():
        return np.zeros(len(times), dtype=np.float32)
    cal = pd.read_csv(path)
    cal["event_time"] = pd.to_datetime(cal["event_time"], utc=True, errors="coerce").dt.tz_convert(None)
    cal = cal[cal["importance"].astype(str).str.lower() == "high"].dropna(subset=["event_time"])
    flags = np.zeros(len(times), dtype=np.float32)
    if cal.empty:
        return flags
    ev = cal["event_time"].to_numpy()
    for i, t in enumerate(times):
        # known at t: any event in (t, t+4h] for forecasting; at historical t use past events as proxy
        delta = (ev - np.datetime64(t)) / np.timedelta64(1, "h")
        flags[i] = 1.0 if np.any((delta > 0) & (delta <= 4)) else 0.0
    return flags


def prepare_tft_frame(symbol: str = "GBPUSD") -> pd.DataFrame:
    raw = load_pair_h1(symbol)
    c = raw["close"].astype(float)
    h = raw["high"].astype(float)
    l = raw["low"].astype(float)
    v = raw.get("tick_volume", pd.Series(0, index=raw.index)).astype(float)

    out = pd.DataFrame()
    out["time_idx"] = np.arange(len(raw), dtype=np.int64)
    out["symbol"] = f"{symbol[:3]}_{symbol[3:]}"
    out["close"] = c.values
    out["volume"] = v.values
    out["log_return"] = np.log(c / c.shift(1))
    out["atr"] = rolling_true_range(h, l, c).values
    out["news_hi_impact"] = high_impact_news_flag(pd.DatetimeIndex(raw["time"]))

    out = out.dropna().reset_index(drop=True)
    out["time_idx"] = np.arange(len(out), dtype=np.int64)
    return out


def risk_bounds_from_quantiles(q10_move: float, q90_move: float, price: float, pip_size: float = 1e-4) -> dict:
    """Production helper: 10th %ile adverse move → stop; 90th %ile favorable → take-profit."""
    # Log-return quantiles → price levels
    stop = price * float(np.exp(q10_move))  # adverse for long if q10 < 0
    tp = price * float(np.exp(q90_move))
    stop_pips = abs(price - stop) / pip_size
    tp_pips = abs(tp - price) / pip_size
    return {
        "stop_price_long": min(stop, price),
        "tp_price_long": max(tp, price),
        "stop_pips": round(stop_pips, 1),
        "tp_pips": round(tp_pips, 1),
        "note": "Use q10 for long stop / short TP mirror; sign-adjust in execution layer.",
    }


def run_training(
    symbol: str = "GBPUSD",
    max_epochs: int = 8,
    gpus: int = 0,
) -> int:
    try:
        import torch
        from lightning.pytorch import Trainer
        from lightning.pytorch.callbacks import LearningRateMonitor, ModelCheckpoint
        from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
        from pytorch_forecasting.data import GroupNormalizer
        from pytorch_forecasting.metrics import QuantileLoss
    except ImportError as exc:
        print(f"[tft] missing deps: {exc}. pip install -r requirements-ml.txt")
        return 1

    df = prepare_tft_frame(symbol)
    n = len(df)
    test_start = int(n * 0.85)
    test_end = n
    label_window = 5
    train_mask = purge_train_mask(np.arange(n), test_start, test_end, label_window)
    train_df = df.loc[train_mask].copy()
    val_df = df.iloc[test_start:test_end].copy()

    max_encoder_length = 60
    max_prediction_length = 5
    training_cutoff = train_df["time_idx"].max() - max_prediction_length

    training = TimeSeriesDataSet(
        train_df[train_df.time_idx <= training_cutoff],
        time_idx="time_idx",
        target="log_return",
        group_ids=["symbol"],
        max_encoder_length=max_encoder_length,
        max_prediction_length=max_prediction_length,
        time_varying_known_reals=["news_hi_impact"],
        time_varying_unknown_reals=["close", "volume", "atr", "log_return"],
        static_categoricals=["symbol"],
        target_normalizer=GroupNormalizer(groups=["symbol"]),
        add_relative_time_idx=True,
        add_target_scales=True,
        add_encoder_length=True,
    )

    validation = TimeSeriesDataSet.from_dataset(training, val_df, predict=True, stop_randomization=True)

    train_loader = training.to_dataloader(train=True, batch_size=64, num_workers=0)
    val_loader = validation.to_dataloader(train=False, batch_size=64, num_workers=0)

    tft = TemporalFusionTransformer.from_dataset(
        training,
        learning_rate=1e-3,
        hidden_size=32,
        attention_head_size=4,
        dropout=0.1,
        hidden_continuous_size=16,
        loss=QuantileLoss(quantiles=[0.1, 0.5, 0.9]),
        log_interval=-1,
        reduce_on_plateau_patience=3,
    )

    checkpoint = ModelCheckpoint(monitor="val_loss", mode="min", save_top_k=1)
    trainer = Trainer(
        max_epochs=max_epochs,
        accelerator="gpu" if gpus and torch.cuda.is_available() else "cpu",
        devices=1,
        precision="16-mixed" if torch.cuda.is_available() else "32-true",
        gradient_clip_val=0.1,
        callbacks=[checkpoint, LearningRateMonitor(logging_interval="epoch")],
        enable_progress_bar=True,
        logger=False,
    )

    trainer.fit(tft, train_loader, val_loader)

    # Demo risk bounds on last validation batch median
    raw = tft.predict(val_loader, mode="raw", return_x=True)
    preds = raw.output["prediction"]
    if preds is not None and len(preds):
        last_q = preds[-1, 0, :]  # horizons x quantiles
        price = float(val_df["close"].iloc[-1])
        bounds = risk_bounds_from_quantiles(float(last_q[0]), float(last_q[2]), price)
        print("[tft] risk bounds example (long):", bounds)

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"tft_{symbol}_manifest.txt"
    out.write_text(
        f"symbol={symbol}\ntrain_rows={len(train_df)}\nval_rows={len(val_df)}\nepochs={max_epochs}\n",
        encoding="utf-8",
    )
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="TFT FX quantile pipeline")
    p.add_argument("--symbol", default="GBPUSD")
    p.add_argument("--epochs", type=int, default=8)
    p.add_argument("--gpu", type=int, default=0)
    p.add_argument("--prep-only", action="store_true")
    args = p.parse_args()
    if args.prep_only:
        frame = prepare_tft_frame(args.symbol)
        RESULTS.mkdir(parents=True, exist_ok=True)
        path = RESULTS / f"tft_{args.symbol}_frame.csv"
        frame.to_csv(path, index=False)
        print(f"[tft] wrote {path} rows={len(frame)}")
        return 0
    return run_training(args.symbol, max_epochs=args.epochs, gpus=args.gpu)


if __name__ == "__main__":
    raise SystemExit(main())
