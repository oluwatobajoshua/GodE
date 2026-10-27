#!/usr/bin/env python3
"""Portfolio H1 trend test across the major FX pairs.

A single-pair Donchian trend model failed on EURUSD.  Trend following is
normally a basket strategy, so this script runs the same rules on eight pairs
and aggregates 1R-per-trade results.  The split logic is identical:

* design:      2026-01-01 .. 2026-06-30
* validation:  2026-07-01 .. 2026-10-26
* OOS:         2025-01-01 .. 2025-12-31

This is still research, not a deployment decision.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from h1_trend import Config, load_data, metrics, simulate  # noqa: E402

DATA = Path(os.environ.get("SOVEREIGN_DATA_DIR", HERE / "data"))
OUT = Path(os.environ.get("SOVEREIGN_RESULTS_DIR", HERE / "results"))
DESIGN_YEAR = int(os.environ.get("SOVEREIGN_DESIGN_YEAR", "2026"))
DESIGN_LABEL = f"design_{DESIGN_YEAR}H1"
VAL_LABEL = f"validation_{DESIGN_YEAR}H2"

SYMBOLS = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "AUDUSD": 0.00001,
    "NZDUSD": 0.00001,
    "USDCAD": 0.00001,
    "USDCHF": 0.00001,
    "EURGBP": 0.00001,
    "USDJPY": 0.001,
    "EURJPY": 0.001,
}

CONFIGS = [
    Config("don20_sl20_tp3", lookback=20, sl_atr=2.0, tp_r=3.0),
    Config("don20_sl20_tp4", lookback=20, sl_atr=2.0, tp_r=4.0),
    Config("don20_sl20_tp6", lookback=20, sl_atr=2.0, tp_r=6.0),
    Config("don40_sl20_tp3", lookback=40, sl_atr=2.0, tp_r=3.0),
    Config("don40_sl20_tp4", lookback=40, sl_atr=2.0, tp_r=4.0),
    Config("don40_sl20_tp6", lookback=40, sl_atr=2.0, tp_r=6.0),
    Config("don55_sl20_tp4", lookback=55, sl_atr=2.0, tp_r=4.0),
    Config("don55_sl25_tp6", lookback=55, sl_atr=2.5, tp_r=6.0),
    Config("don100_sl25_tp4", lookback=100, sl_atr=2.5, tp_r=4.0),
    Config("don40_sl15_tp4", lookback=40, sl_atr=1.5, tp_r=4.0),
    Config("don40_sl25_tp4", lookback=40, sl_atr=2.5, tp_r=4.0),
    Config("don20_sl20_trail20", lookback=20, sl_atr=2.0, tp_r=0.0, trail_atr=2.0),
    Config("don40_sl20_trail20", lookback=40, sl_atr=2.0, tp_r=0.0, trail_atr=2.0),
    Config("don40_sl20_trail30", lookback=40, sl_atr=2.0, tp_r=0.0, trail_atr=3.0),
    Config("don55_sl25_trail30", lookback=55, sl_atr=2.5, tp_r=0.0, trail_atr=3.0),
    Config("don40_sl20_tp4_notrend", lookback=40, sl_atr=2.0, tp_r=4.0, trend_filter=False),
    Config("don20_sl20_tp3_reverse", lookback=20, sl_atr=2.0, tp_r=3.0, exit_opposite=True),
]


def split_trades(trades: pd.DataFrame) -> dict[str, pd.DataFrame]:
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    return {
        DESIGN_LABEL: t[
            (t["entry_time"] >= f"{DESIGN_YEAR}-01-01")
            & (t["entry_time"] < f"{DESIGN_YEAR}-07-01")
        ],
        VAL_LABEL: t[
            (t["entry_time"] >= f"{DESIGN_YEAR}-07-01")
            & (t["entry_time"] < f"{DESIGN_YEAR + 1}-01-01")
        ],
        "OOS_2025": t[
            (t["entry_time"] >= "2025-01-01")
            & (t["entry_time"] < "2026-01-01")
        ],
    }


def main() -> int:
    summaries = []
    per_symbol_rows = []
    all_trades = []

    for cfg in CONFIGS:
        trades = []
        for symbol, point in SYMBOLS.items():
            path = DATA / f"{symbol}_H1.csv"
            if not path.exists():
                continue
            df = load_data(path)
            tr = simulate(df, cfg, point=point)
            if tr.empty:
                continue
            tr["symbol"] = symbol
            trades.append(tr)
            # Per-symbol design/validation/OOS totals for the final table.
            for split, part in split_trades(tr).items():
                m = metrics(part)
                per_symbol_rows.append(
                    {"config": cfg.name, "symbol": symbol, "split": split, **m}
                )
        if not trades:
            continue
        all_tr = pd.concat(trades, ignore_index=True).sort_values("entry_time")
        for split, part in split_trades(all_tr).items():
            m = metrics(part)
            summaries.append({"config": cfg.name, "split": split, **m})
        all_trades.append(all_tr)

    summary = pd.DataFrame(summaries)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "h1_portfolio_summary.csv", index=False)
    pd.DataFrame(per_symbol_rows).to_csv(OUT / "h1_portfolio_by_symbol.csv", index=False)
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_csv(OUT / "h1_portfolio_trades.csv", index=False)

    print("=== portfolio H1 trend ===")
    print(summary.to_string(index=False))

    piv = summary.pivot(index="config", columns="split", values="total")
    good = piv[(piv[DESIGN_LABEL] > 0) & (piv[VAL_LABEL] > 0)]
    print("\n=== configs positive in design AND validation ===")
    print(good.round(2).to_string() if len(good) else "none")
    if len(good):
        print("\n=== OOS 2025 for those configs ===")
        print(piv.loc[good.index, "OOS_2025"].round(2).to_string())

    if all_trades:
        # Show per-symbol contribution for the best validation config.
        best = piv[VAL_LABEL].idxmax()
        print(f"\n=== per-symbol total R for best validation config: {best} ===")
        ps = pd.DataFrame(per_symbol_rows)
        ps = ps[ps["config"] == best].pivot(index="symbol", columns="split", values="total")
        print(ps.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
