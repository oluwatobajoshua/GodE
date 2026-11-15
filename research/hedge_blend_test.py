import pandas as pd
import numpy as np
from pathlib import Path

RES = Path("C:/xampp/htdocs/GodE/research/results")
d = pd.read_csv(RES / "edge_combine_monthly.csv")
d["date"] = pd.to_datetime(d["date"])
d = d.set_index("date").sort_index()

# The regime weight for month t must be knowable at the START of month t, so the
# trailing momentum is shifted by one month. Without the shift the weight for
# month t contains carry[t] itself, which is the lookahead that produced the
# retracted "Sharpe 1.89 / OOS 1.47 / CAGR 7.22%" claim. See the comparison at
# the bottom of this file.
d["carry_mom3"] = d["carry"].rolling(3).sum().shift(1)
d["rsi_mom3"] = d["rsi_band"].rolling(3).sum().shift(1)

# Regime: carry momentum positive -> weight carry higher
d["regime"] = np.where(d["carry_mom3"] > 0, "carry_favored", "rsi_favored")
d["w_carry"] = np.where(d["carry_mom3"] > 0, 0.5, 0.0)
d["w_rsi"] = 1 - d["w_carry"]
d["adaptive_blend"] = d["w_carry"] * d["carry"] + d["w_rsi"] * d["rsi_band"]

def metrics(r, label):
    r = r.dropna()
    eq = (1 + r).cumprod()
    n = len(r)
    if n == 0 or eq.iloc[-1] <= 0:
        return {"CAGR": -1, "Sharpe": 0, "maxDD": 0, "total": -1}
    cagr = eq.iloc[-1] ** (12 / n) - 1
    peak = np.maximum.accumulate(eq)
    sd = r.std()
    sharpe = r.mean() / sd * np.sqrt(12) if sd > 0 else 0
    dd = (eq / peak - 1).min()
    return {"CAGR": cagr * 100, "Sharpe": sharpe, "maxDD": dd * 100, "total": (eq.iloc[-1] - 1) * 100}

n = len(d)
cut = int(n * 0.8)

print("=== Regime-dependent adaptive blend ===")
print("(carry_mom3 > 0 -> 50/50 carry/RSI, else RSI only)")
print()
for label, sl in (("design+validation", slice(0, cut)), ("OOS", slice(cut, n)), ("full", slice(None))):
    print(f"--- {label} ---")
    for name in ["carry", "rsi_band", "blend", "adaptive_blend"]:
        m = metrics(d[name].iloc[sl], name)
        print(f"  {name:<20} CAGR {m['CAGR']:>7.2f}% Sharpe {m['Sharpe']:>6.2f} maxDD {m['maxDD']:>7.1f}% total {m['total']:>7.1f}%")
    print()

print("Regime switches:", (d["regime"] != d["regime"].shift(1)).sum())
print("Months carry_favored:", int((d["regime"] == "carry_favored").sum()))
print("Months rsi_favored:", int((d["regime"] == "rsi_favored").sum()))
print()

# Also test: simple rule - only trade carry when its last month was positive
d["carry_pos_last"] = d["carry"].shift(1) > 0
d["w_carry2"] = np.where(d["carry_pos_last"], 0.3, 0.0)
d["w_rsi2"] = 1 - d["w_carry2"]
d["adaptive_blend2"] = d["w_carry2"] * d["carry"] + d["w_rsi2"] * d["rsi_band"]
print("=== Alternative: carry weight = 0.3 if last month carry positive, else 0 ===")
for label, sl in (("design+validation", slice(0, cut)), ("OOS", slice(cut, n)), ("full", slice(None))):
    print(f"--- {label} ---")
    for name in ["carry", "rsi_band", "adaptive_blend2"]:
        m = metrics(d[name].iloc[sl], name)
        print(f"  {name:<20} CAGR {m['CAGR']:>7.2f}% Sharpe {m['Sharpe']:>6.2f} maxDD {m['maxDD']:>7.1f}% total {m['total']:>7.1f}%")
    print()

# --- the trap, kept on purpose: what the unshifted weight does ---
print("=== LOOKAHEAD CONTROL: weight for month t built from carry[t] ===")
_mom_leaky = d["carry"].rolling(3).sum()
_w_leaky = np.where(_mom_leaky > 0, 0.5, 0.0)
_leaky = pd.Series(_w_leaky * d["carry"].values + (1 - _w_leaky) * d["rsi_band"].values,
                   index=d.index)
for label, sl in (("design+validation", slice(0, cut)), ("OOS", slice(cut, n)),
                  ("full", slice(None))):
    ml = metrics(_leaky.iloc[sl], "leaky")
    mh = metrics(d["adaptive_blend"].iloc[sl], "honest")
    print(f"  {label:<18} leaky CAGR {ml['CAGR']:>6.2f}% Sharpe {ml['Sharpe']:>5.2f}"
          f"   |   honest CAGR {mh['CAGR']:>6.2f}% Sharpe {mh['Sharpe']:>5.2f}")
print("  The gap is the leak, not an edge. Only the honest row is a result.")

# --- machine-readable record so the ledger rebuilds from a file, not from stdout ---
try:
    import json

    manifest = {
        "honest": {lab: metrics(d["adaptive_blend"].iloc[sl], lab)
                   for lab, sl in (("design+validation", slice(0, cut)),
                                   ("OOS", slice(cut, n)), ("full", slice(None)))},
        "leaky": {lab: metrics(_leaky.iloc[sl], lab)
                  for lab, sl in (("design+validation", slice(0, cut)),
                                  ("OOS", slice(cut, n)), ("full", slice(None)))},
        "baselines": {
            "carry": metrics(d["carry"], "carry"),
            "rsi_band": metrics(d["rsi_band"], "rsi_band"),
        },
        "verdict": ("weight uses carry[t] itself -> lookahead; lagged one month the "
                    "blend is worse than RSI band alone in every split"),
        "source": "research/hedge_blend_test.py -> edge_combine_monthly.csv",
    }
    (RES / "hedge_blend_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
except Exception as exc:  # pragma: no cover - reporting only
    print(f"manifest skipped ({exc})")

# --- calendar proof for the surviving variant (user reporting rule) ---
try:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from period_returns import publish_results  # noqa: PLC0415

    publish_results("hedge_blend", d["adaptive_blend"].dropna(), already_monthly=True)
except Exception as exc:  # pragma: no cover - reporting only
    print(f"publish_results skipped ({exc})")
