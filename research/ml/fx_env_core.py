#!/usr/bin/env python3
"""Pure NumPy FX trading environment — no Gymnasium / pip / subprocess.

Gymnasium-compatible tuple API: reset() -> obs, info; step() -> obs, r, term, trunc, info.
Use this when ASR blocks venv\\Scripts\\pip.exe or third-party installers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PIP_SIZE_FX = 0.0001


@dataclass(frozen=True)
class FxEnvConfig:
    initial_balance: float = 100_000.0
    pip_cost: float = 10.0
    spread_pips: float = 1.2
    commission_round_turn: float = 7.0
    maintenance_margin_frac: float = 0.30
    vol_window: int = 24
    lot_size: float = 1.0
    pip_size: float = PIP_SIZE_FX
    max_episode_steps: int | None = None
    n_features: int = 8


def load_ohlcv_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.sort_values("time").drop_duplicates("time")
    for c in ("open", "high", "low", "close"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "tick_volume" not in df.columns:
        df["tick_volume"] = 0.0
    else:
        df["tick_volume"] = pd.to_numeric(df["tick_volume"], errors="coerce").fillna(0)
    if "spread" not in df.columns:
        df["spread"] = 0.0
    else:
        df["spread"] = pd.to_numeric(df["spread"], errors="coerce").fillna(0)
    return df.dropna(subset=["close"]).reset_index(drop=True)


def build_feature_matrix(df: pd.DataFrame, vol_window: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    c = df["close"].to_numpy(dtype=np.float64)
    o = df["open"].to_numpy(dtype=np.float64)
    h = df["high"].to_numpy(dtype=np.float64)
    l = df["low"].to_numpy(dtype=np.float64)
    v = df["tick_volume"].to_numpy(dtype=np.float64)
    sp = df["spread"].to_numpy(dtype=np.float64)

    log_ret = np.zeros_like(c)
    log_ret[1:] = np.log(c[1:] / c[:-1])
    w = max(vol_window, 2)
    sd = pd.Series(log_ret).rolling(w, min_periods=w).std().to_numpy()
    realized_vol = np.maximum(np.nan_to_num(sd, nan=1e-6), 1e-6)
    spread_pips = sp * 0.01
    spread_ratio = spread_pips / np.maximum(np.abs(log_ret) * 1e4, 1e-6)

    c_mean, c_std = np.nanmean(c), np.nanstd(c) + 1e-9
    feats = np.column_stack(
        [
            (o - c_mean) / c_std,
            (h - c_mean) / c_std,
            (l - c_mean) / c_std,
            (c - c_mean) / c_std,
            v,
            log_ret,
            realized_vol,
            spread_ratio,
        ]
    )
    valid = np.isfinite(feats).all(axis=1)
    valid[:w] = False
    return feats.astype(np.float32), np.where(valid)[0], c.astype(np.float64)


class FxTradingEnvCore:
    """Discrete 3-way FX sim: 0 flat, 1 long, 2 short."""

    def __init__(
        self,
        features: np.ndarray,
        closes: np.ndarray,
        valid_idx: np.ndarray,
        config: FxEnvConfig | None = None,
    ):
        self._x = features
        self._closes = closes
        self._valid = valid_idx
        self.cfg = config or FxEnvConfig()
        self._t = 0
        self._pos = 0
        self._entry_price = 0.0
        self._balance = self.cfg.initial_balance
        self._equity_prev = self.cfg.initial_balance
        self._step_count = 0
        self.n_features = int(features.shape[1])

    def _target_position(self, action: int) -> int:
        if action == 1:
            return 1
        if action == 2:
            return -1
        return 0

    def _transaction_cost(self) -> float:
        return self.cfg.spread_pips * self.cfg.pip_cost * self.cfg.lot_size + self.cfg.commission_round_turn

    def _mark_to_market(self, price: float) -> float:
        if self._pos == 0:
            return self._balance
        pips = (price - self._entry_price) / self.cfg.pip_size * float(self._pos)
        return self._balance + pips * self.cfg.pip_cost * self.cfg.lot_size

    def _obs(self) -> np.ndarray:
        return self._x[self._valid[self._t]]

    def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None) -> tuple[np.ndarray, dict]:
        if seed is not None:
            np.random.seed(seed)
        self._t = int(options.get("start", 0)) if options else 0
        self._pos = 0
        self._entry_price = 0.0
        self._balance = self.cfg.initial_balance
        self._equity_prev = self.cfg.initial_balance
        self._step_count = 0
        return self._obs(), {"balance": self._balance, "position": self._pos}

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        if self._t >= len(self._valid) - 1:
            return self._obs(), 0.0, False, True, {"reason": "data_end"}

        target = self._target_position(int(action))
        price = self._closes[self._valid[self._t]]
        if target != self._pos:
            self._balance -= self._transaction_cost()
            if self._pos != 0:
                pips = (price - self._entry_price) / self.cfg.pip_size * float(self._pos)
                self._balance += pips * self.cfg.pip_cost * self.cfg.lot_size
            self._pos = target
            self._entry_price = price if target != 0 else 0.0

        self._t += 1
        price_next = self._closes[self._valid[self._t]]
        equity = self._mark_to_market(price_next)
        step_lr = np.log(max(equity, 1e-9) / max(self._equity_prev, 1e-9))
        vol = float(self._x[self._valid[self._t], 6])
        reward = float(step_lr / vol)

        terminated = equity < self.cfg.maintenance_margin_frac * self.cfg.initial_balance
        if terminated:
            self._pos = 0
            self._entry_price = 0.0
            reward -= 5.0

        truncated = bool(self.cfg.max_episode_steps and self._step_count + 1 >= self.cfg.max_episode_steps)
        self._step_count += 1
        self._equity_prev = equity
        info = {
            "equity": equity,
            "balance": self._balance,
            "position": self._pos,
            "unrealized": equity - self._balance,
        }
        return self._obs(), reward, terminated, truncated, info


def make_env_from_csv(path: Path, config: FxEnvConfig | None = None) -> FxTradingEnvCore:
    df = load_ohlcv_csv(path)
    feats, valid, closes = build_feature_matrix(df, (config or FxEnvConfig()).vol_window)
    return FxTradingEnvCore(feats, closes, valid, config=config)


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    csv = root / "research" / "data" / "histdata" / "GBPUSD_H1.csv"
    if not csv.exists():
        csv = root / "research" / "data" / "GBPUSD_H1.csv"
    env = make_env_from_csv(csv)
    obs, _ = env.reset()
    for _ in range(5):
        obs, r, term, trunc, info = env.step(np.random.randint(0, 3))
        print(f"equity={info['equity']:.2f} reward={r:.4f}")
        if term or trunc:
            break
    print("[fx_env_core] smoke OK (no gymnasium)")
