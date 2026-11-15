#!/usr/bin/env python3
"""Multi-horizon log-return forecast — sklearn + purged CV (no PyTorch / pip).

Quantile-ish bands via residual percentiles on validation fold.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from purged_cv import purge_train_mask  # noqa: E402
from tft_forecast_pipeline import prepare_tft_frame, risk_bounds_from_quantiles  # noqa: E402

RESULTS = ROOT / "research" / "results"
FEATS = ["log_return", "atr", "volume", "news_hi_impact"]
TARGET = "log_return"
HORIZON = 5
ENCODER = 60


def make_supervised(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Each row: flatten last ENCODER lags of FEATS; y = mean forward log-return over HORIZON."""
    n = len(frame)
    xs, ys, ends = [], [], []
    f = frame[FEATS].to_numpy(dtype=np.float64)
    log_r = frame["log_return"].to_numpy(dtype=np.float64)
    for i in range(ENCODER, n - HORIZON):
        xs.append(f[i - ENCODER : i].ravel())
        ys.append(float(np.mean(log_r[i + 1 : i + 1 + HORIZON])))
        ends.append(i)
    return np.asarray(xs), np.asarray(ys), np.asarray(ends)


def main(symbol: str = "GBPUSD") -> int:
    frame = prepare_tft_frame(symbol)
    x, y, ends = make_supervised(frame)
    n = len(x)
    test_start = int(n * 0.85)
    test_end = n
    train_mask = purge_train_mask(np.arange(n), test_start, test_end, label_window=HORIZON)

    model = HistGradientBoostingRegressor(max_depth=6, learning_rate=0.05, max_iter=150)
    model.fit(x[train_mask], y[train_mask])
    pred = model.predict(x[test_start:test_end])
    truth = y[test_start:test_end]
    resid = truth - pred
    q10, q50, q90 = np.percentile(resid, [10, 50, 90])
    price = float(frame["close"].iloc[-1])
    bounds = risk_bounds_from_quantiles(q10, q90, price)

    forecasts = {
        symbol: {
            "H1": {
                "p_up": float(0.5 + np.tanh(float(pred[-1]) / 0.001) * 0.25),
                "q10_resid": float(q10),
                "q90_resid": float(q90),
                "median_pred_logret_5h": float(pred[-1]),
                "next_move": "up" if pred[-1] > 0 else "down",
                "confidence": float(min(abs(pred[-1]) / 0.002, 1.0)),
                "risk_bounds": bounds,
            }
        },
        "_meta": {
            "backend": "sklearn_hist_gradient_boosting",
            "no_pip_no_torch": True,
        },
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "prophet_forecasts.json"
    mt5 = ROOT / "backtest" / "mt5_files" / "Sovereign" / "prophet_forecasts.json"
    payload = json.dumps(forecasts, indent=2)
    out.write_text(payload, encoding="utf-8")
    mt5.parent.mkdir(parents=True, exist_ok=True)
    mt5.write_text(payload, encoding="utf-8")
    mae = float(np.mean(np.abs(truth - pred)))
    manifest = {"symbol": symbol, "val_mae": mae, "n_test": len(pred), "bounds": bounds}
    (RESULTS / "horizon_sklearn_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "GBPUSD"
    raise SystemExit(main(sym))
