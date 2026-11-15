"""MT5 M5 spread → price cost (5-digit FX)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def point_size(sym: str) -> float:
    return 0.001 if "JPY" in sym.upper() else 0.00001


def pip_size(sym: str) -> float:
    return 0.01 if "JPY" in sym.upper() else 0.0001


def spread_points(row: pd.Series, default_pts: float = 10.0) -> float:
    if "spread" in row.index and pd.notna(row["spread"]):
        return float(row["spread"])
    return default_pts


def round_trip_cost_price(sym: str, row: pd.Series, *, slippage_pts: float = 1.0) -> float:
    """Half spread each way + slippage (points) in price units."""
    pt = point_size(sym)
    sp = spread_points(row)
    return (sp + slippage_pts) * pt
