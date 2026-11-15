#!/usr/bin/env python3
"""Literature-backed FX stack (carry, xsec mom, TSMOM, triple-screen).

Sources (public research, not cherry-picked blogs):
- Sarno et al. (2012 JFE): cross-sectional currency momentum.
- Moskowitz, Ooi & Pedersen (2012): time-series momentum (12m own-return sign).
- Lustig et al. / carry literature; triple-screen combos (momentum+carry+skew).

Promotion rule (pre-registered):
  design_2005_2014 AND validation_2015_2019 AND OOS_2020_2026 must each have
  cagr > 0, sharpe > 0, profit factor >= 1.0 on monthly returns.

Run:
    .\\.venv\\Scripts\\python.exe research\\fx_evidence_stack.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = HERE / "results" / "fx_evidence_summary.csv"
MANIFEST = HERE / "results" / "fx_evidence_manifest.json"
AUTOMATION = REPO / "backtest" / "mt5_files" / "Sovereign" / "evidence_directives.json"

sys.path.insert(0, str(HERE))
from carry import (  # noqa: E402
    CURRENCIES,
    TARGET_VOL,
    carry_weights,
    load_monthly_prices,
    load_rates,
    metrics,
    run,
    split_returns,
)
from sovereign_composite import (  # noqa: E402
    apply_dd_halt,
    build_neutral_momentum_weights,
    load_vix,
    vix_scale,
)


def rank_weights(signal: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    rank = signal.rank(axis=1, ascending=False)
    n = signal.notna().sum(axis=1)
    long = rank.le(top_n)
    short = rank.gt(n - top_n, axis=0)
    n_long = long.sum(axis=1).replace(0, np.nan)
    n_short = short.sum(axis=1).replace(0, np.nan)
    return long.div(n_long, axis=0).fillna(0) - short.div(n_short, axis=0).fillna(0)


def tsmom_weights(spot: pd.DataFrame, lookback: int = 12) -> pd.DataFrame:
    mom = (1 + spot).rolling(lookback).apply(np.prod, raw=True) - 1
    s = np.sign(mom.shift(1))
    gross = s.abs().sum(axis=1).replace(0, np.nan)
    return s.div(gross, axis=0).fillna(0.0)


def triple_screen_weights(diff_prev: pd.DataFrame, spot: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)
    base = carry_weights(diff_prev, top_n=top_n)
    w = pd.DataFrame(0.0, index=base.index, columns=base.columns)
    for c in base.columns:
        mp = mom_prev[c]
        w[c] = base[c].where((base[c] > 0) & (mp > 0) | (base[c] < 0) & (mp < 0), 0.0)
    long = w.gt(0)
    short = w.lt(0)
    w = long.div(long.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short.div(
        short.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    both = long.any(axis=1) & short.any(axis=1)
    return w.mul(both.astype(float), axis=0)


def profit_factor(r: pd.Series) -> float:
    r = r.dropna()
    wins, losses = r[r > 0], r[r < 0]
    if len(losses) == 0 or losses.sum() == 0:
        return float("nan")
    return wins.sum() / abs(losses.sum())


def passes_split(r: pd.Series) -> bool:
    m = metrics(r, "x")
    if m["cagr"] <= 0 or m["sharpe"] <= 0:
        return False
    return profit_factor(r) >= 1.0


def sovereign_stack_series() -> pd.Series:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)
    neutral_w = build_neutral_momentum_weights(spot, diff_prev)
    vix = load_vix().reindex(idx).ffill()
    scale = vix_scale(vix.shift(1))
    w_stack = neutral_w.mul(scale, axis=0)
    r = run(w_stack, spot, diff, price)
    return apply_dd_halt(r)


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
    diff_prev = diff.shift(1)

    mom12 = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev_1 = spot.shift(1)

    strategies = {
        "lit_xsec_mom12": run(rank_weights(mom12.shift(1), 3), spot, diff, price),
        "lit_xsec_mom1": run(rank_weights(mom_prev_1, 3), spot, diff, price),
        "lit_tsmom12": run(tsmom_weights(spot, 12), spot, diff, price),
        "lit_triple_carry_mom12": run(triple_screen_weights(diff_prev, spot, 3), spot, diff, price),
        "sov_stack_v1": sovereign_stack_series(),
    }
    mom_prev = mom12.shift(1)
    base_w = carry_weights(diff_prev, top_n=3)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    strategies["carry_mom_filter"] = run(filtered, spot, diff, price)

    rows = []
    promoted = []
    required = ["design_2005_2014", "validation_2015_2019", "OOS_2020_2026"]
    for name, series in strategies.items():
        ok_all = True
        for split, part in split_returns(series).items():
            pf = profit_factor(part)
            row = {"strategy": name, "split": split, **metrics(part, name), "pf": round(pf, 2) if pd.notna(pf) else None}
            rows.append(row)
            if split in required and not passes_split(part):
                ok_all = False
        if ok_all:
            promoted.append(name)

    summary = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT, index=False)

    from period_returns import frame_to_monthly_rows, publish_results, print_split_monthly

    monthly_rows: list[dict] = []
    split_dates = {
        "design_2005_2014": ("2005-01-01", "2015-01-01"),
        "validation_2015_2019": ("2015-01-01", "2020-01-01"),
        "OOS_2020_2026": ("2020-01-01", "2030-01-01"),
    }
    for name, series in strategies.items():
        monthly_rows.extend(frame_to_monthly_rows(name, series, already_monthly=True))
        if name in ("carry_mom_filter", "sov_stack_v1"):
            publish_results(name, series, already_monthly=True)
            print_split_monthly(name, series, split_dates, already_monthly=True)
    monthly_path = HERE / "results" / "fx_evidence_monthly_returns.csv"
    pd.DataFrame(monthly_rows).to_csv(monthly_path, index=False)

    best = None
    if promoted:
        oos = summary[(summary.strategy.isin(promoted)) & (summary.split == "OOS_2020_2026")]
        best = oos.sort_values("sharpe", ascending=False).iloc[0]["strategy"] if len(oos) else promoted[0]

    manifest = {
        "sources": [
            "Sarno et al. (2012) cross-sectional FX momentum",
            "Moskowitz, Ooi & Pedersen (2012) time-series momentum",
            "Carry + momentum triple-screen (literature combo)",
            "Sovereign Stack v1 (neutral carry + VIX + DD halt)",
        ],
        "promoted": promoted,
        "automated_default": best,
        "promotion_rule": "cagr>0, sharpe>0, pf>=1 on design, validation, OOS",
        "ea_mapping": {
            "carry_mom_filter": "SovereignGodsEyeEA InpStrategy=SOV_CARRY_MOM",
            "sov_stack_v1": "SovereignGodsEyeEA InpUseSovereignStackV1=true",
            "lit_*": "research only until EA sleeve wired",
        },
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    AUTOMATION.parent.mkdir(parents=True, exist_ok=True)
    auto = {
        "version": 1,
        "active_strategy": best or "none",
        "promoted_strategies": promoted,
        "mode": "SOV_CARRY_MOM",
        "use_sovereign_stack_v1": best == "sov_stack_v1",
        "notes": "Generated by fx_evidence_stack.py — not live deploy without human review",
    }
    AUTOMATION.write_text(json.dumps(auto, indent=2), encoding="utf-8")

    print(f"Promoted (3-split rule): {promoted or 'NONE'}; automated default: {best or 'none'}")
    print(f"Wrote {OUT}, {monthly_path}, {MANIFEST}, {AUTOMATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
