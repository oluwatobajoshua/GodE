#!/usr/bin/env python3
"""FXMOPOL playbook on BTCUSD H1 exported from the open MT5 terminal.

Exports nothing; expects research/data/BTCUSD_H1.csv (run export_btcusd_h1.ps1 or
inline MT5 paging first). Crypto: 24/7 bars, wider default cost.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import fxmopol_playbook as pb
from period_returns import print_split_monthly, publish_results

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "BTCUSD_H1.csv"
OUT_PREFIX = "playbook_BTCUSD_mid50"

# BTC history on this broker starts ~2018; adjust 3-split windows.
SPLITS = {
    "design": ("2018-01-01", "2021-01-01"),
    "validation": ("2021-01-01", "2023-07-01"),
    "OOS": ("2023-07-01", "2032-01-01"),
}


def load_btc() -> pd.DataFrame:
    if not DATA.exists():
        raise FileNotFoundError(
            f"{DATA} missing — export H1 from MT5 while terminal is open."
        )
    d = pd.read_csv(DATA, parse_dates=["time"])
    d["time"] = pd.to_datetime(d["time"], utc=True).dt.tz_convert(None)
    return d.set_index("time").sort_index()


def metrics(r: pd.Series) -> dict:
    return pb.metrics(r)


def main() -> int:
    h1 = load_btc()
    print(f"[BTCUSD] H1 bars: {len(h1):,}  {h1.index[0]} -> {h1.index[-1]}")

    variants = [
        ("baseline_3bps", 3.0, True, True),
        ("realistic_10bps", 10.0, True, True),
        ("no_proximity", 10.0, False, True),
        ("relaxed_stayaway", 10.0, True, False),
    ]
    summary_rows = []

    for tag, cost, prox, strict in variants:
        pb.COST_BPS = cost
        pb.USE_PROXIMITY_FILTERS = prox
        pb.STRICT_STAY_AWAY = strict
        trade_r = pb.backtest(h1, ema_mid=50)
        name = f"{OUT_PREFIX}_{tag}"
        publish_results(name, trade_r)
        print_split_monthly(name, trade_r, SPLITS)
        trades = pb.replay_trades(h1, ema_mid=50)
        trades_path = HERE / "results" / f"fxmopol_playbook_trades_BTCUSD_{tag}.csv"
        trades.to_csv(trades_path, index=False)

        for split, (a, b) in SPLITS.items():
            part = trade_r[(trade_r.index >= a) & (trade_r.index < b)]
            summary_rows.append(
                {
                    "variant": tag,
                    "cost_bps": cost,
                    "proximity": prox,
                    "strict_stayaway": strict,
                    "split": split,
                    **metrics(part),
                }
            )
        full = metrics(trade_r)
        print(
            f"=== {tag} (cost={cost}bps prox={prox} strict={strict}) "
            f"n={full['n']} exp_bps={full['exp_bps']} pf={full['pf']} "
            f"total_pct={full['total_pct']} wr={full['winrate']}% ==="
        )

    out_sum = HERE / "results" / "fxmopol_btcusd_summary.csv"
    pd.DataFrame(summary_rows).to_csv(out_sum, index=False)
    manifest = {
        "symbol": "BTCUSD",
        "bars": len(h1),
        "from": str(h1.index[0]),
        "to": str(h1.index[-1]),
        "source": "MT5 Deriv-Demo via MetaTrader5 Python",
        "summary_csv": out_sum.name,
    }
    (HERE / "results" / "fxmopol_btcusd_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"Wrote {out_sum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
