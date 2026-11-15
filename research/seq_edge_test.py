#!/usr/bin/env python3
"""Does a sequence model see structure that pointwise features cannot?

`research/ml_edge_test.py` gives every bar to the model on its own: 20 hand-made
features, no memory. It found a real but tiny signal (~0.52 AUC) that dies by 20
bars and loses money net of spread. A fair objection remains, though:

    "You only ever showed the model a snapshot. What about the SHAPE of the last
     32 bars - a sequence? Maybe the pattern is in the arrangement, not the level."

That is a genuinely different hypothesis, and it is the one thing the tabular test
could not answer. This script answers it with a sequence model.

MODELS
  majority   base rate                                  (AUC 0.500 by construction)
  linear     logistic regression on the last bar only    (the honest benchmark)
  tcn        dilated temporal convolutional net          (sees 32 bars)
  lstm       recurrent net                               (sees 32 bars)

The linear benchmark deliberately sees ONLY the most recent bar, so the comparison
is "does sequence memory add anything over the current snapshot" - not "does a
bigger model fit harder".

THE SAME THREE TRAPS ARE CLOSED
  1. Overlapping labels -> purge h bars from the end of every training window and
     embargo the first h bars after every test window.
  2. Scaler fitted inside the training window only.
  3. A shuffled-label control on every run. If it scores above 0.5 out of sample
     the pipeline leaks and nothing else in the output counts.

The neural nets also get an inner validation slice (the last 15% of the training
window, purged by h) for early stopping, so they cannot peek at the test window.

Usage
    .\\.venv\\Scripts\\python.exe research/seq_edge_test.py --symbol EURUSD --arch tcn
    .\\.venv\\Scripts\\python.exe research/seq_edge_test.py --symbol EURUSD --arch lstm --h 5
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research"))

from ema_cross_harvest import ATR_P, EMA, RSI_P, ema, wilder_atr, wilder_rsi  # noqa: E402

DATA = ROOT / "research" / "data" / "mt5"
RESULTS = ROOT / "research" / "results"

warnings.filterwarnings("ignore")

try:
    import torch
    import torch.nn as nn
    HAS_TORCH = True
except Exception:  # pragma: no cover
    HAS_TORCH = False


def build_channels(df: pd.DataFrame) -> np.ndarray:
    """(n, K) causal channels. Every value at row t uses bars <= t only."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    atr = wilder_atr(df, ATR_P).replace(0.0, np.nan)
    e21 = ema(c, EMA)
    e50 = ema(c, 50)
    rsi = wilder_rsi(c, RSI_P)

    ch = pd.DataFrame(index=df.index)
    ch["ret1"] = c.diff() / atr
    ch["body"] = (c - o) / atr
    ch["range"] = (h - l) / atr
    ch["rsi"] = rsi / 100.0 - 0.5
    ch["rsi_d1"] = rsi.diff() / 10.0
    ch["dist21"] = (c - e21) / atr
    ch["dist50"] = (c - e50) / atr
    ch["slope3"] = (e21 - e21.shift(3)) / atr
    spread = df["spread"].astype(float)
    med = spread.rolling(500, min_periods=50).median().clip(lower=1.0)
    ch["spread_rel"] = spread / med - 1.0
    ch["above50"] = (c > e50).astype(float) - 0.5
    return ch


class TCN(nn.Module):
    """Small dilated causal conv stack. Receptive field 1+2*(1+2+4+8)=31 bars."""

    def __init__(self, k: int, width: int = 32, p: float = 0.2):
        super().__init__()
        layers = []
        for d in (1, 2, 4, 8):
            layers += [nn.Conv1d(k if d == 1 else width, width, 3,
                                 padding=d, dilation=d),
                       nn.ReLU(), nn.Dropout(p)]
        self.body = nn.Sequential(*layers)
        self.head = nn.Linear(width, 1)

    def forward(self, x):            # x: (B, W, K)
        z = self.body(x.transpose(1, 2))   # (B, width, W)
        return self.head(z[:, :, -1]).squeeze(-1)


class LSTMNet(nn.Module):
    def __init__(self, k: int, width: int = 32, p: float = 0.2):
        super().__init__()
        self.rnn = nn.LSTM(k, width, num_layers=1, batch_first=True)
        self.drop = nn.Dropout(p)
        self.head = nn.Linear(width, 1)

    def forward(self, x):
        z, _ = self.rnn(x)
        return self.head(self.drop(z[:, -1, :])).squeeze(-1)


