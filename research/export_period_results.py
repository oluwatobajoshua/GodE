#!/usr/bin/env python3
"""Export daily / monthly return tables for all certified + experimental sleeves.

Run:
    .\\.venv\\Scripts\\python.exe research\\export_period_results.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from carry import CURRENCIES, carry_weights, load_monthly_prices, load_rates, run  # noqa: E402
from period_returns import (  # noqa: E402
    DAILY_OUT,
    MONTHLY_OUT,
    frame_to_daily_rows,
    frame_to_monthly_rows,
    write_daily_bundle,
    write_monthly_bundle,
)
from sovereign_stack_tune import Cfg, build_series  # noqa: E402

try:
    from fxmopol_tune import Cfg as FxmCfg, backtest, load_h1  # noqa: E402
except FileNotFoundError:
    FxmCfg = backtest = load_h1 = None  # type: ignore


SETTINGS = HERE / "results" / "best_win_settings.json"
PROMOTED_STACK = Cfg(12, 4, 18.0, 30.0, 0.75, None, True)
DEFAULT_FXMOPOL = "breakout_sess1_pin0_h4200_sl1.0_tp2.0"


def promoted_stack_cfg() -> tuple[Cfg, str]:
    """Stack from best_win_settings (falls back to PROMOTED_STACK / mom12_n4)."""
    if SETTINGS.exists():
        ov = json.loads(SETTINGS.read_text(encoding="utf-8")).get("monthly_automation", {}).get("stack_overlay", {})
        inp = ov.get("inputs", {})
        if inp:
            cfg = Cfg(
                12,
                int(inp.get("InpTopN", PROMOTED_STACK.top_n)),
                float(inp.get("InpStackVixLo", PROMOTED_STACK.vix_lo)),
                float(inp.get("InpStackVixHi", PROMOTED_STACK.vix_hi)),
                float(inp.get("InpStackVixMidScale", PROMOTED_STACK.vix_mid)),
                None if not inp.get("InpStackDdHaltEnable", False) else 0.2,
                bool(inp.get("InpStackRequireNeutral", PROMOTED_STACK.neutral)),
            )
            label = ov.get("config") or cfg.key()
            return cfg, f"recommended_{label}"
    return PROMOTED_STACK, f"recommended_{PROMOTED_STACK.key()}"


def carry_mom_filter_series(spot, diff, price) -> pd.Series:
    diff_prev = diff.shift(1)
    mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
    mom_prev = mom.shift(1)
    base_w = carry_weights(diff_prev, top_n=3)
    long_ok = base_w.gt(0) & mom_prev.gt(0)
    short_ok = base_w.lt(0) & mom_prev.lt(0)
    filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
        short_ok.sum(axis=1).replace(0, np.nan), axis=0
    ).fillna(0)
    return run(filtered, spot, diff, price)


def parse_fxmopol_key(key: str) -> FxmCfg:
    # breakout_sess1_pin0_h4200_sl1.0_tp2.0
    parts = key.split("_")
    mode = parts[0]
    session = parts[1] == "sess1"
    pin_only = parts[2] == "pin1"
    h4 = int(parts[3].replace("h4", ""))
    sl = float(parts[4].replace("sl", ""))
    tp = float(parts[5].replace("tp", ""))
    return FxmCfg(mode, session, pin_only, h4, sl, tp)


def main() -> int:
    rates = load_rates()
    spot, price = load_monthly_prices()
    idx = spot.index.intersection(rates.index)
    spot = spot.loc[idx]
    price = price.loc[idx]
    rates = rates.loc[idx]
    diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)

    monthly_rows: list[dict] = []
    daily_rows: list[dict] = []

    monthly_rows.extend(
        frame_to_monthly_rows("carry_mom_filter", carry_mom_filter_series(spot, diff, price), already_monthly=True)
    )
    stack_cfg, stack_label = promoted_stack_cfg()
    stack_r = build_series(spot, diff, price, stack_cfg)
    monthly_rows.extend(frame_to_monthly_rows(stack_label, stack_r, already_monthly=True))

    manifest = HERE / "results" / "fxmopol_manifest.json"
    fx_key = DEFAULT_FXMOPOL
    if manifest.exists():
        fx_key = json.loads(manifest.read_text(encoding="utf-8")).get("best_validation_config", fx_key)

    if load_h1 is not None:
        h1 = load_h1("EURUSD")
        cfg = parse_fxmopol_key(fx_key)
        trade_r = backtest(h1, cfg)
        daily_rows.extend(frame_to_daily_rows(f"fxmopol_tune", trade_r))
        monthly_rows.extend(frame_to_monthly_rows("fxmopol_tune", trade_r, already_monthly=False))

    mpath = write_monthly_bundle(monthly_rows)
    dpath = write_daily_bundle(daily_rows)

    from period_returns import publish_results

    stack_cfg, stack_label = promoted_stack_cfg()
    stack_r = build_series(spot, diff, price, stack_cfg)
    publish_results("carry_mom_filter", carry_mom_filter_series(spot, diff, price), already_monthly=True)
    publish_results(stack_label, stack_r, already_monthly=True)
    if load_h1 is not None and len(daily_rows):
        cfg = parse_fxmopol_key(fx_key)
        publish_results("fxmopol_tune", backtest(load_h1("EURUSD"), cfg))
    print(f"Bundle: {mpath} ({len(monthly_rows)} rows), {dpath} ({len(daily_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
