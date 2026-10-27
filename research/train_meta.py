#!/usr/bin/env python3
"""Train a meta-label neural network for the Sovereign EA.

The primary rule engine proposes trades. This model predicts whether the
proposed trade will be profitable, and the EA uses that probability as a veto.

Outputs:
  research/models/meta_model.json
  research/models/candidates_M5.csv
  EA/Include/SovereignMLModel.mqh
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import candidates  # noqa: E402
import core  # noqa: E402

MODEL_DIR = HERE / "models"
EA_INCLUDE = ROOT / "EA" / "Include"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def auc_score(y: np.ndarray, p: np.ndarray) -> float:
    order = np.argsort(p)
    y = y[order]
    p = p[order]
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5
    ranks = np.arange(1, len(y) + 1, dtype=float)
    # Average ranks for ties.
    _, inv, counts = np.unique(p, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    avg = sums / counts
    ranks = avg[inv]
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


class MLP:
    def __init__(self, n_in: int, n_hidden: int = 24, seed: int = 7):
        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0, np.sqrt(2.0 / n_in), size=(n_hidden, n_in))
        self.b1 = np.zeros(n_hidden)
        self.W2 = rng.normal(0, np.sqrt(2.0 / n_hidden), size=n_hidden)
        self.b2 = 0.0
        self.n_hidden = n_hidden

    def forward(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        z1 = X @ self.W1.T + self.b1
        a1 = np.maximum(0.0, z1)
        logit = a1 @ self.W2 + self.b2
        return a1, logit, sigmoid(logit)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.forward(X)[2]

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        X_val: np.ndarray,
        y_val: np.ndarray,
        epochs: int = 2500,
        lr: float = 0.01,
        l2: float = 1e-3,
        batch: int = 64,
        seed: int = 7,
    ) -> dict:
        rng = np.random.default_rng(seed)
        n = len(X)
        pos = max(y.sum(), 1.0)
        neg = max(n - pos, 1.0)
        w_pos = neg / pos
        weights = np.where(y > 0.5, w_pos, 1.0)
        mW1, vW1 = np.zeros_like(self.W1), np.zeros_like(self.W1)
        mb1, vb1 = np.zeros_like(self.b1), np.zeros_like(self.b1)
        mW2, vW2 = np.zeros_like(self.W2), np.zeros_like(self.W2)
        mb2, vb2 = 0.0, 0.0
        beta1, beta2, eps = 0.9, 0.999, 1e-8
        best_loss = float("inf")
        best = None
        history = []
        for epoch in range(1, epochs + 1):
            idx = rng.permutation(n)
            for start in range(0, n, batch):
                b = idx[start : start + batch]
                xb, yb, wb = X[b], y[b], weights[b]
                a1, logit, p = self.forward(xb)
                # Weighted binary cross-entropy gradient.
                dz2 = (p - yb) * wb / wb.sum()
                dW2 = a1.T @ dz2 + l2 * self.W2
                db2 = dz2.sum()
                da1 = np.outer(dz2, self.W2)
                dz1 = da1 * (a1 > 0)
                dW1 = dz1.T @ xb + l2 * self.W1
                db1 = dz1.sum(axis=0)

                for param, grad, m, v in (
                    (self.W1, dW1, mW1, vW1),
                    (self.b1, db1, mb1, vb1),
                    (self.W2, dW2, mW2, vW2),
                ):
                    m *= beta1
                    m += (1 - beta1) * grad
                    v *= beta2
                    v += (1 - beta2) * (grad * grad)
                    mhat = m / (1 - beta1**epoch)
                    vhat = v / (1 - beta2**epoch)
                    param -= lr * mhat / (np.sqrt(vhat) + eps)
                mb2 = beta1 * mb2 + (1 - beta1) * db2
                vb2 = beta2 * vb2 + (1 - beta2) * db2 * db2
                mb2h = mb2 / (1 - beta1**epoch)
                vb2h = vb2 / (1 - beta2**epoch)
                self.b2 -= lr * mb2h / (np.sqrt(vb2h) + eps)

            p_val = self.predict(X_val)
            eps_ = 1e-9
            val_loss = float(
                -np.mean(y_val * np.log(p_val + eps_) + (1 - y_val) * np.log(1 - p_val + eps_))
            )
            history.append(val_loss)
            if val_loss < best_loss - 1e-6:
                best_loss = val_loss
                best = (self.W1.copy(), self.b1.copy(), self.W2.copy(), self.b2)
            elif epoch > 300 and val_loss > best_loss + 1e-4:
                break
        if best is not None:
            self.W1, self.b1, self.W2, self.b2 = best
        return {"val_loss": best_loss, "epochs": len(history), "history": history}


def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "win": 0.0, "avg_r": 0.0, "total_r": 0.0, "pf": 0.0}
    wins = df["r_multiple"] > 0
    gp = df.loc[wins, "r_multiple"].sum()
    gl = -df.loc[~wins, "r_multiple"].sum()
    return {
        "n": int(len(df)),
        "win": float(wins.mean() * 100.0),
        "avg_r": float(df["r_multiple"].mean()),
        "total_r": float(df["r_multiple"].sum()),
        "pf": float(gp / gl) if gl > 0 else 999.0,
    }


def threshold_table(val: pd.DataFrame, p: np.ndarray) -> pd.DataFrame:
    rows = []
    for threshold in np.arange(0.30, 0.76, 0.025):
        part = val[p >= threshold]
        m = metrics(part)
        m["threshold"] = round(float(threshold), 3)
        rows.append(m)
    return pd.DataFrame(rows)


def export_mqh(model: MLP, mean: np.ndarray, std: np.ndarray, threshold: float, path: Path) -> None:
    lines = [
        "//+------------------------------------------------------------------+",
        "//| Generated by research/train_meta.py. Do not edit by hand.        |",
        f"//| Generated: {datetime.now(timezone.utc).isoformat()}",
        "//+------------------------------------------------------------------+",
        "#property strict",
        f"#define SOV_ML_FEATURES {len(mean)}",
        f"#define SOV_ML_HIDDEN {model.n_hidden}",
        f"#define SOV_ML_DEFAULT_THRESHOLD {threshold:.6f}",
        "",
        "const double SOV_ML_MEAN[SOV_ML_FEATURES] = {",
    ]
    lines.append("   " + ", ".join(f"{v:.9f}" for v in mean))
    lines.append("};")
    lines.append("const double SOV_ML_STD[SOV_ML_FEATURES] = {")
    lines.append("   " + ", ".join(f"{v:.9f}" for v in std))
    lines.append("};")
    lines.append("const double SOV_ML_W1[SOV_ML_HIDDEN][SOV_ML_FEATURES] = {")
    for row in model.W1:
        lines.append("   {" + ", ".join(f"{v:.9f}" for v in row) + "},")
    lines.append("};")
    lines.append("const double SOV_ML_B1[SOV_ML_HIDDEN] = {")
    lines.append("   " + ", ".join(f"{v:.9f}" for v in model.b1))
    lines.append("};")
    lines.append("const double SOV_ML_W2[SOV_ML_HIDDEN] = {")
    lines.append("   " + ", ".join(f"{v:.9f}" for v in model.W2))
    lines.append("};")
    lines.append(f"const double SOV_ML_B2 = {model.b2:.9f};")
    lines.append("")
    lines.append("double SovereignMLPredict(const double &raw[])")
    lines.append("{")
    lines.append("   double x[SOV_ML_FEATURES];")
    lines.append("   for(int i = 0; i < SOV_ML_FEATURES; i++)")
    lines.append("   {")
    lines.append("      double z = (raw[i] - SOV_ML_MEAN[i]) / (SOV_ML_STD[i] + 1e-9);")
    lines.append("      if(z > 5.0) z = 5.0;")
    lines.append("      if(z < -5.0) z = -5.0;")
    lines.append("      x[i] = z;")
    lines.append("   }")
    lines.append("   double h[SOV_ML_HIDDEN];")
    lines.append("   for(int j = 0; j < SOV_ML_HIDDEN; j++)")
    lines.append("   {")
    lines.append("      double z = SOV_ML_B1[j];")
    lines.append("      for(int i = 0; i < SOV_ML_FEATURES; i++)")
    lines.append("         z += SOV_ML_W1[j][i] * x[i];")
    lines.append("      h[j] = (z > 0.0) ? z : 0.0;")
    lines.append("   }")
    lines.append("   double out = SOV_ML_B2;")
    lines.append("   for(int j = 0; j < SOV_ML_HIDDEN; j++)")
    lines.append("      out += SOV_ML_W2[j] * h[j];")
    lines.append("   out = 1.0 / (1.0 + MathExp(-MathMax(-40.0, MathMin(40.0, out))));")
    lines.append("   return out;")
    lines.append("}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Sovereign meta-label MLP")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--hidden", type=int, default=24)
    parser.add_argument("--epochs", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--train-end", default="2026-05-01")
    parser.add_argument("--val-end", default="2026-07-01")
    args = parser.parse_args()

    df = core.load_data(args.timeframe)
    cfg = candidates.CandidateConfig()
    cand = candidates.generate(df, cfg)
    if cand.empty:
        print("No candidates generated.")
        return 1
    cand["signal_time"] = pd.to_datetime(cand["signal_time"], utc=True)
    cand.to_csv(MODEL_DIR / f"candidates_{args.timeframe}.csv", index=False)
    print(f"candidates={len(cand)}  win={cand['label'].mean() * 100:.1f}%  avgR={cand['r_multiple'].mean():.3f}")
    print(cand.groupby(["mode", "side"]).agg(n=("label", "size"), win=("label", "mean"), avgR=("r_multiple", "mean")).to_string())

    train = cand[cand["signal_time"] < pd.Timestamp(args.train_end, tz="UTC")]
    val = cand[
        (cand["signal_time"] >= pd.Timestamp(args.train_end, tz="UTC"))
        & (cand["signal_time"] < pd.Timestamp(args.val_end, tz="UTC"))
    ]
    test = cand[cand["signal_time"] >= pd.Timestamp(args.val_end, tz="UTC")]
    print(f"\nsplits: train={len(train)} val={len(val)} test={len(test)}")
    if min(len(train), len(val), len(test)) < 50:
        print("Not enough samples in a split.")
        return 1

    X_train = candidates.feature_matrix(train)
    X_val = candidates.feature_matrix(val)
    X_test = candidates.feature_matrix(test)
    y_train = train["label"].to_numpy(dtype=float)
    y_val = val["label"].to_numpy(dtype=float)
    y_test = test["label"].to_numpy(dtype=float)

    mean = X_train.mean(axis=0)
    std = X_train.std(axis=0)
    std[std < 1e-9] = 1.0

    def norm(X: np.ndarray) -> np.ndarray:
        Z = (X - mean) / std
        return np.clip(Z, -5, 5)

    Xtr, Xva, Xte = norm(X_train), norm(X_val), norm(X_test)
    model = MLP(Xtr.shape[1], args.hidden, args.seed)
    info = model.fit(Xtr, y_train, Xva, y_val, epochs=args.epochs, seed=args.seed)
    p_val = model.predict(Xva)
    p_test = model.predict(Xte)
    print(f"model: epochs={info['epochs']} val_loss={info['val_loss']:.4f}")
    print(f"AUC val={auc_score(y_val, p_val):.3f} test={auc_score(y_test, p_test):.3f}")

    base_train, base_val, base_test = metrics(train), metrics(val), metrics(test)
    print(f"baseline train={base_train}")
    print(f"baseline val  ={base_val}")
    print(f"baseline test ={base_test}")

    table = threshold_table(val, p_val)
    # Choose a threshold that improves validation total R with enough trades.
    eligible = table[(table["n"] >= 25) & (table["avg_r"] > 0)]
    if eligible.empty:
        chosen = table.sort_values("total_r", ascending=False).iloc[0]
    else:
        chosen = eligible.sort_values("total_r", ascending=False).iloc[0]
    threshold = float(chosen["threshold"])
    print("\nValidation thresholds:")
    print(table.to_string(index=False))
    print(f"\nchosen threshold={threshold:.3f}")

    for name, part, p in (("train", train, model.predict(Xtr)), ("val", val, p_val), ("test", test, p_test)):
        filtered = part[p >= threshold]
        print(f"{name}: all={metrics(part)} filtered={metrics(filtered)}")

    export_mqh(model, mean, std, threshold, EA_INCLUDE / "SovereignMLModel.mqh")
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "timeframe": args.timeframe,
        "hidden": args.hidden,
        "features": candidates.FEATURE_NAMES,
        "mean": mean.tolist(),
        "std": std.tolist(),
        "W1": model.W1.tolist(),
        "b1": model.b1.tolist(),
        "W2": model.W2.tolist(),
        "b2": float(model.b2),
        "threshold": threshold,
        "train": base_train,
        "val": base_val,
        "test": base_test,
        "filtered_test": metrics(test[p_test >= threshold]),
    }
    (MODEL_DIR / "meta_model.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nExported {EA_INCLUDE / 'SovereignMLModel.mqh'}")
    print(f"Exported {MODEL_DIR / 'meta_model.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
