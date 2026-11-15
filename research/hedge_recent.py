import pandas as pd
import numpy as np
from pathlib import Path

RES = Path("C:/xampp/htdocs/GodE/research/results")
d = pd.read_csv(RES / "edge_combine_monthly.csv")
d["date"] = pd.to_datetime(d["date"])
d = d.set_index("date").sort_index()
# shift(1): the weight for month t must be knowable at the start of month t
d["carry_mom3"] = d["carry"].rolling(3).sum().shift(1)
d["w_carry"] = np.where(d["carry_mom3"] > 0, 0.5, 0.0)
d["w_rsi"] = 1 - d["w_carry"]
d["regime"] = np.where(d["carry_mom3"] > 0, "carry_favored", "rsi_favored")
d["adaptive_blend"] = d["w_carry"] * d["carry"] + d["w_rsi"] * d["rsi_band"]

print("=== Last 12 months ===")
last12 = d.tail(12)
for name in ["carry", "rsi_band", "adaptive_blend"]:
    r = last12[name].dropna()
    eq = (1 + r).cumprod()
    sharpe = r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else 0
    dd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
    print(f"{name:<20} total {(eq.iloc[-1] - 1) * 100:>7.2f}%  Sharpe {sharpe:>6.2f}  maxDD {dd:>7.1f}%")

print()
print("=== Regime by year (carry_favored fraction) ===")
d["year"] = d.index.year
regime_by_year = d.groupby("year")["regime"].apply(lambda x: (x == "carry_favored").mean())
print(regime_by_year.round(2))

print()
print("=== Last 12 months detail ===")
for idx, row in last12.iterrows():
    print(idx.strftime("%Y-%m"), f"carry {row['carry']*100:>7.2f}%", f"rsi {row['rsi_band']*100:>7.2f}%", f"blend {row['adaptive_blend']*100:>7.2f}%", row["regime"])
