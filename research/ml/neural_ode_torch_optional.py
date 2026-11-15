#!/usr/bin/env python3
"""PyTorch + torchdiffeq Neural ODE — optional (requires IT-approved packages).

Do not run pip.exe from venv under ASR. Import only when torchdiffeq is pre-installed.
"""
from __future__ import annotations


def build_torch_ode_if_available(hidden: int, feat: int):
    try:
        import torch
        import torch.nn as nn
        from torchdiffeq import odeint
    except ImportError as exc:
        raise RuntimeError(
            "torch/torchdiffeq not installed — use neural_ode_euler.py "
            "or ask IT to allowlist python.exe and deploy wheels offline."
        ) from exc

    class ODEFunc(nn.Module):
        def __init__(self):
            super().__init__()
            self.lin_h = nn.Linear(hidden, hidden)
            self.lin_x = nn.Linear(feat, hidden)

        def forward(self, t, h, x):
            return torch.tanh(self.lin_h(h) + self.lin_x(x))

    class MicroODE(nn.Module):
        def __init__(self):
            super().__init__()
            self.func = ODEFunc()
            self.head_alpha = nn.Linear(hidden, 1)
            self.head_tox = nn.Linear(hidden, 1)

    return MicroODE(), odeint
