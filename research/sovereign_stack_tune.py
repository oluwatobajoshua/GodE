#!/usr/bin/env python3
"""Sovereign Stack / carry_mom grid — literature-informed, no OOS fitting.

Inspired by:
- Menkhoff/Fan-style carry + momentum (3m vs 12m formation)
- Vol overlay (VIX scale bands, mid exposure cut)
- Integrated FX factor tilting (momentum as downside filter on carry)

Select on validation Sharpe among configs passing design; report OOS honestly.
Run:
    .\\.venv\\Scripts\\python.exe research\\sovereign_stack_tune.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "sovereign_stack_tune.csv"
MANIFEST = HERE / "results" / "sovereign_stack_manifest.json"

import sys

sys.path.insert(0, str(HERE))
from carry import (
    COST_PTS,
    CURRENCIES,
    TARGET_VOL,
    carry_weights,
    load_monthly_prices,
    load_rates,
    metrics,
    run,
    split_returns,
)
from sovereign_composite import apply_dd_halt, load_vix, vix_scale


SPLITS = ["design_2005_2014", "validation_2015_2019", "OOS_2020_2026"]


def profit_factor(r: pd.Series) -> float:
    r = r.dropna()
    wins, losses = r[r > 0], r[r < 0]
    if len(losses) == 0 or losses.sum() == 0:
        return float("nan")
    return wins.sum() / abs(losses.sum())


def passes(r: pd.Series) -> bool:
    m = metrics(r, "x")
    if m["cagr"] <= 0 or m["sharpe"] <= 0:
        return False
    return profit_factor(r) >= 1.0


@dataclass(frozen=True)
class Cfg:
    mom_lb: int
    top_n: int
    vix_lo: float
    vix_hi: float
    vix_mid: float
    dd_halt: float | None
    neutral: bool

    def key(self) -> str:
        dd = "off" if self.dd_halt is None else f"{int(self.dd_halt * 100)}"
        return (
            f"mom{self.mom_lb}_n{self.top_n}_v{int(self.vix_lo)}"
            f"_{int(self.vix_hi)}_m{self.vix_mid}_dd{dd}_neu{int(self.neutral)}"
        )


def cfg_from_key(key: str) -> Cfg:
    p = key.split("_")
    mom = int(p[0].replace("mom", ""))
    n = int(p[1].replace("n", ""))
    vlo = float(p[2].replace("v", ""))
    vhi = float(p[3])
    mid = float(p[4].replace("m", ""))
    dd_s = p[5].replace("dd", "")
    dd = None if dd_s == "off" else int(dd_s) / 100.0
    neu = p[6].replace("neu", "") == "1"
    return Cfg(mom, n, vlo, vhi, mid, dd, neu)


def build_series(
    spot: pd.DataFrame,
    diff: pd.DataFrame,
    price: pd.DataFrame,
    cfg: Cfg,
    vol_target: float | None = None,
    cost_pts: float | None = None,
) -> pd.Series:
    diff_prev = diff.shift(1)
    mom = (1 + spot).rolling(cfg.mom_lb).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)
    base_w = carry_weights(diff_prev, top_n=cfg.top_n)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    if cfg.neutral:
        both = long_ok.any(axis=1) & short_ok.any(axis=1)
        filtered = filtered.mul(both.astype(float), axis=0)
    vix = load_vix().reindex(filtered.index).ffill()
    scale = vix_scale(vix.shift(1), lo=cfg.vix_lo, hi=cfg.vix_hi, mid=cfg.vix_mid)
    w = filtered.mul(scale, axis=0)
    vt = TARGET_VOL if vol_target is None else vol_target
    cp = COST_PTS if cost_pts is None else cost_pts
    r = run(w, spot, diff, price, vol_target=vt, cost_pts=cp)
    if cfg.dd_halt is not None:
        r = apply_dd_halt(r, threshold=cfg.dd_halt)
    return r


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    grid = [
        Cfg(mom, n, vlo, vhi, mid, dd, neu)
        for mom, n, vlo, vhi, mid, dd, neu in product(
            (3, 6, 12),
            (2, 3, 4),
            (18.0, 20.0, 22.0),
            (28.0, 30.0, 32.0),
            (0.5, 0.75),
            (None, 0.15, 0.20),
            (True, False),
        )
    ]

    rows = []
    for cfg in grid:
        series = build_series(spot, diff, price, cfg)
        parts = split_returns(series)
        row = {"config": cfg.key()}
        ok_all = True
        for sp in SPLITS:
            part = parts[sp]
            pf = profit_factor(part)
            m = metrics(part, sp)
            row[f"cagr_{sp}"] = m["cagr"]
            row[f"sharpe_{sp}"] = m["sharpe"]
            row[f"pf_{sp}"] = round(pf, 2) if pd.notna(pf) else None
            row[f"maxdd_{sp}"] = m["maxdd"]
            if not passes(part):
                ok_all = False
        row["promoted_3split"] = ok_all
        row["val_sharpe"] = row["sharpe_validation_2015_2019"]
        rows.append(row)

    df = pd.DataFrame(rows)
    design_ok = df[df["sharpe_design_2005_2014"] > 0]
    pick = design_ok.sort_values("val_sharpe", ascending=False).iloc[0] if len(design_ok) else df.sort_values("val_sharpe", ascending=False).iloc[0]
    promoted = df[df.promoted_3split]

    manifest = {
        "sources": [
            "Fan (2025) optimized FX factors OOS",
            "JSI (2024) carry + momentum tilting",
            "Sovereign Stack v1 VIX + DD halt",
        ],
        "configs_tested": len(grid),
        "promoted_count": int(len(promoted)),
        "best_validation_pick": pick["config"],
        "best_validation_oos_sharpe": float(pick["sharpe_OOS_2020_2026"]),
        "best_validation_promoted": bool(pick["promoted_3split"]),
        "note": "Only carry_mom_filter baseline is EA-certified; stack tune guides InpStack* inputs",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("val_sharpe", ascending=False).to_csv(OUT, index=False)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=== Sovereign stack tune (top 10 by validation Sharpe) ===")
    top = df.sort_values("val_sharpe", ascending=False).head(10)
    cols = ["config", "val_sharpe", "sharpe_OOS_2020_2026", "promoted_3split"]
    print(top[cols].to_string(index=False))
    print(f"\nPromoted 3-split: {len(promoted)} configs")
    print(f"Validation pick: {pick['config']} (OOS sharpe={pick['sharpe_OOS_2020_2026']:.2f}, promoted={pick['promoted_3split']})")
    print(f"Wrote {OUT}, {MANIFEST}")
    from period_returns import publish_results, print_split_monthly

    pick_cfg = next(c for c in grid if c.key() == pick["config"])
    series = build_series(spot, diff, price, pick_cfg)
    publish_results(pick["config"], series, already_monthly=True)
    split_dates = {
        "design_2005_2014": ("2005-01-01", "2015-01-01"),
        "validation_2015_2019": ("2015-01-01", "2020-01-01"),
        "OOS_2020_2026": ("2020-01-01", "2030-01-01"),
    }
    print_split_monthly(pick["config"], series, split_dates, already_monthly=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
