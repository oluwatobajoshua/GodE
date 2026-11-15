#!/usr/bin/env python3
"""Gymnasium FX trading environment — Stable-Baselines3 (PPO) compatible.

Research only. Vectorized feature matrix; O(1) step (no Python loops in hot path).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces

    _HAS_GYM = True
except ImportError:
    gym = None  # type: ignore
    spaces = None  # type: ignore
    _HAS_GYM = False

from fx_env_core import (  # noqa: E402
    FxEnvConfig,
    FxTradingEnvCore,
    build_feature_matrix,
    load_ohlcv_csv,
    make_env_from_csv as _make_core,
)

PIP_SIZE_FX = 0.0001  # override per symbol via config


@dataclass(frozen=True)
class FxEnvConfig:
    initial_balance: float = 100_000.0
    pip_cost: float = 10.0  # account ccy per pip (1 lot notional proxy)
    spread_pips: float = 1.2
    commission_round_turn: float = 7.0  # account ccy per position flip
    maintenance_margin_frac: float = 0.30  # terminate below 30% of initial equity
    vol_window: int = 24
    lot_size: float = 1.0
    pip_size: float = PIP_SIZE_FX
    max_episode_steps: int | None = None


if _HAS_GYM:

    class FxTradingEnv(gym.Env):
        """Gymnasium wrapper around FxTradingEnvCore (optional — needs gymnasium installed)."""

        metadata = {"render_modes": ["human"]}

        def __init__(
            self,
            features: np.ndarray,
            closes: np.ndarray,
            valid_idx: np.ndarray,
            config: FxEnvConfig | None = None,
            render_mode: str | None = None,
        ):
            super().__init__()
            self._core = FxTradingEnvCore(features, closes, valid_idx, config)
            self.render_mode = render_mode
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf, shape=(features.shape[1],), dtype=np.float32
            )
            self.action_space = spaces.Discrete(3)

        def reset(self, *, seed: int | None = None, options: dict[str, Any] | None = None):
            super().reset(seed=seed)
            return self._core.reset(seed=seed, options=options)

        def step(self, action: int):
            return self._core.step(action)

        def render(self):
            if self.render_mode == "human":
                print(self._core._equity_prev)


def make_env_from_csv(path: Path, config: FxEnvConfig | None = None, **kwargs):
    if _HAS_GYM:
        df = load_ohlcv_csv(path)
        feats, valid, closes = build_feature_matrix(df, (config or FxEnvConfig()).vol_window)
        return FxTradingEnv(feats, closes, valid, config=config, **kwargs)
    return _make_core(path, config)


def train_ppo_demo(csv_path: Path, total_timesteps: int = 20_000) -> int:
    """Smoke train with Stable-Baselines3 PPO when installed."""
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.env_checker import check_env
    except ImportError:
        print("[fx_gym_env] install requirements-ml.txt for PPO")
        return 1
    env = make_env_from_csv(csv_path)
    check_env(env, warn=True)
    model = PPO("MlpPolicy", env, verbose=0, n_steps=512, batch_size=64)
    model.learn(total_timesteps=total_timesteps)
    return 0


if __name__ == "__main__":
    import sys

    if not _HAS_GYM:
        print("[fx_gym_env] gymnasium not installed — use research\\ml\\fx_env_core.py or run_ml_no_pip.ps1")
        raise SystemExit(1)
    root = Path(__file__).resolve().parents[2]
    csv = root / "research" / "data" / "histdata" / "GBPUSD_H1.csv"
    if not csv.exists():
        csv = root / "research" / "data" / "GBPUSD_H1.csv"
    env = make_env_from_csv(csv, render_mode="human")
    obs, info = env.reset()
    for _ in range(5):
        obs, r, term, trunc, info = env.step(env.action_space.sample())
        env.render()
        if term or trunc:
            break
    if "--ppo" in sys.argv:
        raise SystemExit(train_ppo_demo(csv))
    print("[fx_gym_env] smoke OK")
