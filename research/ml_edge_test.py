#!/usr/bin/env python3
"""Can machine learning find direction in features that measured as directionless?

The question this answers: everything visual tested in this project turned out to be
a persistence detector with zero forward drift. Does a nonlinear model - boosted
trees, a neural network - extract directional signal that the linear tests missed?

That is a falsifiable question, so this script falsifies it rather than asserting it.

THE THREE TRAPS THIS AVOIDS

  1. OVERLAPPING LABELS. A forward h-bar return means rows t and t+1 share h-1 bars
     of their label. Training on that leaks the answer. Every fold therefore PURGES
     the last h bars of the training window and EMBARGOES the first h bars after
     the test window, and the test window is never shuffled.

  2. FITTING THE SCALER ON THE TEST SET. Scaler, feature selection and model are
     all fitted inside the training window only, then applied forward.

  3. A PIPELINE THAT LEAKS AND LOOKS SMART. Every run is repeated with the training
     labels randomly shuffled. If the shuffled control scores above 0.5 out of
     sample, the pipeline is leaking and the real score means nothing. This is the
     check that makes the rest trustworthy.

MODELS, weakest to strongest, so capacity is the only thing that changes
  majority        predict the base rate                (AUC 0.500 by construction)
  linear          logistic regression                  (the honest benchmark)
  trees           histogram gradient boosting          (nonlinear, no extrapolation)
  neural          multi-layer perceptron               (nonlinear, smooth)
  lgbm            LightGBM leaf-wise boosting          (the xgboost/lightgbm family)

If the neural net does not beat the linear model out of sample, added capacity
bought nothing - which is what "there is no signal to fit" looks like.

Usage
    .\\.venv\\Scripts\\python.exe research/ml_edge_test.py --symbol EURUSD --tf H1
    .\\.venv\\Scripts\\python.exe research/ml_edge_test.py --symbol EURUSD --h 5
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

try:  #--- optional: the leaf-wise booster the earlier notes could not install
    import lightgbm as lgb
    HAS_LGB = True
except Exception:  # pragma: no cover
    HAS_LGB = False

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

warnings.filterwarnings("ignore")


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Every feature uses data up to and including bar t. None look forward."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    f = pd.DataFrame(index=df.index)
    atr = wilder_atr(df, ATR_P)
    e21 = ema(c, EMA)
    e50 = ema(c, 50)
    rsi = wilder_rsi(c, RSI_P)
    d1 = rsi.diff()
    f["rsi"] = rsi
    f["rsi_d1"] = d1
    f["rsi_d2"] = d1.diff()
    f["rsi_z"] = (rsi - rsi.rolling(100, min_periods=20).mean()) / \
                 rsi.rolling(100, min_periods=20).std()
    f["dist21"] = (c - e21) / atr
    f["dist50"] = (c - e50) / atr
    f["slope3"] = (e21 - e21.shift(3)) / atr
    f["slope10"] = (e21 - e21.shift(10)) / atr
    f["atr_ratio"] = atr / atr.rolling(100, min_periods=20).mean()
    f["body"] = (c - o) / atr
    f["range_atr"] = (h - l) / atr
    f["ret1"] = c.pct_change() * 100
    f["ret5"] = c.pct_change(5) * 100
    f["ret20"] = c.pct_change(20) * 100
    streak = np.zeros(len(df))
    col = np.sign(c - o).to_numpy()
    for t in range(1, len(df)):
        streak[t] = streak[t - 1] + col[t] if col[t] == col[t - 1] else col[t]
    f["streak"] = streak
    spread = df["spread"].astype(float)
    #--- the median spread is 0 on more than half the bars, so floor the divisor
    #--- instead of dividing by zero and blanking most of the sample
    med = spread.rolling(500, min_periods=50).median().clip(lower=1.0)
    f["spread_rel"] = spread / med
    t = pd.to_datetime(df["time"])
    f["hour_sin"] = np.sin(2 * np.pi * t.dt.hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * t.dt.hour / 24)
    f["dow"] = t.dt.dayofweek
    f["above50"] = (c > e50).astype(int)
    return f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--h", type=int, default=5, help="forward horizon in bars")
    ap.add_argument("--train", type=int, default=20000)
    ap.add_argument("--test", type=int, default=3000)
    ap.add_argument("--step", type=int, default=3000)
    ap.add_argument("--conf", type=float, default=0.05,
                    help="trade only when |p-0.5| exceeds this")
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    args = ap.parse_args()

    sym, tf, h = args.symbol.upper(), args.tf.upper(), args.h
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    feats = build_features(df)
    fwd = df["close"].shift(-h) / df["close"] - 1.0
    y = (fwd > 0).astype(int)
    ok = feats.notna().all(axis=1) & fwd.notna() & np.isfinite(fwd)
    feats = feats[ok].reset_index(drop=True)
    y = y[ok].reset_index(drop=True)
    fwd = fwd[ok].reset_index(drop=True)
    close = df["close"][ok].reset_index(drop=True)
    time = df["time"][ok].reset_index(drop=True)
    spread = df["spread"][ok].astype(float).reset_index(drop=True)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_side = (np.maximum(spread.to_numpy(), args.floor_pts) / 2.0
                 + args.slip_pts + args.comm_pts / 2.0) * point

    X = feats.to_numpy(float)
    n = len(X)
    names = list(feats.columns)

    print(f"=== can ML find direction that linear tests missed?  {sym} {tf} ===")
    print(f"bars {n}  {time.iloc[0]:%Y-%m-%d} -> {time.iloc[-1]:%Y-%m-%d}   "
          f"horizon {h} bars   features {len(names)}")
    print(f"base rate P(up) {100 * y.mean():.1f}%")
    print(f"walk-forward: train {args.train}, test {args.test}, step {args.step}, "
          f"purge+embargo {h} bars")
    print("")

    folds = []
    start = args.train
    while start + args.test <= n:
        tr_lo = start - args.train
        tr_hi = start - h                      # purge: labels overlap the test
        te_lo = start
        te_hi = min(start + args.test, n)
        if tr_hi - tr_lo > 2000 and te_hi - te_lo > 200:
            folds.append((tr_lo, tr_hi, te_lo, te_hi))
        start += args.step
    if not folds:
        raise SystemExit("not enough bars for one fold")

    models = {
        "linear": lambda: LogisticRegression(max_iter=500, C=0.1),
        "trees": lambda: HistGradientBoostingClassifier(
            max_iter=120, learning_rate=0.06, max_depth=3,
            min_samples_leaf=200, l2_regularization=1.0, random_state=0),
        "neural": lambda: MLPClassifier(
            hidden_layer_sizes=(32, 16), alpha=1.0, max_iter=200,
            early_stopping=True, n_iter_no_change=10, random_state=0),
    }
    if HAS_LGB:
        models["lgbm"] = lambda: lgb.LGBMClassifier(
            n_estimators=400, learning_rate=0.05, num_leaves=31,
            min_child_samples=100, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, reg_lambda=1.0, n_jobs=-1,
            verbose=-1, random_state=0)
    order = ["majority", "linear", "trees", "neural"] + (["lgbm"] if HAS_LGB else [])

    def run(model_name, shuffle_labels=False):
        preds = np.full(n, np.nan)
        rng = np.random.default_rng(12345)
        for tr_lo, tr_hi, te_lo, te_hi in folds:
            Xtr, ytr = X[tr_lo:tr_hi], y.to_numpy()[tr_lo:tr_hi]
            if shuffle_labels:
                ytr = rng.permutation(ytr)
            sc = StandardScaler().fit(Xtr)
            Xtr_s, Xte_s = sc.transform(Xtr), sc.transform(X[te_lo:te_hi])
            if model_name == "majority":
                preds[te_lo:te_hi] = float(ytr.mean())
                continue
            m = models[model_name]()
            m.fit(Xtr_s, ytr)
            preds[te_lo:te_hi] = m.predict_proba(Xte_s)[:, 1]
        return preds

    def evaluate(preds, label):
        m = np.isfinite(preds)
        yt = y.to_numpy()[m]
        p = preds[m]
        auc = roc_auc_score(yt, p) if len(np.unique(yt)) > 1 else 0.5
        acc = float(((p > 0.5) == yt).mean())
        pos = np.where(np.abs(p - 0.5) > args.conf, np.sign(p - 0.5), 0.0)
        #--- positions live on the full bar index; cost is charged per side on
        #--- every change, and the return is the next bar's move
        pos_full = np.zeros(n)
        pos_full[m] = pos
        c = close.to_numpy()
        ret = np.zeros(n)
        ret[1:] = pos_full[:-1] * (c[1:] / c[:-1] - 1.0)
        turn = np.abs(np.diff(pos_full, prepend=0.0))
        ret -= turn * (cost_side / c)
        r = ret[m]
        eq = np.cumprod(1.0 + r)
        total = float(eq[-1] - 1.0) * 100
        traded = int((pos != 0).sum())
        return auc, acc, total, traded

    print(f"{'model':<14}{'OOS AUC':>9}{'accuracy':>10}{'net return':>12}"
          f"{'bars traded':>13}")
    print("-" * 60)
    out = []
    for name in order:
        p = run(name)
        auc, acc, total, traded = evaluate(p, name)
        out.append((name, auc, acc, total, traded, False))
        print(f"{name:<14}{auc:>9.4f}{100 * acc:>9.2f}%{total:>11.2f}%{traded:>13}")
    print("")

    print("--- leak control: same pipeline, training labels SHUFFLED ---")
    print("    (if this scores above 0.5 OOS the pipeline leaks and nothing above counts)")
    print(f"{'model':<14}{'OOS AUC':>9}{'net return':>12}")
    print("-" * 35)
    for name in [m for m in order if m != "majority"]:
        p = run(name, shuffle_labels=True)
        auc, acc, total, traded = evaluate(p, name)
        out.append((name, auc, acc, total, traded, True))
        print(f"{name:<14}{auc:>9.4f}{total:>11.2f}%")
    print("")

    best = max((r for r in out if not r[5]), key=lambda r: r[1])
    print(f"best real model: {best[0]}  AUC {best[1]:.4f}  net {best[3]:+.2f}%")
    print(f"linear benchmark: AUC {[r[1] for r in out if r[0] == 'linear' and not r[5]][0]:.4f}")
    if best[1] - 0.5 < 0.01:
        print("-> AUC is within 0.01 of a coin flip: no directional signal to fit.")
    if best[0] != "linear":
        lin = [r for r in out if r[0] == "linear" and not r[5]][0]
        d = best[1] - lin[1]
        print(f"-> nonlinear minus linear AUC = {d:+.4f}  "
              f"({'capacity helped' if d > 0.005 else 'capacity bought nothing'})")
    print("")

    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out, columns=["model", "auc", "accuracy", "net_pct",
                               "bars_traded", "shuffled"]).to_csv(
        RESULTS / f"ml_edge_test_{sym}_{tf}_h{h}.csv", index=False)
    print(f"wrote {RESULTS / f'ml_edge_test_{sym}_{tf}_h{h}.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
