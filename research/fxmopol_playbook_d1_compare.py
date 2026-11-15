#!/usr/bin/env python3
"""FXMOPOL playbook — D1 gate off vs on (matches EA InpRequireD1Align).

Run:
    .\\.venv\\Scripts\\python.exe research\\fxmopol_playbook_d1_compare.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from fxmopol_playbook import SPLITS, backtest, load_h1, metrics
from period_returns import print_split_monthly, publish_results, to_monthly

HERE = Path(__file__).resolve().parent
OUT_SUM = HERE / "results" / "fxmopol_playbook_d1_compare_summary.csv"
OUT_MANIFEST = HERE / "results" / "fxmopol_playbook_d1_manifest.json"
COMPARE_SYMS = ("EURUSD", "GBPUSD")


def run_variant(sym: str, use_d1: bool) -> pd.Series:
    mid = 50
    h1 = load_h1(sym)
    return backtest(h1, ema_mid=mid, use_d1_gate=use_d1)


def main() -> int:
    rows: list[dict] = []
    manifest: dict = {"splits": SPLITS, "symbols": {}}

    for sym in COMPARE_SYMS:
        off = run_variant(sym, False)
        on = run_variant(sym, True)
        manifest["symbols"][sym] = {}
        for label, trade_r, d1_on in (("d1_off", off, False), ("d1_on", on, True)):
            tag = f"playbook_{sym}_d1_{'on' if d1_on else 'off'}"
            publish_results(tag, trade_r)
            print_split_monthly(tag, trade_r, SPLITS)
            manifest["symbols"][sym][label] = {
                "full": metrics(trade_r),
            }
            for split, (a, b) in SPLITS.items():
                part = trade_r[(trade_r.index >= a) & (trade_r.index < b)]
                m = metrics(part)
                rows.append(
                    {
                        "symbol": sym,
                        "d1_gate": d1_on,
                        "split": split,
                        **m,
                    }
                )
                manifest["symbols"][sym][label][split] = m

        oos_off = off[(off.index >= SPLITS["OOS"][0]) & (off.index < SPLITS["OOS"][1])]
        oos_on = on[(on.index >= SPLITS["OOS"][0]) & (on.index < SPLITS["OOS"][1])]
        mo_off = to_monthly(oos_off)
        mo_on = to_monthly(oos_on)
        if not mo_off.empty and not mo_on.empty:
            merged = mo_off[["date", "return_pct"]].merge(
                mo_on[["date", "return_pct"]],
                on="date",
                suffixes=("_off", "_on"),
            )
            print(f"\n=== {sym} OOS monthly: D1 off vs on (sum off={oos_off.sum()*100:.2f}% on={oos_on.sum()*100:.2f}%) ===")
            print(merged.tail(12).to_string(index=False))

    pd.DataFrame(rows).to_csv(OUT_SUM, index=False)
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("\n=== Split summary (exp bps/trade) ===")
    df = pd.DataFrame(rows)
    for sym in COMPARE_SYMS:
        sub = df[df["symbol"] == sym]
        print(sym)
        for split in ("design", "validation", "OOS"):
            line = sub[sub["split"] == split]
            for _, r in line.iterrows():
                gate = "on" if r["d1_gate"] else "off"
                print(
                    f"  {split:10} D1 {gate}: n={int(r['n']):4d} exp_bps={r['exp_bps']:7.2f} "
                    f"PF={r['pf']:.2f} total%={r['total_pct']:.2f}"
                )

    # Carry anchor (research-only; not deploy)
    print("\n=== Anchor sleeve: carry_mom_filter (monthly — compare scale) ===")
    try:
        from export_period_results import carry_mom_filter_series, load_monthly_prices, load_rates
        from carry import CURRENCIES, carry_weights
        import numpy as np

        rates = load_rates()
        spot, price = load_monthly_prices()
        idx = spot.index.intersection(rates.index)
        spot, price, rates = spot.loc[idx], price.loc[idx], rates.loc[idx]
        diff = rates[list(CURRENCIES.keys())].sub(rates["USD"], axis=0)
        diff_prev = diff.shift(1)
        mom = (1 + spot).rolling(12).apply(np.prod, raw=True) - 1
        mom_prev = mom.shift(1)
        base_w = carry_weights(diff_prev, top_n=3)
        long_ok = base_w.gt(0) & mom_prev.gt(0)
        short_ok = base_w.lt(0) & mom_prev.lt(0)
        filtered = long_ok.div(long_ok.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) - short_ok.div(
            short_ok.sum(axis=1).replace(0, np.nan), axis=0
        ).fillna(0)
        from carry import run

        r = run(filtered, spot, diff, price)
        oos = r[(r.index >= SPLITS["OOS"][0]) & (r.index < SPLITS["OOS"][1])]
        print(f"OOS months: n={len(oos)} sum_return%={oos.sum()*100:.2f} mean/mo%={oos.mean()*100:.3f}")
    except Exception as ex:
        print(f"(carry snippet skipped: {ex})")

    print(f"\nWrote {OUT_SUM.name}, {OUT_MANIFEST.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
