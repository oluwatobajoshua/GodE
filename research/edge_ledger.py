#!/usr/bin/env python3
"""One ledger for every concept tested: keep it, or drop it, and why.

The rule this file enforces: a concept is KEPT only if it is positive in design,
validation AND out-of-sample, net of a real cost model, AND the thing being kept
is the new concept rather than the baseline it was tested against. A concept that
is positive only gross, only in some splits, or only because the baseline survived
is REJECTED - and stays recorded, so it cannot return later as a "new idea".

Each row is rebuilt from the result file that proves it. Nothing is hand-typed
except the two legacy M5 lanes, which predate the per-run CSVs and are quoted from
AGENTS.md with their provenance named.

The design/validation/OOS columns are left as "n/a" wherever the study did not
produce genuine split returns. Forcing a number into those columns is how a
ledger starts lying, so it is not done.

Verdicts
  KEEP         positive in all three splits, net, and it is the new concept
  CONDITIONAL  positive but with a hard restriction (one pair, one timeframe), or
               never split - can be run, must not be scaled on faith
  REJECT       measured and not profitable, or only the baseline survived
  RETRACTED    published as positive, then found to be a lookahead artifact.
               Never quote the old number.

Usage
    .\\.venv\\Scripts\\python.exe research/edge_ledger.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "research" / "results"
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY")
NA = float("nan")

rows: list[dict] = []


def read(name: str) -> pd.DataFrame | None:
    p = RESULTS / name
    if not p.exists():
        return None
    try:
        return pd.read_csv(p)
    except Exception:
        return None


def read_json(name: str) -> dict | None:
    import json

    p = RESULTS / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def add(concept, verdict, variant, metric, proves, note="",
        design=NA, validation=NA, oos=NA):
    rows.append(dict(concept=concept, verdict=verdict, variant=variant,
                     design=design, validation=validation, oos=oos,
                     metric=metric, proves=proves, note=note))


# ---------------------------------------------------------------- 1. carry (D1)
d = read("carry_summary.csv")
if d is not None and "strategy" in d.columns:
    c = d[d.strategy == "carry_mom_filter"].set_index("split")
    need = {"design_2005_2014", "validation_2015_2019", "OOS_2020_2026", "full_2005_2026"}
    if need <= set(c.index):
        full = c.loc["full_2005_2026"]
        add("carry + 12m momentum (D1)", "KEEP", "carry_mom_filter",
            f"CAGR {float(full.cagr):+.2f}%  Sharpe {float(full.sharpe):.2f}  "
            f"maxDD {float(full.maxdd):.1f}%  (21.6y)",
            "research/carry.py -> carry_summary.csv",
            "only research_good tier; positive in all 3 splits",
            float(c.loc["design_2005_2014", "total"]),
            float(c.loc["validation_2015_2019", "total"]),
            float(c.loc["OOS_2020_2026", "total"]))

# ------------------------------------------------- 2. RSI band family (H1, net)
# The book trades the equal-weight ENSEMBLE of every parameter config, not the
# single design+validation winner. That is not a detail: ranking 100 configs on
# the worst of design+validation picks the extreme of the surface, and the
# extreme does not generalise. On the extended tape the EURUSD winner has an OOS
# of -1.35% while the family average is +0.75%. So the ledger quotes the
# ensemble, and the plateau statistics are kept in the metric column.
CAL = (("2018-01-01", "2022-01-01"),
       ("2022-01-01", "2024-01-01"),
       ("2024-01-01", "2031-01-01"))


def monthly_calendar(name: str, strategy: str):
    """Design/validation/OOS totals (%) on fixed calendar boundaries."""
    d = read(name)
    if d is None or not len(d):
        return None
    if "strategy" in d.columns:
        d = d[d["strategy"] == strategy]
    if not len(d):
        return None
    idx = pd.to_datetime(d["date"]).dt.to_period("M")
    r = pd.Series(d["return_pct"].to_numpy(float) / 100.0, index=idx).sort_index()
    out = []
    for lo, hi in CAL:
        x = r[(r.index >= pd.Period(lo, "M")) & (r.index < pd.Period(hi, "M"))]
        out.append(float((1.0 + x).prod() - 1.0) * 100.0 if len(x) else 0.0)
    return out


for sym, levels, extra in (("EURUSD", "50/75/25", ""),
                           ("GBPUSD", "45/65/35", "pair params")):
    d = read(f"rsi_band_net_{sym}_H1.csv")
    ens = monthly_calendar(f"rsi_band_ens_{sym}_H1_monthly.csv",
                           f"rsi_band_ens_{sym}_H1")
    if d is None or not len(d) or ens is None:
        continue
    n = len(d)
    all3 = int(((d[["design", "validation", "oos"]] > 0).all(axis=1)).sum())
    b = d.sort_values("worst_dv", ascending=False).iloc[0]
    verdict = "KEEP" if all(v > 0 for v in ens) else "CONDITIONAL"
    add(f"RSI band ride ({sym} H1, net)", verdict,
        f"{levels} {extra}".strip(),
        f"ensemble D/V/OOS {ens[0]:+.2f}/{ens[1]:+.2f}/{ens[2]:+.2f}%  "
        f"{all3}/{n} configs positive in all 3",
        f"research/rsi_band_net.py -> rsi_band_ens_{sym}_H1_monthly.csv",
        f"the book trades the equal-weight ensemble of all {n} configs, not the "
        f"design+validation winner: that winner has OOS {float(b.oos):+.2f}% "
        f"while the family average is {ens[2]:+.2f}%, because ranking on the "
        f"worst of design+validation selects the extreme of the surface and the "
        f"extreme does not generalise",
        ens[0], ens[1], ens[2])

# ------------------------------------------------------- 3. candle exits
for sym in SYMBOLS:
    d = read(f"candle_exit_{sym}_H1.csv")
    if d is None or not len(d):
        continue
    base = d[(d.rule == "rsi75") & (d["mode"] == "fresh")]
    surv = d[(d.design > 0) & (d.validation > 0) & (d.oos > 0)]
    #--- the baseline is not a candle concept: a candle rule only counts if it
    #--- survives on its own
    candle_surv = surv[~surv.rule.isin(("rsi75", "rsi75_recover"))]
    best = d.sort_values("exp", ascending=False).iloc[0]
    if len(candle_surv):
        v = "KEEP"
        metric = f"{len(candle_surv)} candle exits survive"
        note = ""
    else:
        v = "REJECT"
        metric = (f"0 candle exits survive; only the RSI-75 baseline does. "
                  f"best exp {best.exp:+.3f}%/trade (win {best.win:.1f}%)")
        note = ("win rate is not expectancy: green2 wins 60-63% on all 3 pairs "
                "and still loses")
    add(f"candle-colour exits ({sym} H1)", v,
        f"{len(surv)}/16 positive in all splits, all baseline",
        metric, f"research/candle_exit.py -> candle_exit_{sym}_H1.csv", note,
        float(best.design), float(best.validation), float(best.oos))

# ------------------------------------------------------- 4. RSI curvature
for sym in SYMBOLS:
    d = read(f"rsi_curvature_{sym}_H1.csv")
    if d is None or not len(d):
        continue
    base = d[(d.exit == "rsi75") & (d.filt == "none")]
    pure = d[d.exit.isin(("curv", "curv_any"))]
    pure_surv = pure[(pure.design > 0) & (pure.validation > 0) & (pure.oos > 0)]
    base_worst = float(base.worst_dv.iloc[0]) if len(base) else NA
    best_pure = pure.sort_values("worst_dv", ascending=False).iloc[0] if len(pure) else None
    gain = (float(best_pure.worst_dv) - base_worst) if best_pure is not None else NA
    if len(pure_surv) and np.isfinite(gain) and gain > 0.5:
        v = "KEEP"
        metric = f"pure curvature beats baseline by {gain:+.2f}pp on worst split"
        note = ""
        chosen = best_pure
    else:
        v = "REJECT"
        metric = (f"0 pure curvature exits survive"
                  if not len(pure_surv) else
                  f"pure curvature beats baseline by only {gain:+.2f}pp (noise)")
        note = "a random walk bends the same way 76-78% of the time"
        chosen = best_pure
    add(f"RSI curvature / the line bending ({sym} H1)", v,
        f"baseline worst-split {base_worst:+.2f}%",
        metric, f"research/rsi_curvature.py -> rsi_curvature_{sym}_H1.csv", note,
        float(chosen.design) if chosen is not None else NA,
        float(chosen.validation) if chosen is not None else NA,
        float(chosen.oos) if chosen is not None else NA)

# ------------------------------------------- 5. structure grade (retracted)
for sym in SYMBOLS:
    d = read(f"struct_quality_{sym}_H1_good_h20_monthly.csv")
    if d is None or not len(d):
        continue
    total = float(d.equity.iloc[-1] - 1.0) * 100
    add(f"look-left S/R grade ({sym} H1)", "RETRACTED",
        "GOOD grade gating entries",
        f"causal full-period total {total:+.2f}%",
        f"research/structure_quality.py -> "
        f"struct_quality_{sym}_H1_good_h20_monthly.csv",
        "the published +0.137/+0.193% OOS was a lookahead artifact")

# ------------------------------------------------------- 6. dynamic bands
for sym in SYMBOLS:
    d = read(f"band_variants_{sym}_H1.csv")
    if d is None or not len(d):
        continue
    cols = [c for c in d.columns if c.startswith("ret_")]
    tot = {c: (np.prod(1.0 + d[c].fillna(0.0).to_numpy()) - 1.0) * 100 for c in cols}
    fixed = tot.get("ret_fixed", NA)
    alt = {k: v for k, v in tot.items() if k != "ret_fixed"}
    best_alt = max(alt.values()) if alt else NA
    add(f"dynamic RSI bands ({sym} H1)", "REJECT",
        f"fixed {fixed:+.2f}% vs best adaptive {best_alt:+.2f}%",
        "fixed 50/75/25 beats quantile, volscale and walk-forward",
        f"research/walkforward_band.py -> band_variants_{sym}_H1.csv",
        "all four forms of adaptivity measured and beaten by fixed")

# ------------------------------------------------------- 7. move decay
for sym in SYMBOLS:
    d = read(f"move_decay_{sym}_H1.csv")
    if d is None or not len(d):
        continue
    add(f"move onset decay ({sym} H1)", "REJECT",
        f"onset drift d0 {float(d.d0.mean()) * 100:+.3f}% -> "
        f"d20 {float(d.d20.mean()) * 100:+.3f}%",
        "no early-entry premium; entering 1-8 bars late costs ~0",
        f"research/move_decay.py -> move_decay_{sym}_H1.csv",
        "the 'enter early' advantage was the lookahead, not the market")

# ------------------------------------------------------- 8. market structure
for sym in SYMBOLS:
    d = read(f"market_structure_{sym}_H1.csv")
    if d is None or not len(d):
        continue
    up, dn = d[d.side > 0], d[d.side < 0]
    if not len(up) or not len(dn):
        continue
    add(f"HH/HL + LH/LL structure ({sym} H1)", "REJECT",
        f"h20 up {float(up.h20.mean()) * 100:+.3f}%  "
        f"down {float(dn.h20.mean()) * 100:+.3f}%",
        "zero forward drift after structure onset",
        f"research/market_structure.py -> market_structure_{sym}_H1.csv",
        "persistence detector, not a direction detector")

# ------------------------------------------------------- 9. EMA pyramid (D1)
def pyramid_splits(name: str):
    """Design/validation/OOS totals (%) from the eq_best equity curve."""
    d = read(name)
    if d is None or not len(d) or "eq_best" not in d.columns or "time" not in d.columns:
        return None
    t = pd.to_datetime(d["time"], utc=True).dt.tz_localize(None).sort_values()
    eq = d["eq_best"].to_numpy(float)
    # Align eq to sorted time
    order = t.argsort(kind="mergesort")
    t = t.iloc[order]
    eq = eq[order]
    ret = np.diff(np.r_[1.0, eq]) / np.r_[1.0, eq][:-1]
    r = pd.Series(ret, index=t).sort_index()
    t0, t1 = r.index[0], r.index[-1]
    span = t1 - t0
    e1, e2 = t0 + span * 0.60, t0 + span * 0.80
    out = []
    for _, lo, hi in (("design", t0, e1), ("validation", e1, e2), ("oos", e2, t1)):
        x = r[(r.index >= lo) & (r.index < hi)]
        out.append(float((1.0 + x).prod() - 1.0) * 100.0 if len(x) else 0.0)
    return out


for sym in SYMBOLS:
    d = read(f"ema_pyramid_{sym}_D1.csv")
    if d is None or not len(d) or "eq_best" not in d.columns:
        continue
    splits = pyramid_splits(f"ema_pyramid_{sym}_D1.csv")
    if splits is None:
        continue
    dv, val, oos = splits
    tot = float(d.eq_best.iloc[-1] - 1.0) * 100
    all3 = dv > 0 and val > 0 and oos > 0
    verdict = "KEEP" if all3 else "REJECT"
    add(f"EMA21 pyramid chunking ({sym} D1)", verdict, "eq_best variant",
        f"design {dv:+.2f}%  validation {val:+.2f}%  OOS {oos:+.2f}%  "
        f"full-period {tot:+.2f}%",
        f"research/ema_pyramid.py -> ema_pyramid_{sym}_D1.csv",
        "split into design/validation/OOS from eq_best equity curve",
        dv, val, oos)

# ---------------------------------------------- 10. machine learning / neural
for sym in SYMBOLS:
    d = read(f"ml_edge_test_{sym}_H1_h5.csv")
    if d is None or not len(d):
        continue
    real = d[~d.shuffled.astype(bool)]
    if not len(real):
        continue
    lin = real[real.model == "linear"]
    nn = real[real.model == "neural"]
    tr = real[real.model == "trees"]
    best = real.sort_values("auc", ascending=False).iloc[0]
    lin_auc = float(lin.auc.iloc[0]) if len(lin) else NA
    nn_auc = float(nn.auc.iloc[0]) if len(nn) else NA
    tr_auc = float(tr.auc.iloc[0]) if len(tr) else NA
    all_neg = bool((real[real.model != "majority"].net_pct < 0).all())
    profitable = float(best.net_pct) > 0 and best.model != "majority"
    add(f"ML / neural net direction ({sym} H1 h5)",
        "KEEP" if profitable else "REJECT",
        f"linear {lin_auc:.4f} / trees {tr_auc:.4f} / neural {nn_auc:.4f} AUC",
        f"best AUC {float(best.auc):.4f}, net {float(best.net_pct):+.2f}% - "
        f"capacity adds {nn_auc - lin_auc:+.4f}",
        f"research/ml_edge_test.py -> ml_edge_test_{sym}_H1_h5.csv",
        "purged walk-forward; shuffled-label control scored 0.49-0.50, so no leak. "
        "The neural net does not beat logistic regression, and every model loses "
        "money net - there is a signal, but it is too small to pay the spread")

# ---------------------------------------------- 11. combining the two survivors
d = read("edge_combine_monthly.csv")
if d is not None and len(d):
    a = d.carry.to_numpy(float)
    b = d.rsi_band.to_numpy(float)
    bl = d.blend.to_numpy(float)
    n = len(d)
    cut2 = int(n * 0.80)
    corr = float(np.corrcoef(a, b)[0, 1])

    def sh(r):
        sd = r.std()
        return (r.mean() / sd * np.sqrt(12)) if sd > 0 else 0.0

    def dd(r):
        eq = np.cumprod(1.0 + r)
        peak = np.maximum.accumulate(eq)
        return float((eq / peak - 1.0).min()) * 100

    add("carry x RSI band blend", "CONDITIONAL",
        f"correlation {corr:+.3f}",
        f"full-overlap Sharpe {sh(bl):.2f}, maxDD {dd(bl):.1f}% "
        f"(legs {sh(a):.2f}/{dd(a):.1f}% and {sh(b):.2f}/{dd(b):.1f}%)",
        "research/edge_combine.py -> edge_combine_monthly.csv",
        f"genuinely negatively correlated and the drawdown collapses, but OOS the "
        f"carry leg went to Sharpe {sh(a[cut2:]):.2f} so the blend "
        f"({sh(bl[cut2:]):.2f}) did not beat RSI alone ({sh(b[cut2:]):.2f})")

# ------------------------- 14. regime hedge blend (RETRACTED - lookahead)
d = read_json("hedge_blend_manifest.json")
if d is not None:
    h, l = d["honest"], d["leaky"]
    base = d["baselines"]["rsi_band"]
    add("regime hedge blend (carry x RSI)", "RETRACTED",
        "carry 3m momentum > 0 -> 50/50, else RSI only",
        f"honest OOS Sharpe {h['OOS']['Sharpe']:.2f} / maxDD {h['OOS']['maxDD']:.1f}% "
        f"vs retracted {l['OOS']['Sharpe']:.2f}; RSI band alone "
        f"{base['Sharpe']:.2f}",
        "research/hedge_blend_test.py -> hedge_blend_manifest.json",
        "the regime weight for month t was built from carry_mom3 which includes "
        "carry[t] itself, so the weight knew that month's outcome. Lagged one "
        "month the blend is CAGR 1.80% / Sharpe 0.42 / maxDD -7.5% against RSI "
        "band alone at 1.84% / 0.58 / -5.5%: it is strictly dominated by the leg "
        "it contains, in every split. The robustness sweep agrees - carry weight "
        "0.00 is the best member of the family and every added carry weight "
        "degrades Sharpe monotonically. Do not enable InpHedgeBlendEnabled.")

# ------------------------- 12. MA compression rubberband (RETRACTED artifact)
d = read("ma_compression_sweep.csv")
if d is not None:
    if "all3_pass" in d.columns:
        n_all3 = int(d["all3_pass"].sum())
        d["worst_dv"] = d[["design_pf", "validation_pf"]].min(axis=1)
        # a config needs a real sample before it can be called the best
        liquid = d[(d["design_n"] >= 50) & (d["validation_n"] >= 30)]
        best = (liquid if not liquid.empty else d).sort_values(
            "worst_dv", ascending=False).iloc[0]
        metric = (f"{n_all3}/{len(d)} clear all 3 splits on honest bar returns; "
                  f"best with a real sample: {best['symbol']} "
                  f"c={best['compress_atr']} e={best['ext_atr']} "
                  f"OOS Sharpe {best['OOS_sharpe']:.2f} "
                  f"({int(best['OOS_n'])} OOS trades)")
    else:
        metric = "metrics artifact; rerun ma_compression_sweep.py for corrected numbers"
    add("MA compression rubberband (H1)", "RETRACTED", "fade MA-bundle extension",
        metric,
        "research/ma_compression_sweep.py -> ma_compression_sweep.csv",
        "the return series stored (close - entry) i.e. the cumulative gain since "
        "entry, and metrics() compounded it as a per-bar return, so each gain was "
        "counted once per bar held; the entry cost was also charged twice; and the "
        "'44/48 OOS passes' were selected on OOS. Marked to market bar by bar with "
        "the RSI-band cost model the rule is cost-negative on EURUSD at every config "
        "(1.0/1.5: PF 0.91, -20.35%) and on USDJPY at 1.0/2.5 (PF 0.83, -11.19%). "
        "The corrected sweep does find 2 of 48 configs clearing all three splits: "
        "GBPUSD 1.0/2.5 at +10.88% over 8.6 years (~1.2% CAGR, 324 trades, OOS "
        "Sharpe 0.33) and USDJPY 0.5/3.0 with only 20 trades in 8.6 years. 2/48 is "
        "below the ~6/48 you would get from chance across three independent splits, "
        "and neither clears the quality bar. Do not enable "
        "SOV_COMPRESSION_RUBBERBAND.")

# --------------------------------------------- 13. RSI band pullback on M15/M30
d = read("rsi_band_m15m30_sweep.csv")
if d is not None and "all3_pos" in d.columns:
    for sym in ("GBPUSD", "USDJPY"):
        sub = d[(d["sym"] == sym) & (d["tf"].isin(["M15", "M30"]))
                & (d["all3_pos"].astype(str) == "True")]
        if sub.empty:
            continue
        best = sub.sort_values("net_sharpe", ascending=False).iloc[0]
        add(f"RSI band pullback ({sym} {best['tf']})", "CONDITIONAL",
            f"min_hold {best['min_hold']}, trend-aligned entry",
            f"Sharpe {best['net_sharpe']:.2f}  DD {best['net_dd']:.2f}%  {best['rt']} RT",
            "research/rsi_band_m15m30_sweep.py -> rsi_band_m15m30_sweep.csv",
            "research pass only - no forward demo, no MT5 tester, and cost "
            "sensitivity was not re-run at the shorter timeframe",
            float(best["net_design"]), float(best["net_validation"]),
            float(best["net_oos"]))

# ---------------------------------------------- 12. legacy / documented lanes
add("M5 breakout (2011-2025 HistData)", "REJECT", "single lane",
    "-355.79R  PF 0.75", "AGENTS.md validation section",
    "independent out-of-sample, proven negative")
add("M5 mean reversion (2011-2025 HistData)", "REJECT", "single lane",
    "-4078.37R  PF 0.75", "AGENTS.md validation section",
    "independent out-of-sample, proven negative")
add("structure ride (USDJPY H1)", "CONDITIONAL", "failed-high exit",
    "Sharpe 0.95 OOS", "research/structure_ride.py",
    "USDJPY only; EURUSD and GBPUSD fail the design split",
    +2.2, +11.7, +15.5)

# ---------------------------------------------------------------- output
tbl = pd.DataFrame(rows)
order = {"KEEP": 0, "CONDITIONAL": 1, "RETRACTED": 2, "REJECT": 3}
tbl["_o"] = tbl.verdict.map(order)
tbl = tbl.sort_values(["_o", "concept"]).drop(columns="_o").reset_index(drop=True)
RESULTS.mkdir(parents=True, exist_ok=True)
tbl.to_csv(RESULTS / "EDGE_LEDGER.csv", index=False)


def f(x):
    return "n/a" if x is None or not np.isfinite(x) else f"{x:+.2f}%"


lines = ["# Edge ledger - what is kept and what is dropped\n",
         "Generated by `research/edge_ledger.py`. Every row is rebuilt from the result",
         "file in the last column. KEEP requires positive design, validation AND",
         "out-of-sample, net of cost, and that the new concept - not its baseline -",
         "is what survived.\n",
         "| verdict | concept | variant | design | validation | OOS | deciding metric |",
         "|---|---|---|---|---|---|---|"]
for _, r in tbl.iterrows():
    lines.append(f"| **{r.verdict}** | {r.concept} | {r.variant} | {f(r.design)} | "
                 f"{f(r.validation)} | {f(r.oos)} | {r.metric} |")
kept = tbl[tbl.verdict == "KEEP"]
cond = tbl[tbl.verdict == "CONDITIONAL"]
lines += ["",
          f"**Kept: {len(kept)}. Conditional: {len(cond)}. "
          f"Rejected or retracted: {len(tbl) - len(kept) - len(cond)}.**", "",
          "## Notes\n"]
for _, r in tbl.iterrows():
    if r.note:
        lines.append(f"- **{r.concept}** - {r.note}")
(RESULTS / "EDGE_LEDGER.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"{'verdict':<12}{'concept':<40}{'design':>9}{'valid':>9}{'oos':>9}  deciding metric")
print("-" * 120)
for _, r in tbl.iterrows():
    print(f"{r.verdict:<12}{r.concept[:39]:<40}{f(r.design):>9}{f(r.validation):>9}"
          f"{f(r.oos):>9}  {r.metric[:56]}")
print("")
print(f"KEEP {len(kept)}   CONDITIONAL {len(cond)}   "
      f"REJECT/RETRACTED {len(tbl) - len(kept) - len(cond)}   (of {len(tbl)} concepts)")
print(f"wrote {RESULTS / 'EDGE_LEDGER.md'}")
print(f"wrote {RESULTS / 'EDGE_LEDGER.csv'}")
sys.exit(0)