def fit_net(arch, Xtr, ytr, Xva, yva, seed=0, epochs=8, batch=512, lr=1e-3):
    torch.manual_seed(seed)
    k = Xtr.shape[2]
    net = TCN(k) if arch == "tcn" else LSTMNet(k)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=1e-4)
    lossf = nn.BCEWithLogitsLoss()

    xt = torch.from_numpy(Xtr.astype(np.float32))
    yt = torch.from_numpy(ytr.astype(np.float32))
    xv = torch.from_numpy(Xva.astype(np.float32))
    yv = torch.from_numpy(yva.astype(np.float32))

    best, best_state, bad = -1.0, None, 0
    n = len(xt)
    g = torch.Generator().manual_seed(seed)
    for _ in range(epochs):
        net.train()
        perm = torch.randperm(n, generator=g)
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            opt.zero_grad()
            loss = lossf(net(xt[idx]), yt[idx])
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), 1.0)
            opt.step()
        net.eval()
        with torch.no_grad():
            auc = roc_auc_score(yv.numpy(), net(xv).numpy())
        if auc > best + 1e-4:
            best, bad = auc, 0
            best_state = {k2: v.detach().clone() for k2, v in net.state_dict().items()}
        else:
            bad += 1
            if bad >= 2:
                break
    if best_state is not None:
        net.load_state_dict(best_state)
    net.eval()
    return net


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--h", type=int, default=5)
    ap.add_argument("--window", type=int, default=32)
    ap.add_argument("--train", type=int, default=20000)
    ap.add_argument("--test", type=int, default=3000)
    ap.add_argument("--step", type=int, default=6000)
    ap.add_argument("--arch", default="tcn", choices=["tcn", "lstm", "both"])
    ap.add_argument("--conf", type=float, default=0.05)
    ap.add_argument("--comm-pts", type=float, default=7.0)
    ap.add_argument("--slip-pts", type=float, default=1.0)
    ap.add_argument("--floor-pts", type=float, default=1.0)
    args = ap.parse_args()

    if not HAS_TORCH:
        raise SystemExit("torch not installed - python -m pip install torch "
                         "--index-url https://download.pytorch.org/whl/cpu")
    torch.set_num_threads(4)

    sym, tf, h, W = args.symbol.upper(), args.tf.upper(), args.h, args.window
    path = DATA / f"{sym}_{tf}.csv"
    if not path.exists():
        raise SystemExit(f"missing data: {path}")

    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    df = df.sort_values("time").reset_index(drop=True)

    ch = build_channels(df)
    fwd = df["close"].shift(-h) / df["close"] - 1.0
    y = (fwd > 0).astype(int)
    ok = ch.notna().all(axis=1) & fwd.notna() & np.isfinite(fwd)
    ch = ch[ok].reset_index(drop=True)
    y = y[ok].reset_index(drop=True)
    close = df["close"][ok].reset_index(drop=True)
    time = df["time"][ok].reset_index(drop=True)
    spread = df["spread"][ok].astype(float).reset_index(drop=True)

    point = 0.001 if sym.endswith("JPY") else 0.00001
    cost_side = (np.maximum(spread.to_numpy(), args.floor_pts) / 2.0
                 + args.slip_pts + args.comm_pts / 2.0) * point

    C = ch.to_numpy(np.float32)
    n, K = C.shape
    yv = y.to_numpy()

    #--- window ending at bar t, zero padded before the first full window
    Wn = np.zeros((n, W, K), dtype=np.float32)
    for j in range(W):
        Wn[W - 1:, W - 1 - j, :] = C[j:n - (W - 1) + j]

    folds = []
    start = args.train
    while start + args.test <= n:
        tr_lo, tr_hi = start - args.train, start - h          # purge
        te_lo, te_hi = start, min(start + args.test, n)
        if tr_hi - tr_lo > 2000 and te_hi - te_lo > 200:
            folds.append((tr_lo, tr_hi, te_lo, te_hi))
        start += args.step
    if not folds:
        raise SystemExit("not enough bars for one fold")

    print(f"=== does sequence memory find direction?  {sym} {tf} ===")
    print(f"bars {n}  {time.iloc[0]:%Y-%m-%d} -> {time.iloc[-1]:%Y-%m-%d}   "
          f"horizon {h}   window {W} bars   channels {K}")
    print(f"base rate P(up) {100 * yv.mean():.1f}%   folds {len(folds)}")
    print(f"walk-forward: train {args.train}, test {args.test}, step {args.step}, "
          f"purge+embargo {h} bars")
    print("")

    def predict(name, shuffle_labels=False):
        preds = np.full(n, np.nan)
        rng = np.random.default_rng(12345)
        for tr_lo, tr_hi, te_lo, te_hi in folds:
            Xtr, ytr = Wn[tr_lo:tr_hi], yv[tr_lo:tr_hi].copy()
            if shuffle_labels:
                ytr = rng.permutation(ytr)

            if name == "linear":
                #--- the benchmark sees only the newest bar, no memory
                sc = StandardScaler().fit(C[tr_lo:tr_hi])
                m = LogisticRegression(max_iter=500, C=0.1)
                m.fit(sc.transform(C[tr_lo:tr_hi]), ytr)
                preds[te_lo:te_hi] = m.predict_proba(
                    sc.transform(C[te_lo:te_hi]))[:, 1]
                continue

            if name == "majority":
                preds[te_lo:te_hi] = float(ytr.mean())
                continue

            #--- inner validation slice for early stopping, purged by h
            vhi = tr_hi - h
            vlo = max(tr_lo + 1, vhi - int(0.15 * (tr_hi - tr_lo)))
            Xt, yt = Xtr[:vlo - tr_lo], ytr[:vlo - tr_lo]
            Xva, yva = Xtr[vlo - tr_lo:vhi - tr_lo], ytr[vlo - tr_lo:vhi - tr_lo]
            if len(Xva) < 100 or len(np.unique(yva)) < 2:
                Xva, yva = Xt[-1000:], yt[-1000:]

            net = fit_net(name, Xt, yt, Xva, yva)
            with torch.no_grad():
                z = net(torch.from_numpy(Wn[te_lo:te_hi])).numpy()
            preds[te_lo:te_hi] = 1.0 / (1.0 + np.exp(-z))
        return preds

    def evaluate(preds):
        m = np.isfinite(preds)
        yt, p = yv[m], preds[m]
        auc = roc_auc_score(yt, p) if len(np.unique(yt)) > 1 else 0.5
        acc = float(((p > 0.5) == yt).mean())
        pos = np.where(np.abs(p - 0.5) > args.conf, np.sign(p - 0.5), 0.0)
        pos_full = np.zeros(n)
        pos_full[m] = pos
        c = close.to_numpy()
        ret = np.zeros(n)
        ret[1:] = pos_full[:-1] * (c[1:] / c[:-1] - 1.0)
        ret -= np.abs(np.diff(pos_full, prepend=0.0)) * (cost_side / c)
        r = ret[m]
        return auc, acc, float(np.cumprod(1.0 + r)[-1] - 1.0) * 100, int((pos != 0).sum())

    archs = ["tcn", "lstm"] if args.arch == "both" else [args.arch]
    order = ["majority", "linear"] + archs

    print(f"{'model':<12}{'OOS AUC':>9}{'accuracy':>10}{'net return':>12}"
          f"{'bars traded':>13}")
    print("-" * 58)
    out = []
    for name in order:
        auc, acc, total, traded = evaluate(predict(name))
        out.append((name, auc, acc, total, traded, False))
        print(f"{name:<12}{auc:>9.4f}{100 * acc:>9.2f}%{total:>11.2f}%{traded:>13}")
    print("")

    print("--- leak control: same pipeline, training labels SHUFFLED ---")
    print("    (if this scores above 0.5 OOS the pipeline leaks and nothing counts)")
    print(f"{'model':<12}{'OOS AUC':>9}{'net return':>12}")
    print("-" * 33)
    for name in order[1:]:
        auc, acc, total, traded = evaluate(predict(name, shuffle_labels=True))
        out.append((name, auc, acc, total, traded, True))
        print(f"{name:<12}{auc:>9.4f}{total:>11.2f}%")
    print("")

    lin = [r for r in out if r[0] == "linear" and not r[5]][0]
    best = max((r for r in out if not r[5]), key=lambda r: r[1])
    print(f"best real model: {best[0]}  AUC {best[1]:.4f}  net {best[3]:+.2f}%")
    print(f"linear benchmark (last bar only): AUC {lin[1]:.4f}  net {lin[3]:+.2f}%")
    for name in archs:
        seq = [r for r in out if r[0] == name and not r[5]][0]
        d = seq[1] - lin[1]
        print(f"  {name}: AUC {seq[1]:.4f} ({d:+.4f} vs linear), "
              f"net {seq[3]:+.2f}%  -> sequence memory "
              f"{'helps' if d > 0.005 else 'adds nothing'}")
    if all(r[3] < 0 for r in out if not r[5] and r[0] != "majority"):
        print("-> every model loses money net of spread: no tradable direction.")
    print("")

    RESULTS.mkdir(parents=True, exist_ok=True)
    tag = f"seq_edge_test_{sym}_{tf}_h{h}"
    pd.DataFrame(out, columns=["model", "auc", "accuracy", "net_pct",
                               "bars_traded", "shuffled"]).to_csv(
        RESULTS / f"{tag}.csv", index=False)
    print(f"wrote {RESULTS / f'{tag}.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
