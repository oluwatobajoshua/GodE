import pandas as pd
import numpy as np
from pathlib import Path

RES = Path("C:/xampp/htdocs/GodE/research/results")
d = pd.read_csv(RES / "edge_combine_monthly.csv")
d["date"] = pd.to_datetime(d["date"])
d = d.set_index("date").sort_index()

n = len(d)
cut = int(n * 0.8)

def metrics(r):
    r = r.dropna()
    if len(r) == 0:
        return -1, 0, 0, -1
    eq = (1 + r).cumprod()
    cagr = eq.iloc[-1] ** (12 / len(r)) - 1
    peak = np.maximum.accumulate(eq)
    sd = r.std()
    sharpe = r.mean() / sd * np.sqrt(12) if sd > 0 else 0
    dd = (eq / peak - 1).min()
    return cagr * 100, sharpe, dd * 100, (eq.iloc[-1] - 1) * 100

print("=== Robustness: different momentum lookbacks for regime switch ===")
print(f"{'lookback':>10}{'D+V Sharpe':>12}{'OOS Sharpe':>12}{'Full Sharpe':>12}{'OOS maxDD':>10}")
print("-" * 58)

for lb in [1, 2, 3, 6, 9, 12]:
    # shift(1): the weight for month t may only use returns known before t
    d[f"carry_mom{lb}"] = d["carry"].rolling(lb).sum().shift(1)
    d[f"w_carry_{lb}"] = np.where(d[f"carry_mom{lb}"] > 0, 0.5, 0.0)
    d[f"w_rsi_{lb}"] = 1 - d[f"w_carry_{lb}"]
    d[f"blend_{lb}"] = d[f"w_carry_{lb}"] * d["carry"] + d[f"w_rsi_{lb}"] * d["rsi_band"]

    c1, s1, dd1, t1 = metrics(d[f"blend_{lb}"].iloc[:cut])
    c2, s2, dd2, t2 = metrics(d[f"blend_{lb}"].iloc[cut:])
    c3, s3, dd3, t3 = metrics(d[f"blend_{lb}"])

    print(f"{lb:>10} {s1:>12.2f} {s2:>12.2f} {s3:>12.2f} {dd2:>10.1f}%")

print()
print("=== Robustness: different carry weights when momentum positive ===")
print(f"{'weight':>10}{'D+V Sharpe':>12}{'OOS Sharpe':>12}{'Full Sharpe':>12}{'OOS maxDD':>10}")
print("-" * 58)

for w in [0.0, 0.25, 0.5, 0.75, 1.0]:
    d[f"blend_w{w}"] = np.where(d["carry_mom3"] > 0, w * d["carry"] + (1 - w) * d["rsi_band"], d["rsi_band"])
    c1, s1, dd1, t1 = metrics(d[f"blend_w{w}"].iloc[:cut])
    c2, s2, dd2, t2 = metrics(d[f"blend_w{w}"].iloc[cut:])
    c3, s3, dd3, t3 = metrics(d[f"blend_w{w}"])

    print(f"{w:>10.2f} {s1:>12.2f} {s2:>12.2f} {s3:>12.2f} {dd2:>10.1f}%")

print()
print("Baseline for reference:")
c1, s1, dd1, t1 = metrics(d["carry"].iloc[:cut])
c2, s2, dd2, t2 = metrics(d["carry"].iloc[cut:])
print(f"  carry alone    OOS Sharpe: {s2:.2f}  OOS maxDD: {dd2:.1f}%")
c1, s1, dd1, t1 = metrics(d["rsi_band"].iloc[:cut])
c2, s2, dd2, t2 = metrics(d["rsi_band"].iloc[cut:])
print(f"  RSI band alone OOS Sharpe: {s2:.2f}  OOS maxDD: {dd2:.1f}%")
