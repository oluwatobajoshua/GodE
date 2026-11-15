#!/usr/bin/env python3
"""Neural ODE backbone — explicit Euler on CPU (no torchdiffeq / pip).

dh/dt = tanh(W_h h + W_x x(t))
Irregular dt[] between observations supported.
"""
from __future__ import annotations

import numpy as np


class NeuralOdeEuler:
    def __init__(self, hidden_dim: int, feat_dim: int, seed: int = 42):
        rng = np.random.default_rng(seed)
        self.W_h = rng.normal(0, 0.1, (hidden_dim, hidden_dim))
        self.W_x = rng.normal(0, 0.1, (hidden_dim, feat_dim))
        self.hidden_dim = hidden_dim
        self.W_alpha = rng.normal(0, 0.05, hidden_dim)  # head A
        self.W_tox = rng.normal(0, 0.05, hidden_dim)  # head B

    def derivative(self, h: np.ndarray, x: np.ndarray) -> np.ndarray:
        return np.tanh(self.W_h @ h + self.W_x @ x)

    def integrate(
        self,
        features: np.ndarray,
        dt: np.ndarray,
    ) -> np.ndarray:
        """features (T, F), dt (T,) seconds or bar gaps; returns hidden path (T, H)."""
        t_steps = len(features)
        h = np.zeros(self.hidden_dim, dtype=np.float64)
        path = np.zeros((t_steps, self.hidden_dim), dtype=np.float64)
        for i in range(t_steps):
            if i > 0:
                h = h + dt[i] * self.derivative(h, features[i - 1])
                h = np.clip(h, -5.0, 5.0)
            path[i] = h
        return path

    def head_alpha(self, h: np.ndarray) -> float:
        h = np.clip(h, -5.0, 5.0)
        return float(np.tanh(self.W_alpha @ h) * 1e-3)

    def head_toxicity(self, h: np.ndarray) -> float:
        h = np.clip(h, -5.0, 5.0)
        z = float(self.W_tox @ h)
        return float(1.0 / (1.0 + np.exp(-np.clip(z, -20.0, 20.0))))


def huber(x: np.ndarray, delta: float = 1.0) -> np.ndarray:
    ax = np.abs(x)
    return np.where(ax <= delta, 0.5 * x * x, delta * (ax - 0.5 * delta))

def huber_loss(pred: np.ndarray, target: np.ndarray, delta: float = 1.0) -> float:
    return float(np.mean(huber(pred - target, delta)))
