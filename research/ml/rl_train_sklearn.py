#!/usr/bin/env python3
"""RL research without Gymnasium/SB3 — scikit-learn policy on FxTradingEnvCore.

Uses only packages in requirements.txt (numpy, pandas, sklearn).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import SGDClassifier

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from fx_env_core import FxEnvConfig, make_env_from_csv  # noqa: E402

RESULTS = ROOT / "research" / "results"


def collect_random_episode(env, steps: int = 500) -> tuple[np.ndarray, np.ndarray]:
    obs, _ = env.reset()
    xs, ys = [], []
    for _ in range(steps):
        a = np.random.randint(0, 3)
        obs2, r, term, trunc, _ = env.step(a)
        label = 1 if r > 0 else 0
        xs.append(obs)
        ys.append(label)
        obs = obs2
        if term or trunc:
            obs, _ = env.reset()
    return np.asarray(xs, dtype=np.float32), np.asarray(ys, dtype=np.int64)


def main() -> int:
    csv = ROOT / "research" / "data" / "histdata" / "GBPUSD_H1.csv"
    if not csv.exists():
        csv = ROOT / "research" / "data" / "GBPUSD_H1.csv"
    env = make_env_from_csv(csv, FxEnvConfig(max_episode_steps=2000))
    x, y = collect_random_episode(env, steps=3000)
    clf = SGDClassifier(loss="log_loss", max_iter=200, tol=1e-3)
    clf.fit(x, y)
    acc = float((clf.predict(x) == y).mean())

    # greedy policy eval
    obs, _ = env.reset()
    equity_path = [env.cfg.initial_balance]
    for _ in range(1000):
        # map binary clf to flat-ish policy: use predict_proba threshold
        proba = clf.predict_proba(obs.reshape(1, -1))[0, 1] if hasattr(clf, "predict_proba") else 0.5
        action = 1 if proba > 0.55 else (2 if proba < 0.45 else 0)
        obs, _, term, trunc, info = env.step(action)
        equity_path.append(info["equity"])
        if term or trunc:
            obs, _ = env.reset()

    out = {
        "backend": "sklearn_sgd_on_fx_env_core",
        "train_acc_proxy": round(acc, 4),
        "final_equity_sample": round(equity_path[-1], 2),
        "note": "Research smoke — not promotion. No pip.exe / gymnasium required.",
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "rl_sklearn_manifest.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
