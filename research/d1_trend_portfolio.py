#!/usr/bin/env python3
"""D1 trend-following portfolio test across major FX pairs.

The M5 and H1 tests failed.  This is the same trend idea at the timeframe where
it has the best structural case: D1 bars, a 20-year sample, and a spread cost
that is negligible relative to 1R.

Chronological split:
* design:      2005-01-01 .. 2014-12-31
* validation:  2015-01-01 .. 2019-12-31
* OOS:         2020-01-01 .. 2026-10-26

Signals are evaluated on a completed D1 bar and filled at the next D1 open.
One position per symbol.  Results are in R, one unit of risk per trade.

Run:
    .\\.venv\\Scripts\\python.exe research\\d1_trend_portfolio.py
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

SYMBOLS = {
    "EURUSD": 0.00001,
    "GBPUSD": 0.00001,
    "AUDUSD": 0.00001,
    "NZDUSD": 0.00001,
    "USDCAD": 0.00001,
    "USDCHF": 0.00001,
    "EURGBP": 0.00001,
    "EURCHF": 0.00001,
    "USDJPY": 0.001,
    "EURJPY": 0.001,
    "AUDJPY": 0.001,
    "CADJPY": 0.001,
}

CONFIGS = [
    Config("don20_sl20_tp4", lookback=20, sl_atr=2.0, tp_r=4.0),
    Config("don20_sl20_tp6", lookback=20, sl_atr=2.0, tp_r=6.0),
    Config("don20_sl30_tp6", lookback=20, sl_atr=3.0, tp_r=6.0),
    Config("don40_sl20_tp4", lookback=40, sl_atr=2.0, tp_r=4.0),
    Config("don40_sl20_tp6", lookback=40, sl_atr=2.0, tp_r=6.0),
    Config("don40_sl30_tp6", lookback=40, sl_atr=3.0, tp_r=6.0),
    Config("don55_sl20_tp4", lookback=55, sl_atr=2.0, tp_r=4.0),
    Config("don55_sl25_tp6", lookback=55, sl_atr=2.5, tp_r=6.0),
    Config("don100_sl25_tp4", lookback=100, sl_atr=2.5, tp_r=4.0),
    Config("don100_sl30_tp6", lookback=100, sl_atr=3.0, tp_r=6.0),
    Config("don20_sl20_trail20", lookback=20, sl_atr=2.0, tp_r=0.0, trail_atr=2.0),
    Config("don20_sl20_trail30", lookback=20, sl_atr=2.0, tp_r=0.0, trail_atr=3.0),
    Config("don40_sl20_trail30", lookback=40, sl_atr=2.0, tp_r=0.0, trail_atr=3.0),
    Config("don55_sl25_trail30", lookback=55, sl_atr=2.5, tp_r=0.0, trail_atr=3.0),
    Config("don40_sl30_trail40", lookback=40, sl_atr=3.0, tp_r=0.0, trail_atr=4.0),
    Config("don20_sl20_tp4_notrend", lookback=20, sl_atr=2.0, tp_r=4.0, trend_filter=False),
    Config("don20_sl20_tp4_reverse", lookback=20, sl_atr=2.0, tp_r=4.0, exit_opposite=True),
]


def split_trades(trades: pd.DataFrame) -> dict[str, pd.DataFrame]:
    t = trades.copy()
    t["entry_time"] = pd.to_datetime(t["entry_time"], utc=True)
    return {
        "design_2005_2014": t[(t["entry_time"] >= "2005-01-01") & (t["entry_time"] < "2015-01-01")],
        "validation_2015_2019": t[(t["entry_time"] >= "2015-01-01") & (t["entry_time"] < "2020-01-01")],
        "OOS_2020_2026": t[t["entry_time"] >= "2020-01-01"],
        "recent_2025_2026": t[t["entry_time"] >= "2025-01-01"],
    }


def main() -> int:
    summaries = []
    per_symbol_rows = []
    all_trades = []
    for cfg in CONFIGS:
        symbol_trades = []
        for symbol, point in SYMBOLS.items():
            path = DATA / f"{symbol}_D1.csv"
            if not path.exists():
                continue
            df = load_data(path)
            tr = simulate(df, cfg, point=point)
            if tr.empty:
                continue
            tr["symbol"] = symbol
            symbol_trades.append(tr)
            for split, part in split_trades(tr).items():
                per_symbol_rows.append({"config": cfg.name, "symbol": symbol, "split": split, **metrics(part)})
        if not symbol_trades:
            continue
        all_tr = pd.concat(symbol_trades, ignore_index=True).sort_values("entry_time")
        for split, part in split_trades(all_tr).items():
            summaries.append({"config": cfg.name, "split": split, **metrics(part)})
        all_trades.append(all_tr)

    summary = pd.DataFrame(summaries)
    OUT.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUT / "d1_trend_summary.csv", index=False)
    pd.DataFrame(per_symbol_rows).to_csv(OUT / "d1_trend_by_symbol.csv", index=False)
    if all_trades:
        pd.concat(all_trades, ignore_index=True).to_csv(OUT / "d1_trend_trades.csv", index=False)

    print("=== D1 trend portfolio ===")
    print(summary.to_string(index=False))
    piv = summary.pivot(index="config", columns="split", values="total")
    good = piv[(piv["design_2005_2014"] > 0) & (piv["validation_2015_2019"] > 0)]
    print("\n=== positive in design AND validation ===")
    print(good.round(2).to_string() if len(good) else "none")
    if len(good):
        print("\n=== OOS 2020-2026 and recent 2025-2026 for those configs ===")
        print(piv.loc[good.index, ["OOS_2020_2026", "recent_2025_2026"]].round(2).to_string())

    if all_trades:
        best = piv["OOS_2020_2026"].idxmax()
        print(f"\n=== per-symbol total R for best OOS config: {best} ===")
        ps = pd.DataFrame(per_symbol_rows)
        ps = ps[ps["config"] == best].pivot(index="symbol", columns="split", values="total")
        print(ps.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
