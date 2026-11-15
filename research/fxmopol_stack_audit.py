#!/usr/bin/env python3
"""Ablation: which signal layers help on validation (not used for promotion)."""
from __future__ import annotations

import argparse
from itertools import product

import pandas as pd

import fxmopol_playbook as pb
from fxmopol_playbook import SPLITS, metrics, replay_trades
from fxmopol_symbol_train import load_symbol

LAYER_COMBOS = list(product((False, True), (False, True), (False, True)))


def run_symbol(sym: str) -> None:
    h1 = load_symbol(sym)
    v0, v1 = SPLITS["validation"]
    base = {"proximity": True, "strict_stayaway": True, "ema_mid": 50 if sym != "USDJPY" else 100}
    rows = []
    for rsi_div, sr_f, fund_f in LAYER_COMBOS:
        pb.USE_RSI_DIVERGENCE = rsi_div
        pb.USE_SR_FILTER = sr_f
        pb.USE_FUND_SENTIMENT = fund_f
        pb.USE_PROXIMITY_FILTERS = base["proximity"]
        pb.STRICT_STAY_AWAY = base["strict_stayaway"]
        trades = replay_trades(h1, ema_mid=base["ema_mid"], symbol=sym)
        if trades.empty:
            m = {"n": 0, "exp_bps": -999, "pf": 0, "sharpe": -999}
        else:
            trades["entry_time"] = pd.to_datetime(trades["entry_time"])
            val = trades[(trades["entry_time"] >= v0) & (trades["entry_time"] < v1)]
            rets = val["return_pct"].astype(float) / 100.0
            m = metrics(rets)
        rows.append(
            {
                "rsi_div": rsi_div,
                "sr_filter": sr_f,
                "fund_sentiment": fund_f,
                "n_val": m.get("n", 0),
                "exp_bps_val": m.get("exp_bps", -999),
                "pf_val": m.get("pf", 0),
            }
        )
    out = pd.DataFrame(rows).sort_values("exp_bps_val", ascending=False)
    print(f"=== {sym} validation layer ablation (prox+strict on) ===")
    print(out.to_string(index=False))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="GBPUSD")
    args = p.parse_args()
    run_symbol(args.symbol.upper())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
