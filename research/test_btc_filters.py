#!/usr/bin/env python3
"""Test filters to avoid negative months on BTCUSD H1 RSI band."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import numpy as np

DATA = Path(__file__).resolve().parent / "data"
df = pd.read_csv(DATA / "BTCUSD_H1.csv")
df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
df = df.sort_values("time").reset_index(drop=True)

from ema_cross_harvest import EMA, RSI_P, ema, wilder_atr, wilder_rsi
df["ema"] = ema(df["close"], EMA)
df["rsi"] = wilder_rsi(df["close"], 14)
df["atr"] = wilder_atr(df, 14)
df = df.dropna(subset=["ema", "rsi", "atr"]).reset_index(drop=True)

close = df["close"].to_numpy(float)
rsi = df["rsi"].to_numpy(float)
e = df["ema"].to_numpy(float)
atr = df["atr"].to_numpy(float)
spread = df["spread"].astype(float).to_numpy()
time = df["time"]
n = len(df)
point = 0.00001
slope3 = np.zeros(n)
slope3[3:] = (e[3:] - e[:-3]) / np.maximum(atr[3:], 1e-12)
trend = np.where(slope3 > 0.10, 1, np.where(slope3 < -0.10, -1, 0))
mid = np.full(n, 60.0)
hi = np.full(n, 80.0)
lo = np.full(n, 40.0)

spread_eff = np.maximum(spread, 1.0)
cost_side = (spread_eff / 2.0 + 1.0 + 1.75) * point
q = pd.Series(spread).rolling(500, min_periods=100).quantile(0.90)
spread_ok = (spread <= q.to_numpy()) | ~np.isfinite(q.to_numpy())
atr_pct = pd.Series(atr).rolling(500, min_periods=100).rank(pct=True).to_numpy()


def run(atr_max=0.80, slope_min=0.10, loss_cooldown=0, monthly_dd_stop=None):
    pos = np.zeros(n, dtype=int)
    state = 0
    entry_bar = -10 ** 9
    exit_bar = -10 ** 9
    consec_losses = 0
    cur_month = None
    month_eq = 1.0
    month_halted = False
    eq = 1.0

    for t in range(1, n):
        r, rp = rsi[t], rsi[t - 1]
        if not (np.isfinite(r) and np.isfinite(mid[t])):
            pos[t] = state
            continue

        # Monthly DD kill switch
        tm = time[t].strftime("%Y-%m")
        if cur_month != tm:
            cur_month = tm
            month_eq = 1.0
            month_halted = False
        if monthly_dd_stop is not None and month_eq < monthly_dd_stop:
            month_halted = True

        if state != 0:
            bar_ret = close[t] / close[t - 1] - 1.0
            eq *= (1 + bar_ret * state)
            month_eq *= (1 + bar_ret * state)

        if state > 0:
            held = t - entry_bar
            if trend[t] < 0:
                state = 0
                exit_bar = t
            elif r > hi[t] and held >= 24:
                state = 0
                exit_bar = t
        elif state < 0:
            held = t - entry_bar
            if trend[t] > 0:
                state = 0
                exit_bar = t
            elif r < lo[t] and held >= 24:
                state = 0
                exit_bar = t
        else:
            ok_spread = spread_ok[t]
            ok_atr = atr_pct[t] <= atr_max
            ok_slope = abs(slope3[t]) >= slope_min
            lc_ok = (consec_losses < loss_cooldown) if loss_cooldown > 0 else True
            ok_month = not month_halted

            if t - exit_bar >= 40 and ok_spread and ok_atr and ok_slope and lc_ok and ok_month:
                if trend[t] > 0 and rp <= mid[t] and r > mid[t] + 1.0:
                    state = 1
                    entry_bar = t
                elif trend[t] < 0 and rp >= mid[t] and r < mid[t] - 1.0:
                    state = -1
                    entry_bar = t
        pos[t] = state

    ret = np.zeros(n)
    ret[1:] = pos[:-1] * (close[1:] / close[:-1] - 1.0)
    turn = np.abs(np.diff(pos, prepend=0))
    ret -= turn * (cost_side / close)
    eq_curve = np.cumprod(1 + ret)
    peak = np.maximum.accumulate(eq_curve)
    dd = (eq_curve / peak - 1).min()
    total = eq_curve[-1] - 1

    # Monthly stats
    s = pd.Series(ret, index=pd.DatetimeIndex(time))
    monthly = s.resample("ME").apply(lambda x: (1 + x).prod() - 1) * 100
    neg_months = int((monthly < 0).sum())
    pos_months = int((monthly > 0).sum())
    flat_months = int((monthly == 0).sum())

    return total * 100, dd * 100, neg_months, pos_months, flat_months, len(monthly)


configs = [
    ("ATR p80 (baseline)", 0.80, 0.10, 0, None),
    ("ATR p60", 0.60, 0.10, 0, None),
    ("ATR p50", 0.50, 0.10, 0, None),
    ("Slope > 0.20", 0.80, 0.20, 0, None),
    ("LossCD=2", 0.80, 0.10, 2, None),
    ("LossCD=3", 0.80, 0.10, 3, None),
    ("MoDD -5%", 0.80, 0.10, 0, -0.05),
    ("MoDD -3%", 0.80, 0.10, 0, -0.03),
    ("Combined strict", 0.60, 0.20, 2, -0.05),
    ("Very strict", 0.50, 0.20, 3, -0.03),
]

print("BTCUSD H1 RSI band + ATR gate - filter comparison for avoiding negative months")
print("=" * 100)
print(f"{'Config':<22} {'ATR':>5} {'Slope':>6} {'LossCD':>7} {'MoDD':>9} {'FullRet':>10} {'maxDD':>8} {'NegMo':>6} {'PosMo':>6} {'Flat':>6}")
print("-" * 100)

for name, atr_max, slope_min, lc, mdd in configs:
    tot, dd, neg, pos, flat, nm = run(atr_max, slope_min, lc, mdd)
    print(f"{name:<22} {atr_max:>5.2f} {slope_min:>6.2f} {lc:>7} {str(mdd):>9} {tot:>+10.1f}% {dd:>8.1f}% {neg:>6} {pos:>6} {flat:>6}")

print()
print("Goal: minimize NegMo while keeping FullRet positive and maxDD acceptable")
print("Trade-off: stricter filters = fewer negative months but also lower returns")
