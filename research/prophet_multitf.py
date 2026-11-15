#!/usr/bin/env python3
"""Multi-timeframe next-bar direction research (Sovereign Prophet).

Trains causal logistic models per (symbol, timeframe) on ecosystem features.
Promotion requires validation+OOS accuracy > coin-flip and positive monthly mean on
a simple long-if-p_up>0.55 rule (research only — not live).

Run:
    .\\.venv\\Scripts\\python.exe research\\prophet_multitf.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(HERE))

from ecosystem_pillars import knowledge_inventory  # noqa: E402
from fxmopol_chart_core import build_stack, stack_long, stack_short  # noqa: E402
from fxmopol_signal_layers import prep_macro_series  # noqa: E402
from period_returns import publish_results, to_monthly  # noqa: E402

DATA = HERE / "data"
RESULTS = HERE / "results"

SYMBOLS = ("GBPUSD", "EURUSD", "USDJPY")
TIMEFRAMES = ("H1", "D1")

SPLITS = {
    "design": ("2018-01-01", "2022-01-01"),
    "validation": ("2022-01-01", "2024-07-01"),
    "OOS": ("2024-07-01", "2030-01-01"),
}

MIN_TRAIN = 800


def load_bars(sym: str, tf: str) -> pd.DataFrame:
    paths = [
        DATA / "histdata" / f"{sym}_{tf}.csv",
        DATA / f"{sym}_{tf}.csv",
    ]
    for p in paths:
        if not p.exists():
            continue
        d = pd.read_csv(p, parse_dates=["time"])
        d["time"] = pd.to_datetime(d["time"], utc=True, errors="coerce").dt.tz_convert(None)
        d = d.drop_duplicates("time").set_index("time").sort_index()
        for col in ("open", "high", "low", "close"):
            d[col] = pd.to_numeric(d[col], errors="coerce")
        return d.dropna(subset=["close"])
    raise FileNotFoundError(f"{sym}_{tf}")


def resample_h4(h1: pd.DataFrame) -> pd.DataFrame:
    return (
        h1.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna(subset=["close"])
    )


def feature_frame(bars: pd.DataFrame, sym: str) -> pd.DataFrame:
    st = build_stack(bars.reset_index().rename(columns={bars.index.name or "index": "time"}), 50)
    c = st["close"]
    n = len(c)
    long_s = pd.Series([stack_long(c, st["ema21"], st["ema50"], st["smma100"], i) for i in range(n)])
    short_s = pd.Series([stack_short(c, st["ema21"], st["ema50"], st["smma100"], i) for i in range(n)])
    idx = bars.index[:n]
    out = pd.DataFrame(
        {
            "ret1": c.pct_change().to_numpy(),
            "ret3": c.pct_change(3).to_numpy(),
            "ret6": c.pct_change(6).to_numpy(),
            "rsi": (st["rsi"] / 100.0).to_numpy(),
            "atr_pct": (st["atr"] / c.replace(0, np.nan)).to_numpy(),
            "stack_long": long_s.astype(float).to_numpy(),
            "stack_short": short_s.astype(float).to_numpy(),
        },
        index=idx,
    )
    out["regime"] = out["stack_long"] - out["stack_short"]
    out["hour"] = idx.hour / 24.0
    out["dow"] = idx.dayofweek / 6.0

    carry_s, vix_chg_s = prep_macro_series(idx, sym)
    out["carry"] = carry_s.reindex(idx).ffill().fillna(0.0)
    out["vix_chg"] = vix_chg_s.reindex(idx).ffill().fillna(0.0)

    c_np = c.to_numpy(dtype=float)
    fwd_np = np.roll(c_np, -1)
    fwd_np[-1] = np.nan
    out["y_up"] = (fwd_np > c_np).astype(float)
    out["fwd_ret"] = fwd_np / np.where(c_np != 0, c_np, np.nan) - 1.0
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.dropna(subset=["fwd_ret", "ret1", "rsi", "atr_pct"])
    return out.iloc[:-1]


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -40, 40)
    return 1.0 / (1.0 + np.exp(-z))


def fit_logistic(x: np.ndarray, y: np.ndarray, lr: float = 0.05, steps: int = 400) -> np.ndarray:
    n, d = x.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(steps):
        p = _sigmoid(x @ w + b)
        err = p - y
        grad_w = (x.T @ err) / n + 0.01 * w
        grad_b = err.mean()
        w -= lr * grad_w
        b -= lr * grad_b
    return np.concatenate([[b], w])


def predict_logistic(coef: np.ndarray, x: np.ndarray) -> np.ndarray:
    b, w = coef[0], coef[1:]
    return _sigmoid(x @ w + b)


def split_mask(index: pd.DatetimeIndex, a: str, b: str) -> np.ndarray:
    t0, t1 = pd.Timestamp(a), pd.Timestamp(b)
    return (index >= t0) & (index < t1)


def evaluate_split(
    df: pd.DataFrame,
    feat_cols: list[str],
    train_mask: np.ndarray,
    test_mask: np.ndarray,
) -> dict:
    x_train = df.loc[train_mask, feat_cols].values.astype(float)
    y_train = df.loc[train_mask, "y_up"].values.astype(float)
    x_test = df.loc[test_mask, feat_cols].values.astype(float)
    y_test = df.loc[test_mask, "y_up"].values.astype(float)
    if len(x_train) < MIN_TRAIN or len(x_test) < 50:
        return {"n": len(x_test), "acc": np.nan, "brier": np.nan, "mean_fwd_bps": np.nan}

    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0) + 1e-9
    x_train = (x_train - mu) / sd
    x_test = (x_test - mu) / sd

    coef = fit_logistic(x_train, y_train)
    p = predict_logistic(coef, x_test)
    pred = (p >= 0.5).astype(float)
    acc = float((pred == y_test).mean())
    brier = float(np.mean((p - y_test) ** 2))

    # simple directional PnL proxy in bps
    fwd = df.loc[test_mask, "fwd_ret"].values
    side = np.where(p > 0.55, 1.0, np.where(p < 0.45, -1.0, 0.0))
    mean_bps = float(np.nanmean(side * fwd) * 10000)

    return {
        "n": int(len(x_test)),
        "acc": round(acc, 4),
        "brier": round(brier, 4),
        "mean_fwd_bps": round(mean_bps, 3),
        "coef": coef.tolist(),
        "mu": mu.tolist(),
        "sd": sd.tolist(),
        "feat_cols": feat_cols,
    }


def run_symbol_tf(sym: str, tf: str) -> dict:
    bars = load_bars(sym, tf)
    df = feature_frame(bars, sym)
    feat_cols = [c for c in df.columns if c not in ("y_up", "fwd_ret")]
    rows = []
    models: dict[str, dict] = {}
    for split, (a, b) in SPLITS.items():
        train_end = pd.Timestamp(a)
        if split == "design":
            tr = df.index < pd.Timestamp(b)
        elif split == "validation":
            tr = (df.index >= pd.Timestamp(SPLITS["design"][0])) & (df.index < pd.Timestamp(b))
        else:
            tr = df.index < pd.Timestamp(SPLITS["validation"][1])
        te = split_mask(df.index, a, b)
        tr_mask = np.asarray(tr, dtype=bool)
        te_mask = np.asarray(te, dtype=bool)
        ev = evaluate_split(df, feat_cols, tr_mask, te_mask)
        ev["split"] = split
        ev["symbol"] = sym
        ev["timeframe"] = tf
        rows.append({k: ev[k] for k in ("split", "symbol", "timeframe", "n", "acc", "brier", "mean_fwd_bps")})
        if split == "design" and "coef" in ev:
            models[f"{sym}|{tf}"] = {
                "coef": ev["coef"],
                "mu": ev["mu"],
                "sd": ev["sd"],
                "feat_cols": ev["feat_cols"],
            }

    oos_row = next((r for r in rows if r["split"] == "OOS"), {})
    promoted = (
        oos_row.get("n", 0) >= 200
        and (oos_row.get("acc") or 0) > 0.51
        and (oos_row.get("mean_fwd_bps") or 0) > 0
    )
    return {"rows": rows, "models": models, "promoted": promoted}


def live_forecasts(models: dict) -> dict:
    out: dict = {}
    for key, m in models.items():
        sym, tf = key.split("|", 1)
        try:
            bars = load_bars(sym, tf)
            df = feature_frame(bars, sym)
        except FileNotFoundError:
            continue
        if df.empty:
            continue
        row = df.iloc[-2]
        cols = m["feat_cols"]
        x = np.array([row[c] for c in cols], dtype=float)
        mu = np.array(m["mu"])
        sd = np.array(m["sd"])
        x = (x - mu) / (sd + 1e-9)
        coef = np.array(m["coef"])
        p_up = float(predict_logistic(coef, x.reshape(1, -1))[0])
        out.setdefault(sym, {})[tf] = {
            "p_up": round(p_up, 4),
            "p_down": round(1 - p_up, 4),
            "next_move": "up" if p_up >= 0.5 else "down",
            "confidence": round(abs(p_up - 0.5) * 2, 4),
            "bar_time": str(row.name),
        }
    return out


def main() -> int:
    inv = knowledge_inventory()
    all_rows: list[dict] = []
    all_models: dict = {}
    manifest: dict = {"schema_version": 1, "symbols": {}, "inventory": inv}

    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            try:
                rec = run_symbol_tf(sym, tf)
            except FileNotFoundError as exc:
                print(f"[prophet] skip {sym} {tf}: {exc}")
                continue
            all_rows.extend(rec["rows"])
            all_models.update(rec["models"])
            manifest["symbols"][f"{sym}|{tf}"] = {"promoted": rec["promoted"], "splits": rec["rows"]}

    if not all_rows:
        print("[prophet] no data")
        return 1

    summary = pd.DataFrame(all_rows)
    out_csv = RESULTS / "prophet_multitf_summary.csv"
    summary.to_csv(out_csv, index=False)
    print(summary.to_string(index=False))

    forecasts = live_forecasts(all_models)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "prophet_forecasts.json").write_text(json.dumps(forecasts, indent=2), encoding="utf-8")
    manifest["live_forecasts"] = forecasts
    manifest["models"] = list(all_models.keys())
    (RESULTS / "prophet_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    mt5 = ROOT / "backtest" / "mt5_files" / "Sovereign" / "prophet_forecasts.json"
    mt5.parent.mkdir(parents=True, exist_ok=True)
    mt5.write_text(json.dumps(forecasts, indent=2), encoding="utf-8")

    # Monthly proof from OOS pseudo-strategy
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            key = f"{sym}|{tf}"
            if key not in all_models:
                continue
            try:
                bars = load_bars(sym, tf)
                df = feature_frame(bars, sym)
            except FileNotFoundError:
                continue
            m = all_models[key]
            cols = m["feat_cols"]
            x = (df[cols].values - np.array(m["mu"])) / (np.array(m["sd"]) + 1e-9)
            p = predict_logistic(np.array(m["coef"]), x)
            side = np.where(p > 0.55, 1.0, np.where(p < 0.45, -1.0, 0.0))
            strat = pd.Series(side * df["fwd_ret"].values, index=df.index)
            oos = strat[split_mask(strat.index, *SPLITS["OOS"])]
            monthly = to_monthly(oos)
            monthly.to_csv(RESULTS / f"prophet_{sym}_{tf}_monthly.csv", index=False)

    demo = RESULTS / f"prophet_{SYMBOLS[0]}_{TIMEFRAMES[0]}_monthly.csv"
    if demo.exists():
        md = pd.read_csv(demo)
        if "return_pct" in md.columns and len(md):
            rets = md["return_pct"].astype(float) / 100.0
            rets.index = pd.to_datetime(md["date"])
            publish_results("prophet_multitf", rets, already_monthly=True, last_months=12)
    print(f"[prophet] wrote {out_csv} forecasts={len(forecasts)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
